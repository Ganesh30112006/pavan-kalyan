"""AWS ECS Express Mode.

For AWS accounts that cannot use App Runner (closed to new customers since April 30, 2026). Express
Mode runs a container on Fargate behind a load balancer it creates and manages, from one API call.

Credentials, repository builds (CodeBuild into ECR), image resolution and CloudWatch reads are shared with
the App Runner adapter: the same connection role with an external ID, nothing stored.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from botocore.exceptions import ClientError

from app import clock
from app.config import Settings
from app.providers.aws import (
    BOTO_CONFIG,
    MANAGED_TAG,
    AwsAppRunnerAdapter,
    _error_code,
    cloudwatch_series,
    translate,
)
from app.providers.base import (
    AppTarget,
    ConnectionInfo,
    DeleteStatus,
    EffectiveConfig,
    MetricName,
    MetricSeries,
    ProviderError,
    ResolvedImage,
    RolloutStatus,
    RuntimeLogEntry,
    ServiceState,
    SpecInfo,
)
from app.providers.capabilities import DOCKER_HUB_HOSTS, ECR_PUBLIC_HOST, memory_mib
from app.providers.images import OciResolver, parse_image
from app.redaction import log_line, safe_message

logger = logging.getLogger(__name__)

APP_TAG_KEY = "cloudbridge:application-id"
CPU_UNITS = {"0.25": "256", "0.5": "512", "1": "1024", "2": "2048", "4": "4096"}
CHECKS = (
    "Assume role",
    "ECS permissions",
    "ECR access",
    "Account matches",
    "CodeBuild access (repository sources)",
)
SCALE_ON_CPU_PERCENT = 60
ECS_METRICS: tuple[tuple[str, str, MetricName, str], ...] = (
    ("CPUUtilization", "Average", "cpu_percent", "percent"),
    ("MemoryUtilization", "Average", "memory_percent", "percent"),
)
RUNNING = ("PENDING", "IN_PROGRESS", "STOP_REQUESTED", "ROLLBACK_REQUESTED", "ROLLBACK_IN_PROGRESS")


def _realm_allowed(host: str) -> bool:
    return host in (ECR_PUBLIC_HOST, "auth.docker.io")


def _cpu_from_units(value: str) -> str:
    for cpu, units in CPU_UNITS.items():
        if units == value:
            return cpu
    try:
        return f"{int(value) / 1024:g}"
    except ValueError:
        return value


def _memory_from_mib(value: str) -> str:
    try:
        mib = int(value)
    except ValueError:
        return value
    return f"{mib // 1024}Gi" if mib % 1024 == 0 else f"{mib}Mi"


class AwsEcsExpressAdapter(AwsAppRunnerAdapter):
    kind = "aws_ecs_express"
    label = "AWS ECS Express Mode"
    checks = CHECKS

    def __init__(self, settings: Settings, base_session: Any | None = None, oci: OciResolver | None = None) -> None:
        super().__init__(settings, base_session, oci or OciResolver(realm_allowed=_realm_allowed))

    def _ecs(self, conn: ConnectionInfo) -> Any:
        return self._client(conn, "ecs")

    def _cluster(self, conn: ConnectionInfo) -> str:
        return conn.ecs_cluster or "default"

    def _check_service_access(self, session: Any, conn: ConnectionInfo) -> None:
        client = session.client("ecs", region_name=conn.region, config=BOTO_CONFIG)
        if self._cluster_exists(client, conn):
            client.list_services(cluster=self._cluster(conn), maxResults=1)
        else:
            # Express Mode creates the default cluster with the first service.
            client.list_clusters(maxResults=1)

    def _cluster_exists(self, client: Any, conn: ConnectionInfo) -> bool:
        clusters = client.describe_clusters(clusters=[self._cluster(conn)]).get("clusters", [])
        return any(c.get("status") == "ACTIVE" for c in clusters)

    def check_access(self, conn: ConnectionInfo) -> None:
        try:
            self._ecs(conn).list_clusters(maxResults=1)
        except Exception as error:  # noqa: BLE001
            raise translate(error) from None

    # ----- images: ECR (private and public) and Docker Hub -----

    def resolve_image(self, conn: ConnectionInfo, image: str) -> ResolvedImage:
        ref = parse_image(image)
        if ref.host in DOCKER_HUB_HOSTS:
            path = ref.path if "/" in ref.path else f"library/{ref.path}"
            return self._oci.resolve(ref, "registry-1.docker.io", path)
        try:
            return super().resolve_image(conn, image)
        except ProviderError as error:
            if error.code == "unsupported_registry":
                raise ProviderError(
                    "unsupported_registry", "ECS Express Mode can run images from Amazon ECR, ECR Public or Docker Hub."
                ) from None
            raise

    # ----- rollout -----

    def _container(self, image: ResolvedImage, spec: SpecInfo) -> dict[str, Any]:
        container: dict[str, Any] = {
            "image": image.pinned,
            "containerPort": spec.port,
            "environment": [{"name": name, "value": value} for name, value in spec.environment],
        }
        if spec.secret_refs:
            container["secrets"] = [{"name": name, "valueFrom": ref} for name, ref in spec.secret_refs]
        return container

    def _config(self, conn: ConnectionInfo, image: ResolvedImage, spec: SpecInfo) -> dict[str, Any]:
        mib = memory_mib(spec.memory) or 1024
        return {
            "executionRoleArn": self._role_arn(conn, self._settings.aws_ecs_execution_role_name),
            "healthCheckPath": spec.health_check_path or "/",
            "primaryContainer": self._container(image, spec),
            "cpu": CPU_UNITS[spec.cpu],
            "memory": str(mib),
            "scalingTarget": {
                "minTaskCount": max(spec.min_instances, 1),
                "maxTaskCount": max(spec.max_instances, spec.min_instances, 1),
                "autoScalingMetric": "AVERAGE_CPU",
                "autoScalingTargetValue": SCALE_ON_CPU_PERCENT,
            },
        }

    def _find_ecs_service(self, client: Any, conn: ConnectionInfo, name: str, app_id: str) -> str | None:
        try:
            described = client.describe_services(cluster=self._cluster(conn), services=[name]).get("services", [])
        except ClientError as error:
            if _error_code(error) == "ClusterNotFoundException":
                return None
            raise
        for service in described:
            if service.get("status") != "ACTIVE":
                continue
            arn = str(service["serviceArn"])
            tags = client.list_tags_for_resource(resourceArn=arn).get("tags", [])
            if not any(t.get("key") == APP_TAG_KEY and t.get("value") == app_id for t in tags):
                raise ProviderError(
                    "service_name_taken",
                    f"An ECS service named {name} already exists in cluster {self._cluster(conn)} "
                    "and is not managed by this application.",
                )
            return arn
        return None

    def start_rollout(
        self, conn: ConnectionInfo, app: AppTarget, image: ResolvedImage, spec: SpecInfo
    ) -> dict[str, Any]:
        config = self._config(conn, image, spec)
        try:
            client = self._ecs(conn)
            arn = app.provider_service_id or self._find_ecs_service(client, conn, app.name, app.id)
            if arn is not None:
                current = client.describe_express_gateway_service(serviceArn=arn).get("service", {})
                if (current.get("status") or {}).get("statusCode") != "ACTIVE":
                    arn = None
            if arn is None:
                response = client.create_express_gateway_service(
                    serviceName=app.name,
                    cluster=self._cluster(conn),
                    infrastructureRoleArn=self._role_arn(conn, self._settings.aws_ecs_infrastructure_role_name),
                    tags=[
                        {"key": MANAGED_TAG["Key"], "value": MANAGED_TAG["Value"]},
                        {"key": APP_TAG_KEY, "value": app.id},
                    ],
                    **config,
                )
            else:
                response = client.update_express_gateway_service(serviceArn=arn, **config)
        except ProviderError:
            raise
        except ClientError as error:
            code = _error_code(error)
            if code in ("ServiceNotActiveException", "UpdateInProgressException"):
                raise ProviderError(
                    "provider_busy", "ECS is still applying a previous change. CloudBridge will retry.", retryable=True
                ) from None
            raise translate(error) from None
        except Exception as error:  # noqa: BLE001
            raise translate(error) from None
        service = response.get("service", {})
        return {
            "service_id": str(service.get("serviceArn") or arn),
            "deployment": service.get("currentDeployment"),
            "image": image.pinned,
        }

    def _deployment_status(self, client: Any, deployment_arn: str) -> tuple[str | None, str | None]:
        found = client.describe_service_deployments(serviceDeploymentArns=[deployment_arn])
        items = found.get("serviceDeployments", [])
        if not items:
            return None, None
        item = items[0]
        return str(item.get("status") or ""), str(item.get("statusReason") or "") or None

    def poll_rollout(self, conn: ConnectionInfo, handle: dict[str, Any]) -> RolloutStatus:
        arn = str(handle["service_id"])
        try:
            client = self._ecs(conn)
            service = client.describe_express_gateway_service(serviceArn=arn).get("service", {})
            deployment = handle.get("deployment") or service.get("currentDeployment")
            if not deployment:
                return RolloutStatus(state="running", service_id=arn)
            handle["deployment"] = deployment
            status, reason = self._deployment_status(client, str(deployment))
        except Exception as error:  # noqa: BLE001
            raise translate(error) from None
        if status in (None, "", *RUNNING):
            return RolloutStatus(state="running", service_id=arn)
        url = self._url(service)
        if status == "SUCCESSFUL":
            if not url:
                return RolloutStatus(state="running", service_id=arn)
            return RolloutStatus(
                state="succeeded", service_id=arn, service_url=url, effective=self._ecs_effective(service)
            )
        if status == "ROLLBACK_SUCCESSFUL":
            message = "ECS could not start the new version and kept the previous version running."
        else:
            message = safe_message(
                f"ECS could not deploy the new version: {reason}" if reason else "",
                "ECS could not deploy the new version. Check the image, port and health check.",
            )
        return RolloutStatus(state="failed", service_id=arn, service_url=url, message=message)

    @staticmethod
    def _active(service: dict[str, Any]) -> dict[str, Any]:
        configurations = service.get("activeConfigurations") or [{}]
        latest: dict[str, Any] = max(configurations, key=lambda c: str(c.get("createdAt") or ""))
        return latest

    def _url(self, service: dict[str, Any]) -> str | None:
        for path in self._active(service).get("ingressPaths") or []:
            if path.get("accessType") == "PUBLIC" and path.get("endpoint"):
                endpoint = str(path["endpoint"])
                return endpoint if endpoint.startswith("https://") else f"https://{endpoint}"
        return None

    def _ecs_effective(self, service: dict[str, Any]) -> EffectiveConfig:
        config = self._active(service)
        container = config.get("primaryContainer") or {}
        scaling = config.get("scalingTarget") or {}
        return EffectiveConfig(
            port=int(container.get("containerPort") or 80),
            cpu=_cpu_from_units(str(config.get("cpu", "1024"))),
            memory=_memory_from_mib(str(config.get("memory", "2048"))),
            min_instances=int(scaling.get("minTaskCount") or 1),
            max_instances=int(scaling.get("maxTaskCount") or 1),
            concurrency=None,
            exposure="public",
            health_check_path=config.get("healthCheckPath"),
        )

    def check_service(self, conn: ConnectionInfo, app: AppTarget) -> ServiceState:
        if not app.provider_service_id:
            return ServiceState(running=False, message="The service does not exist.")
        try:
            client = self._ecs(conn)
            service = client.describe_express_gateway_service(serviceArn=app.provider_service_id).get("service", {})
            code = (service.get("status") or {}).get("statusCode")
            if code != "ACTIVE":
                return ServiceState(running=False, message=f"ECS reports the service as {str(code).lower()}.")
            deployment = service.get("currentDeployment")
            status = self._deployment_status(client, str(deployment))[0] if deployment else "SUCCESSFUL"
        except ClientError as error:
            if _error_code(error) in ("ServiceNotFoundException", "ResourceNotFoundException"):
                return ServiceState(running=False, message="The ECS service no longer exists.")
            raise translate(error) from None
        except Exception as error:  # noqa: BLE001
            raise translate(error) from None
        if status in RUNNING:
            return ServiceState(running=None)
        return ServiceState(running=True)

    # ----- deletion -----

    def start_delete(self, conn: ConnectionInfo, app: AppTarget) -> dict[str, Any]:
        build = self._build_resources(app)
        if not app.provider_service_id:
            return {"done": True, "build_resources": build}
        try:
            self._ecs(conn).delete_express_gateway_service(serviceArn=app.provider_service_id)
        except ClientError as error:
            if _error_code(error) in ("ServiceNotFoundException", "ResourceNotFoundException"):
                return {"done": True, "build_resources": build}
            raise translate(error) from None
        except Exception as error:  # noqa: BLE001
            raise translate(error) from None
        return {"service_id": app.provider_service_id, "build_resources": build}

    def poll_delete(self, conn: ConnectionInfo, handle: dict[str, Any]) -> DeleteStatus:
        if handle.get("done"):
            return self._deleted(conn, handle)
        try:
            service = (
                self._ecs(conn).describe_express_gateway_service(serviceArn=handle["service_id"]).get("service", {})
            )
        except ClientError as error:
            if _error_code(error) in ("ServiceNotFoundException", "ResourceNotFoundException"):
                return self._deleted(conn, handle)
            raise translate(error) from None
        except Exception as error:  # noqa: BLE001
            raise translate(error) from None
        if (service.get("status") or {}).get("statusCode") in ("INACTIVE", None):
            return self._deleted(conn, handle)
        return DeleteStatus(state="running")

    # ----- logs and metrics -----

    def runtime_logs(self, conn: ConnectionInfo, app: AppTarget, limit: int) -> tuple[list[RuntimeLogEntry], bool]:
        if not app.provider_service_id:
            return [], False
        try:
            service = (
                self._ecs(conn).describe_express_gateway_service(serviceArn=app.provider_service_id).get("service", {})
            )
            logging_config = (self._active(service).get("primaryContainer") or {}).get("awsLogsConfiguration") or {}
            group = logging_config.get("logGroup")
            if not group:
                return [], False
            client = self._client(conn, "logs")
            start = int((clock.utcnow() - dt.timedelta(hours=1)).timestamp() * 1000)
            events: list[Any] = []
            kwargs: dict[str, Any] = {"logGroupName": group, "startTime": start, "limit": 500}
            for _ in range(4):
                page = client.filter_log_events(**kwargs)
                events.extend(page.get("events", []))
                token = page.get("nextToken")
                if not token:
                    break
                kwargs["nextToken"] = token
        except ClientError as error:
            if _error_code(error) == "ResourceNotFoundException":
                return [], False
            raise translate(error) from None
        except Exception as error:  # noqa: BLE001
            raise translate(error) from None
        truncated = len(events) > limit
        return [
            RuntimeLogEntry(
                timestamp=dt.datetime.fromtimestamp(int(e["timestamp"]) / 1000, dt.UTC),
                stream="stdout",
                message=log_line(str(e.get("message", ""))),
            )
            for e in events[-limit:]
            if "timestamp" in e
        ], truncated

    def metrics(self, conn: ConnectionInfo, app: AppTarget, minutes: int, step_seconds: int) -> list[MetricSeries]:
        if not app.provider_service_id:
            return []
        dimensions = [
            {"Name": "ClusterName", "Value": self._cluster(conn)},
            {"Name": "ServiceName", "Value": app.provider_service_id.rsplit("/", 1)[-1]},
        ]
        return cloudwatch_series(
            self._client(conn, "cloudwatch"), "AWS/ECS", dimensions, ECS_METRICS, minutes, step_seconds
        )

    # ----- pause and resume -----

    def set_paused(self, conn: ConnectionInfo, app: AppTarget, paused: bool) -> None:
        if not app.provider_service_id:
            raise ProviderError("not_deployed", "The application is not deployed.")
        count = 0 if paused else max(app.min_instances, 1)
        try:
            client = self._ecs(conn)
            service = client.describe_express_gateway_service(serviceArn=app.provider_service_id).get("service", {})
            scaling = dict(self._active(service).get("scalingTarget") or {})
            scaling["minTaskCount"] = count
            if not paused:
                scaling["maxTaskCount"] = max(int(scaling.get("maxTaskCount") or count), count)
            client.update_express_gateway_service(serviceArn=app.provider_service_id, scalingTarget=scaling)
            client.update_service(cluster=self._cluster(conn), service=app.provider_service_id, desiredCount=count)
        except ClientError as error:
            raise translate(error) from None
        except Exception as error:  # noqa: BLE001
            raise translate(error) from None

    # ----- not offered by Express Mode through CloudBridge -----

    def add_domain(self, conn: ConnectionInfo, app: AppTarget, domain: str) -> Any:
        raise self._unsupported("custom domains")

    def domain_status(self, conn: ConnectionInfo, app: AppTarget, domain: str) -> Any:
        raise self._unsupported("custom domains")

    def remove_domain(self, conn: ConnectionInfo, app: AppTarget, domain: str) -> None:
        raise self._unsupported("custom domains")

    def service_host_allowed(self, host: str) -> bool:
        return host.endswith((".on.aws", ".elb.amazonaws.com"))
