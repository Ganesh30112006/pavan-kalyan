"""AWS App Runner.

CloudBridge's own identity comes from the standard AWS credential chain. Customer accounts are
reached only through sts:AssumeRole on the connection's role, with the connection's unique
external ID. No customer access keys are accepted or stored.

Repository sources are built by AWS CodeBuild in the customer's account (so untrusted
Dockerfiles never run on CloudBridge infrastructure) and pushed to Amazon ECR. Services are
created and updated through the App Runner API with the image pinned by digest.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
from typing import TYPE_CHECKING, Any, cast

import boto3
from botocore.config import Config
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectTimeoutError,
    CredentialRetrievalError,
    EndpointConnectionError,
    MissingDependencyException,
    NoCredentialsError,
    PartialCredentialsError,
    ReadTimeoutError,
)

from app import clock
from app.config import Settings
from app.providers.base import (
    AppTarget,
    BuildStatus,
    CheckResult,
    ConnectionInfo,
    ConnectionValidationResult,
    DeleteStatus,
    DnsRecord,
    DomainState,
    EffectiveConfig,
    ExtrasNotSupported,
    MetricName,
    MetricPoint,
    MetricSeries,
    PrepareResult,
    ProgressCallback,
    ProviderError,
    ResolvedImage,
    RolloutStatus,
    RuntimeLogEntry,
    ServiceState,
    SourceInfo,
    SpecInfo,
)
from app.providers.capabilities import APP_RUNNER_DEFAULT_CONCURRENCY, ECR_PRIVATE_HOST, ECR_PUBLIC_HOST, memory_mib
from app.providers.images import OciResolver, is_digest, parse_image
from app.redaction import log_line, safe_message

if TYPE_CHECKING:
    from mypy_boto3_apprunner import AppRunnerClient
    from mypy_boto3_codebuild import CodeBuildClient
    from mypy_boto3_ecr import ECRClient

logger = logging.getLogger(__name__)

BOTO_CONFIG = Config(connect_timeout=5, read_timeout=30, retries={"max_attempts": 3, "mode": "standard"})
MANAGED_TAG: Any = {"Key": "managed-by", "Value": "cloudbridge"}
APP_TAG_KEY = "cloudbridge:application-id"
CPU_UNITS = {"0.25": "256", "0.5": "512", "1": "1024", "2": "2048", "4": "4096"}
CHECKS = (
    "Assume role",
    "App Runner permissions",
    "ECR access",
    "Account matches",
    "CodeBuild access (repository sources)",
)

ACCESS_DENIED = {"AccessDenied", "AccessDeniedException", "UnauthorizedOperation", "UnauthorizedException"}
BAD_TOKEN = {"ExpiredToken", "ExpiredTokenException", "InvalidClientTokenId", "UnrecognizedClientException"}
THROTTLED = {
    "Throttling",
    "ThrottlingException",
    "TooManyRequestsException",
    "RequestLimitExceeded",
    "ServiceUnavailable",
    "ServiceUnavailableException",
    "InternalFailure",
    "InternalServiceErrorException",
    "InternalServerException",
}

# Runs inside CodeBuild in the customer's account. User input arrives only as environment
# variables and is always quoted, never interpolated into the commands.
BUILDSPEC = """version: 0.2
env:
  shell: bash
  exported-variables:
    - IMAGE_DIGEST
    - GIT_COMMIT
phases:
  pre_build:
    on-failure: ABORT
    commands:
      - git init src
      - git -C src remote add origin "$REPO_URL"
      - if [ "$REF_TYPE" = "commit" ]; then git -C src fetch origin && git -C src checkout --detach "$GIT_REF"; else git -C src fetch --depth 1 origin "$GIT_REF" && git -C src checkout --detach FETCH_HEAD; fi
      - GIT_COMMIT="$(git -C src rev-parse HEAD)"
      - aws ecr get-login-password --region "$AWS_DEFAULT_REGION" | docker login --username AWS --password-stdin "$REGISTRY"
  build:
    on-failure: ABORT
    commands:
      - docker build --file "src/$DOCKERFILE_PATH" --tag "$IMAGE_URI" "src/$BUILD_CONTEXT"
      - docker push "$IMAGE_URI"
      - IMAGE_DIGEST="$(docker inspect --format '{{index .RepoDigests 0}}' "$IMAGE_URI" | cut -d@ -f2)"
"""


# Errors raised while loading CloudBridge's own AWS credentials (expired "aws login" or SSO sessions,
# missing profiles). Matched by name as well, since botocore adds new credential providers over time.
_CREDENTIAL_ERROR_NAMES = ("Login", "SSOToken", "TokenRetrieval", "ProfileNotFound", "RefreshWithMFA", "InvalidConfig")


def _is_platform_credential_error(error: Exception) -> bool:
    if isinstance(
        error, NoCredentialsError | PartialCredentialsError | CredentialRetrievalError | MissingDependencyException
    ):
        return True
    return isinstance(error, BotoCoreError) and any(
        marker in cls.__name__ for cls in type(error).__mro__ for marker in _CREDENTIAL_ERROR_NAMES
    )


def translate(error: Exception, *, assuming_role: bool = False) -> ProviderError:
    """Maps boto errors to user-safe provider errors."""
    if isinstance(error, ProviderError):
        return error
    if _is_platform_credential_error(error):
        return ProviderError(
            "platform_not_configured",
            "CloudBridge's own AWS credentials are missing or expired, so it cannot assume your role. "
            "Ask your CloudBridge administrator to configure or renew the platform's AWS credentials.",
            connection_status="error",
        )
    if isinstance(error, ConnectTimeoutError | ReadTimeoutError):
        return ProviderError("timed_out", "AWS did not respond in time.", retryable=True)
    if isinstance(error, EndpointConnectionError):
        return ProviderError("provider_unavailable", "AWS could not be reached.", retryable=True)
    if isinstance(error, ClientError):
        code = str(error.response.get("Error", {}).get("Code", ""))
        raw = str(error.response.get("Error", {}).get("Message", ""))
        if assuming_role and (code in ACCESS_DENIED or code in BAD_TOKEN):
            return ProviderError(
                "invalid_credentials",
                "CloudBridge could not assume the role. Check the role ARN, its trust policy and the external ID.",
            )
        if code in BAD_TOKEN:
            return ProviderError("invalid_credentials", "AWS rejected the temporary credentials.", retryable=True)
        # App Runner reports some permission failures (e.g. creating its service-linked role) as
        # InvalidRequestException with an "AccessDenied" message.
        if code in ACCESS_DENIED or raw.startswith(("AccessDenied", "Access denied", "Access Denied")):
            return ProviderError(
                "insufficient_permissions",
                safe_message(raw, "The role does not allow this action."),
            )
        # A service-linked role created moments ago (e.g. on the first ECS Express deployment) takes a
        # minute to propagate; AWS documents retrying.
        if "unable to assume the service linked role" in raw.lower():
            return ProviderError(
                "provider_unavailable",
                "AWS is still setting up its service-linked role. CloudBridge will retry.",
                retryable=True,
            )
        if code in THROTTLED:
            return ProviderError(
                "provider_unavailable", "AWS is busy or unavailable. CloudBridge will retry.", retryable=True
            )
        if code in ("ServiceQuotaExceededException", "LimitExceededException"):
            return ProviderError("quota_exceeded", safe_message(raw, "An AWS service quota was reached."))
        if code == "ResourceNotFoundException":
            return ProviderError("not_found", safe_message(raw, "The AWS resource was not found."))
        return ProviderError("invalid_request", safe_message(raw, "AWS rejected the request."))
    if isinstance(error, BotoCoreError):
        return ProviderError("provider_unavailable", "AWS could not be reached.", retryable=True)
    raise error


def _error_code(error: ClientError) -> str:
    return str(error.response.get("Error", {}).get("Code", ""))


def _partition(conn: ConnectionInfo) -> str:
    parts = (conn.role_arn or "").split(":")
    return parts[1] if len(parts) > 1 and parts[1] else "aws"


def service_name(app_name: str) -> str:
    """App Runner service names need at least 4 characters; CloudBridge names can have 3."""
    return app_name if len(app_name) >= 4 else f"{app_name}-app"


def _cpu_from_units(value: str) -> str:
    text = value.strip().lower().replace("vcpu", "").strip()
    number = float(text)
    vcpu = number / 1024 if number >= 64 else number
    return f"{vcpu:g}"


def _memory_from_units(value: str) -> str:
    text = value.strip().lower()
    mib = int(float(text[:-2].strip()) * 1024) if text.endswith("gb") else int(float(text))
    return f"{mib // 1024}Gi" if mib % 1024 == 0 else f"{mib}Mi"


class AwsAppRunnerAdapter(ExtrasNotSupported):
    kind = "aws_app_runner"
    label = "AWS App Runner"

    def __init__(
        self,
        settings: Settings,
        base_session: Any | None = None,
        oci: OciResolver | None = None,
    ) -> None:
        self._settings = settings
        self._base_session = base_session
        self._oci = oci or OciResolver(realm_allowed=lambda host: host == ECR_PUBLIC_HOST)
        self._lock = threading.Lock()
        self._sessions: dict[str, tuple[dt.datetime, Any]] = {}

    # ----- credentials -----

    def _base(self) -> Any:
        return self._base_session if self._base_session is not None else boto3.Session()

    def _session(self, conn: ConnectionInfo) -> Any:
        if not conn.role_arn or not conn.external_id:
            raise ProviderError("invalid_credentials", "The connection has no role ARN or external ID.")
        key = f"{conn.id}|{conn.role_arn}|{conn.external_id}|{conn.region}"
        now = clock.utcnow()
        with self._lock:
            cached = self._sessions.get(key)
            if cached and cached[0] - now > dt.timedelta(minutes=5):
                return cached[1]
        try:
            sts = self._base().client("sts", region_name=self._settings.aws_sts_region, config=BOTO_CONFIG)
            response = sts.assume_role(
                RoleArn=conn.role_arn,
                RoleSessionName=f"cloudbridge-{conn.id[:8]}",
                ExternalId=conn.external_id,
                DurationSeconds=3600,
            )
        except Exception as error:  # noqa: BLE001 - translated below
            raise translate(error, assuming_role=True) from None
        creds = response["Credentials"]
        session = boto3.Session(
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
            region_name=conn.region,
        )
        expires = clock.as_utc(creds["Expiration"])
        with self._lock:
            self._sessions[key] = (expires, session)
        return session

    def _client(self, conn: ConnectionInfo, service: str, region: str | None = None) -> Any:
        return self._session(conn).client(service, region_name=region or conn.region, config=BOTO_CONFIG)

    def _apprunner(self, conn: ConnectionInfo, region: str | None = None) -> AppRunnerClient:
        client: AppRunnerClient = self._client(conn, "apprunner", region)
        return client

    def _ecr(self, conn: ConnectionInfo, region: str | None = None) -> ECRClient:
        client: ECRClient = self._client(conn, "ecr", region)
        return client

    def _codebuild(self, conn: ConnectionInfo, region: str | None = None) -> CodeBuildClient:
        client: CodeBuildClient = self._client(conn, "codebuild", region)
        return client

    def _role_arn(self, conn: ConnectionInfo, name: str) -> str:
        return f"arn:{_partition(conn)}:iam::{conn.account_id}:role/{name}"

    # ----- connection validation -----

    checks = CHECKS

    def _check_service_access(self, session: Any, conn: ConnectionInfo) -> None:
        session.client("apprunner", region_name=conn.region, config=BOTO_CONFIG).list_services(MaxResults=1)

    def validate_connection(self, conn: ConnectionInfo, progress: ProgressCallback) -> ConnectionValidationResult:
        names = self.checks
        checks = [CheckResult(name, "pending") for name in names]

        def update(index: int, status: Any, detail: str | None = None) -> None:
            checks[index] = CheckResult(names[index], status, detail)

        def finish(error: ProviderError, failed_index: int) -> ConnectionValidationResult:
            update(failed_index, "failed", error.message)
            for i, check in enumerate(checks):
                if check.status == "pending":
                    update(i, "skipped")
            return ConnectionValidationResult(error.connection_status, error.code, list(checks))

        progress("connecting", list(checks))
        try:
            session = self._session(conn)
        except ProviderError as error:
            return finish(error, 0)
        update(0, "passed")

        progress("validating_permissions", list(checks))
        try:
            self._check_service_access(session, conn)
        except Exception as error:  # noqa: BLE001
            return finish(translate(error), 1)
        update(1, "passed")
        try:
            session.client("ecr", region_name=conn.region, config=BOTO_CONFIG).describe_repositories(maxResults=1)
        except Exception as error:  # noqa: BLE001
            return finish(translate(error), 2)
        update(2, "passed")

        progress("checking_readiness", list(checks))
        try:
            identity = session.client("sts", region_name=conn.region, config=BOTO_CONFIG).get_caller_identity()
        except Exception as error:  # noqa: BLE001
            return finish(translate(error), 3)
        if identity.get("Account") != conn.account_id:
            mismatch = ProviderError(
                "account_mismatch",
                f"The role belongs to account {identity.get('Account')}, not {conn.account_id}.",
                connection_status="invalid_credentials",
            )
            return finish(mismatch, 3)
        update(3, "passed")
        try:
            session.client("codebuild", region_name=conn.region, config=BOTO_CONFIG).list_projects()
            update(4, "passed")
        except Exception as error:  # noqa: BLE001
            translated = translate(error)
            if translated.code != "insufficient_permissions":
                return finish(translated, 4)
            # Only repository sources need CodeBuild; image deployments still work.
            update(4, "skipped", "Not granted: building from a repository will fail. Image sources are unaffected.")
        progress("checking_readiness", list(checks))
        return ConnectionValidationResult("connected", None, list(checks))

    def check_access(self, conn: ConnectionInfo) -> None:
        try:
            self._apprunner(conn).list_services(MaxResults=1)
        except Exception as error:  # noqa: BLE001
            raise translate(error) from None

    # ----- build -----

    def _repository_name(self, app: AppTarget) -> str:
        return f"{self._settings.aws_ecr_repository_prefix}/{app.name}"

    def _project_name(self, app: AppTarget) -> str:
        return f"cloudbridge-{app.name}"

    def prepare(self, conn: ConnectionInfo, app: AppTarget, needs_build: bool) -> PrepareResult:
        self.check_access(conn)
        if not needs_build:
            return PrepareResult(ready=True, messages=["Provider access confirmed."])
        messages = ["Provider access confirmed."]
        try:
            ecr = self._ecr(conn)
            try:
                ecr.create_repository(
                    repositoryName=self._repository_name(app),
                    imageTagMutability="IMMUTABLE",
                    imageScanningConfiguration={"scanOnPush": True},
                    tags=[MANAGED_TAG, cast(Any, {"Key": APP_TAG_KEY, "Value": app.id})],
                )
                messages.append(f"Created ECR repository {self._repository_name(app)}.")
            except ClientError as error:
                if _error_code(error) != "RepositoryAlreadyExistsException":
                    raise
            self._ensure_project(conn, app)
        except Exception as error:  # noqa: BLE001
            raise translate(error) from None
        return PrepareResult(ready=True, messages=messages)

    def _ensure_project(self, conn: ConnectionInfo, app: AppTarget) -> None:
        codebuild = self._codebuild(conn)
        params: dict[str, Any] = {
            "name": self._project_name(app),
            "description": f"CloudBridge image builds for {app.name}",
            "source": {"type": "NO_SOURCE", "buildspec": BUILDSPEC},
            "artifacts": {"type": "NO_ARTIFACTS"},
            "environment": {
                "type": "LINUX_CONTAINER",
                "image": "aws/codebuild/standard:7.0",
                "computeType": "BUILD_GENERAL1_MEDIUM",
                "privilegedMode": True,
            },
            "serviceRole": self._role_arn(conn, self._settings.aws_codebuild_role_name),
            "timeoutInMinutes": self._settings.build_timeout_minutes,
            "logsConfig": {"cloudWatchLogs": {"status": "ENABLED"}},
        }
        try:
            codebuild.create_project(
                **params, tags=[{"key": "managed-by", "value": "cloudbridge"}, {"key": APP_TAG_KEY, "value": app.id}]
            )
        except ClientError as error:
            if _error_code(error) != "ResourceAlreadyExistsException":
                raise
            codebuild.update_project(**params)

    def start_build(
        self, conn: ConnectionInfo, app: AppTarget, source: SourceInfo, tag: str, token: str
    ) -> dict[str, Any]:
        registry = f"{conn.account_id}.dkr.ecr.{conn.region}.amazonaws.com"
        image_uri = f"{registry}/{self._repository_name(app)}:{tag}"
        env = {
            "REPO_URL": source.repository_url or "",
            "GIT_REF": source.git_ref or "",
            "REF_TYPE": source.ref_type or "branch",
            "DOCKERFILE_PATH": source.dockerfile_path or "Dockerfile",
            "BUILD_CONTEXT": source.build_context or ".",
            "IMAGE_URI": image_uri,
            "REGISTRY": registry,
        }
        try:
            response = self._codebuild(conn).start_build(
                projectName=self._project_name(app),
                environmentVariablesOverride=[{"name": k, "value": v, "type": "PLAINTEXT"} for k, v in env.items()],
                idempotencyToken=token[:64],
            )
        except Exception as error:  # noqa: BLE001
            raise translate(error) from None
        return {"build_id": response["build"]["id"], "image_uri": image_uri}

    def poll_build(self, conn: ConnectionInfo, handle: dict[str, Any]) -> BuildStatus:
        try:
            builds = self._codebuild(conn).batch_get_builds(ids=[handle["build_id"]]).get("builds", [])
        except Exception as error:  # noqa: BLE001
            raise translate(error) from None
        if not builds:
            return BuildStatus(state="failed", message="The build could not be found in AWS CodeBuild.")
        build: Any = builds[0]
        status = build.get("buildStatus")
        if status == "IN_PROGRESS":
            return BuildStatus(state="running")
        lines = self._build_logs(conn, build)
        if status == "SUCCEEDED":
            exported = {v.get("name"): v.get("value") for v in build.get("exportedEnvironmentVariables", [])}
            digest = exported.get("IMAGE_DIGEST")
            if not is_digest(digest):
                return BuildStatus(
                    state="failed", message="The build finished but did not report an image digest.", log_lines=lines
                )
            commit = exported.get("GIT_COMMIT")
            return BuildStatus(
                state="succeeded",
                image_uri=str(handle["image_uri"]).rsplit(":", 1)[0],
                image_digest=str(digest),
                commit=str(commit) if commit else None,
                log_lines=lines,
            )
        if status == "STOPPED":
            return BuildStatus(state="cancelled", message="The build was stopped.", log_lines=lines)
        message = "The image build failed. See the build logs for details."
        for phase in build.get("phases", []):
            if phase.get("phaseStatus") in ("FAILED", "FAULT", "TIMED_OUT"):
                contexts = phase.get("contexts") or [{}]
                detail = str(contexts[0].get("message") or "")
                message = safe_message(f"Build phase {phase.get('phaseType')} failed: {detail}".rstrip(": "), message)
                break
        if status == "TIMED_OUT":
            message = "The image build exceeded its time limit."
        return BuildStatus(state="failed", message=message, log_lines=lines)

    def _build_logs(self, conn: ConnectionInfo, build: Any) -> list[tuple[dt.datetime, str]]:
        info = build.get("logs") or {}
        group, stream = info.get("groupName"), info.get("streamName")
        if not group or not stream:
            return []
        try:
            events = (
                self._client(conn, "logs")
                .get_log_events(logGroupName=group, logStreamName=stream, limit=300, startFromHead=False)
                .get("events", [])
            )
        except Exception:  # noqa: BLE001 - logs are best effort; the build result is what matters
            logger.info("Could not read CodeBuild logs", extra={"connection_id": conn.id})
            return []
        return [
            (dt.datetime.fromtimestamp(e["timestamp"] / 1000, dt.UTC), log_line(str(e.get("message", ""))))
            for e in events
            if "timestamp" in e
        ]

    def cancel_build(self, conn: ConnectionInfo, handle: dict[str, Any]) -> None:
        try:
            self._codebuild(conn).stop_build(id=handle["build_id"])
        except Exception as error:  # noqa: BLE001
            raise translate(error) from None

    # ----- images -----

    def resolve_image(self, conn: ConnectionInfo, image: str) -> ResolvedImage:
        ref = parse_image(image)
        private = ECR_PRIVATE_HOST.match(ref.host)
        if private:
            registry_id, region = private.group(1), private.group(2)
            image_id: dict[str, str] = {"imageDigest": ref.digest} if ref.digest else {"imageTag": ref.tag or "latest"}
            try:
                details = self._ecr(conn, region).describe_images(
                    registryId=registry_id,
                    repositoryName=ref.path,
                    imageIds=[image_id],  # type: ignore[list-item]
                )["imageDetails"]
            except ClientError as error:
                if _error_code(error) in ("ImageNotFoundException", "RepositoryNotFoundException"):
                    raise ProviderError("image_not_found", "The image or tag was not found in Amazon ECR.") from None
                raise translate(error) from None
            except Exception as error:  # noqa: BLE001
                raise translate(error) from None
            if not details or not is_digest(details[0].get("imageDigest")):
                raise ProviderError("image_not_found", "The image or tag was not found in Amazon ECR.")
            return ResolvedImage(repository=ref.repository, digest=str(details[0]["imageDigest"]))
        if ref.host == ECR_PUBLIC_HOST:
            return self._oci.resolve(ref, ECR_PUBLIC_HOST, ref.path)
        raise ProviderError("unsupported_registry", "AWS App Runner can only run images from Amazon ECR or ECR Public.")

    # ----- rollout -----

    def _autoscaling_arn(self, client: AppRunnerClient, spec: SpecInfo) -> str:
        min_size = max(spec.min_instances, 1)
        max_size = max(spec.max_instances, min_size)
        concurrency = spec.concurrency or APP_RUNNER_DEFAULT_CONCURRENCY
        name = f"cloudbridge-{min_size}-{max_size}-{concurrency}"
        existing = client.list_auto_scaling_configurations(AutoScalingConfigurationName=name, LatestOnly=True)
        for summary in existing.get("AutoScalingConfigurationSummaryList", []):
            # The live API returns "active" although the service model documents "ACTIVE".
            if str(summary.get("Status", "active")).lower() == "active" and summary.get("AutoScalingConfigurationArn"):
                return str(summary["AutoScalingConfigurationArn"])

        def create() -> str:
            created = client.create_auto_scaling_configuration(
                AutoScalingConfigurationName=name,
                MinSize=min_size,
                MaxSize=max_size,
                MaxConcurrency=concurrency,
                Tags=[MANAGED_TAG],
            )
            return str(created["AutoScalingConfiguration"]["AutoScalingConfigurationArn"])

        try:
            return create()
        except ClientError as error:
            # App Runner allows 10 configurations per region. Free the ones CloudBridge created that no
            # service uses any more (they are recreated on demand), then try once more.
            if _error_code(error) != "ServiceQuotaExceededException" or not self._prune_autoscaling(client):
                raise
        return create()

    def _prune_autoscaling(self, client: AppRunnerClient) -> int:
        """Deletes CloudBridge's auto scaling configurations that no service uses. Returns how many."""
        unused: list[str] = []
        kwargs: dict[str, Any] = {"MaxResults": 100}
        for _ in range(20):
            page = client.list_auto_scaling_configurations(**kwargs)
            for summary in page.get("AutoScalingConfigurationSummaryList", []):
                if (
                    str(summary.get("AutoScalingConfigurationName", "")).startswith("cloudbridge-")
                    and str(summary.get("Status", "active")).lower() == "active"
                    and summary.get("HasAssociatedService") is False
                    and not summary.get("IsDefault")
                    and summary.get("AutoScalingConfigurationArn")
                ):
                    unused.append(str(summary["AutoScalingConfigurationArn"]))
            token = page.get("NextToken")
            if not token:
                break
            kwargs["NextToken"] = token
        deleted = 0
        for arn in unused:
            try:
                client.delete_auto_scaling_configuration(AutoScalingConfigurationArn=arn)
                deleted += 1
            except ClientError as error:
                logger.info("Could not delete auto scaling configuration", extra={"code": _error_code(error)})
        return deleted

    def _find_service(self, client: AppRunnerClient, name: str, app_id: str) -> str | None:
        # list_services has no botocore paginator: follow NextToken by hand.
        kwargs: dict[str, Any] = {"MaxResults": 20}
        for _ in range(100):
            page = client.list_services(**kwargs)
            for summary in page.get("ServiceSummaryList", []):
                if summary.get("ServiceName") == name:
                    arn = str(summary["ServiceArn"])
                    tags = client.list_tags_for_resource(ResourceArn=arn).get("Tags", [])
                    if not any(t.get("Key") == APP_TAG_KEY and t.get("Value") == app_id for t in tags):
                        raise ProviderError(
                            "service_name_taken",
                            f"An App Runner service named {name} already exists in this account and region "
                            "and is not managed by this application.",
                        )
                    return arn
            token = page.get("NextToken")
            if not token:
                break
            kwargs["NextToken"] = token
        return None

    def _usable_service(self, client: AppRunnerClient, service_arn: str) -> str | None:
        """The service to update, or None when a new one must be created. A service whose creation failed
        cannot be updated: it is deleted first, and the rollout is retried once the deletion finishes."""
        try:
            status = client.describe_service(ServiceArn=service_arn)["Service"].get("Status")
        except ClientError as error:
            if _error_code(error) == "ResourceNotFoundException":
                return None
            raise
        if status == "DELETED":
            return None
        if status == "CREATE_FAILED":
            client.delete_service(ServiceArn=service_arn)
            raise ProviderError(
                "provider_busy",
                "Removing the App Runner service that failed to create, so it can be created again. "
                "CloudBridge will retry.",
                retryable=True,
            )
        return service_arn

    def start_rollout(
        self, conn: ConnectionInfo, app: AppTarget, image: ResolvedImage, spec: SpecInfo
    ) -> dict[str, Any]:
        public_image = image.repository.split("/", 1)[0] == ECR_PUBLIC_HOST
        image_config: Any = {
            "Port": str(spec.port),
            "RuntimeEnvironmentVariables": dict(spec.environment),
        }
        if spec.secret_refs:
            image_config["RuntimeEnvironmentSecrets"] = dict(spec.secret_refs)
        source: Any = {
            "ImageRepository": {
                "ImageIdentifier": image.pinned,
                "ImageRepositoryType": "ECR_PUBLIC" if public_image else "ECR",
                "ImageConfiguration": image_config,
            },
            "AutoDeploymentsEnabled": False,
        }
        if not public_image:
            source["AuthenticationConfiguration"] = {
                "AccessRoleArn": self._role_arn(conn, self._settings.aws_apprunner_access_role_name)
            }
        mib = memory_mib(spec.memory)
        instance: Any = {"Cpu": CPU_UNITS[spec.cpu], "Memory": str(mib)}
        if spec.secret_refs:
            instance["InstanceRoleArn"] = self._role_arn(conn, self._settings.aws_apprunner_instance_role_name)
        health: Any = (
            {"Protocol": "HTTP", "Path": spec.health_check_path} if spec.health_check_path else {"Protocol": "TCP"}
        )
        network: Any = {
            "IngressConfiguration": {"IsPubliclyAccessible": spec.exposure == "public"},
            "EgressConfiguration": {"EgressType": "DEFAULT"},
        }
        try:
            client = self._apprunner(conn)
            scaling_arn = self._autoscaling_arn(client, spec)
            name = service_name(app.name)
            service_arn = app.provider_service_id or self._find_service(client, name, app.id)
            if service_arn is not None:
                service_arn = self._usable_service(client, service_arn)
            if service_arn is None:
                created = client.create_service(
                    ServiceName=name,
                    SourceConfiguration=source,
                    InstanceConfiguration=instance,
                    AutoScalingConfigurationArn=scaling_arn,
                    HealthCheckConfiguration=health,
                    NetworkConfiguration=network,
                    Tags=[MANAGED_TAG, cast(Any, {"Key": APP_TAG_KEY, "Value": app.id})],
                )
                return {"service_id": created["Service"]["ServiceArn"], "operation_id": created["OperationId"]}
            updated = client.update_service(
                ServiceArn=service_arn,
                SourceConfiguration=source,
                InstanceConfiguration=instance,
                AutoScalingConfigurationArn=scaling_arn,
                HealthCheckConfiguration=health,
                NetworkConfiguration=network,
            )
            return {"service_id": service_arn, "operation_id": updated["OperationId"]}
        except ClientError as error:
            if _error_code(error) == "InvalidStateException":
                raise ProviderError(
                    "provider_busy",
                    "App Runner is still applying a previous change. CloudBridge will retry.",
                    retryable=True,
                ) from None
            raise translate(error) from None
        except ProviderError:
            raise
        except Exception as error:  # noqa: BLE001
            raise translate(error) from None

    def poll_rollout(self, conn: ConnectionInfo, handle: dict[str, Any]) -> RolloutStatus:
        service_arn = str(handle["service_id"])
        try:
            client = self._apprunner(conn)
            operations = client.list_operations(ServiceArn=service_arn, MaxResults=20).get("OperationSummaryList", [])
            operation = next((o for o in operations if o.get("Id") == handle["operation_id"]), None)
            status = operation.get("Status") if operation else None
            if status in (None, "PENDING", "IN_PROGRESS", "ROLLBACK_IN_PROGRESS"):
                return RolloutStatus(state="running", service_id=service_arn)
            service: Any = client.describe_service(ServiceArn=service_arn)["Service"]
            url = f"https://{service['ServiceUrl']}" if service.get("ServiceUrl") else None
            if status == "SUCCEEDED":
                return RolloutStatus(
                    state="succeeded",
                    service_id=service_arn,
                    service_url=url,
                    effective=self._effective(client, service),
                )
        except Exception as error:  # noqa: BLE001
            raise translate(error) from None
        if status == "ROLLBACK_SUCCEEDED":
            message = "App Runner could not start the new version and kept the previous version running."
        elif service.get("Status") == "CREATE_FAILED":
            message = "App Runner could not create the service. Check the image, port and health check."
        else:
            message = "App Runner could not deploy the new version. Check the image, port and health check."
        return RolloutStatus(state="failed", service_id=service_arn, service_url=url, message=message)

    def _effective(self, client: AppRunnerClient, service: Any) -> EffectiveConfig:
        instance = service.get("InstanceConfiguration", {})
        image = service.get("SourceConfiguration", {}).get("ImageRepository", {})
        health = service.get("HealthCheckConfiguration", {})
        ingress = service.get("NetworkConfiguration", {}).get("IngressConfiguration", {})
        scaling_arn = service.get("AutoScalingConfigurationSummary", {}).get("AutoScalingConfigurationArn")
        min_size, max_size, concurrency = 1, 25, APP_RUNNER_DEFAULT_CONCURRENCY
        if scaling_arn:
            scaling = client.describe_auto_scaling_configuration(AutoScalingConfigurationArn=scaling_arn)[
                "AutoScalingConfiguration"
            ]
            min_size = int(scaling.get("MinSize", min_size))
            max_size = int(scaling.get("MaxSize", max_size))
            concurrency = int(scaling.get("MaxConcurrency", concurrency))
        return EffectiveConfig(
            port=int(image.get("ImageConfiguration", {}).get("Port", "8080")),
            cpu=_cpu_from_units(str(instance.get("Cpu", "1024"))),
            memory=_memory_from_units(str(instance.get("Memory", "2048"))),
            min_instances=min_size,
            max_instances=max_size,
            concurrency=concurrency,
            exposure="public" if ingress.get("IsPubliclyAccessible", True) else "private",
            health_check_path=health.get("Path") if health.get("Protocol") == "HTTP" else None,
        )

    def check_service(self, conn: ConnectionInfo, app: AppTarget) -> ServiceState:
        if not app.provider_service_id:
            return ServiceState(running=False, message="The service does not exist.")
        try:
            service = self._apprunner(conn).describe_service(ServiceArn=app.provider_service_id)["Service"]
        except ClientError as error:
            if _error_code(error) == "ResourceNotFoundException":
                return ServiceState(running=False, message="The App Runner service no longer exists.")
            raise translate(error) from None
        except Exception as error:  # noqa: BLE001
            raise translate(error) from None
        status = service.get("Status")
        if status == "RUNNING":
            return ServiceState(running=True)
        if status == "OPERATION_IN_PROGRESS":
            return ServiceState(running=None)
        return ServiceState(running=False, message=f"App Runner reports the service as {str(status).lower()}.")

    # ----- deletion -----

    def _build_resources(self, app: AppTarget) -> dict[str, str]:
        """What `_remove_build_resources` may clean up once the application's service is gone."""
        return {"app_id": app.id, "repository": self._repository_name(app), "project": self._project_name(app)}

    def _remove_build_resources(self, conn: ConnectionInfo, handle: dict[str, Any]) -> None:
        """Deletes the ECR repository and CodeBuild project CloudBridge created for a deleted application.

        Runs only after the service is gone (it pulls from that repository while it runs). Only resources
        tagged as CloudBridge's for this application are removed, and a role without the cleanup
        permissions leaves them in place instead of failing the deletion."""
        build = handle.get("build_resources")
        if not isinstance(build, dict):
            return
        app_id = str(build["app_id"])
        try:
            ecr = self._ecr(conn)
            repositories = ecr.describe_repositories(repositoryNames=[str(build["repository"])])["repositories"]
            tags = ecr.list_tags_for_resource(resourceArn=repositories[0]["repositoryArn"]).get("tags", [])
            if any(t.get("Key") == APP_TAG_KEY and t.get("Value") == app_id for t in tags):
                ecr.delete_repository(repositoryName=str(build["repository"]), force=True)
        except ClientError as error:
            if _error_code(error) != "RepositoryNotFoundException":
                logger.warning("ECR repository left in place", extra={"error_code": _error_code(error)})
        except Exception as error:  # noqa: BLE001 - cleanup is best effort
            logger.warning("ECR repository left in place", extra={"error_type": type(error).__name__})
        try:
            codebuild = self._codebuild(conn)
            projects = codebuild.batch_get_projects(names=[str(build["project"])]).get("projects", [])
            if projects:
                project_tags = {t.get("key"): t.get("value") for t in projects[0].get("tags", [])}
                # Projects created before the application tag was added carry only managed-by.
                if project_tags.get("managed-by") == "cloudbridge" and project_tags.get(APP_TAG_KEY, app_id) == app_id:
                    codebuild.delete_project(name=str(build["project"]))
        except ClientError as error:
            logger.warning("CodeBuild project left in place", extra={"error_code": _error_code(error)})
        except Exception as error:  # noqa: BLE001 - cleanup is best effort
            logger.warning("CodeBuild project left in place", extra={"error_type": type(error).__name__})

    def start_delete(self, conn: ConnectionInfo, app: AppTarget) -> dict[str, Any]:
        build = self._build_resources(app)
        if not app.provider_service_id:
            return {"done": True, "build_resources": build}
        try:
            self._apprunner(conn).delete_service(ServiceArn=app.provider_service_id)
        except ClientError as error:
            code = _error_code(error)
            if code == "ResourceNotFoundException":
                return {"done": True, "build_resources": build}
            if code == "InvalidStateException":
                raise ProviderError(
                    "provider_busy", "App Runner is still applying a change. CloudBridge will retry.", retryable=True
                ) from None
            raise translate(error) from None
        except Exception as error:  # noqa: BLE001
            raise translate(error) from None
        return {"service_id": app.provider_service_id, "build_resources": build}

    def _deleted(self, conn: ConnectionInfo, handle: dict[str, Any]) -> DeleteStatus:
        self._remove_build_resources(conn, handle)
        return DeleteStatus(state="deleted")

    def poll_delete(self, conn: ConnectionInfo, handle: dict[str, Any]) -> DeleteStatus:
        if handle.get("done"):
            return self._deleted(conn, handle)
        try:
            service = self._apprunner(conn).describe_service(ServiceArn=handle["service_id"])["Service"]
        except ClientError as error:
            if _error_code(error) == "ResourceNotFoundException":
                return self._deleted(conn, handle)
            raise translate(error) from None
        except Exception as error:  # noqa: BLE001
            raise translate(error) from None
        status = service.get("Status")
        if status == "DELETED":
            return self._deleted(conn, handle)
        if status == "DELETE_FAILED":
            return DeleteStatus(state="failed", message="App Runner could not delete the service.")
        return DeleteStatus(state="running")

    # ----- logs -----

    def runtime_logs(self, conn: ConnectionInfo, app: AppTarget, limit: int) -> tuple[list[RuntimeLogEntry], bool]:
        if not app.provider_service_id:
            return [], False
        # arn:aws:apprunner:REGION:ACCOUNT:service/NAME/ID
        resource = app.provider_service_id.split(":", 5)[-1].split("/")
        if len(resource) != 3:
            return [], False
        group = f"/aws/apprunner/{resource[1]}/{resource[2]}/application"
        start = int((clock.utcnow() - dt.timedelta(hours=1)).timestamp() * 1000)
        events: list[Any] = []
        try:
            client = self._client(conn, "logs")
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
        entries = [
            RuntimeLogEntry(
                timestamp=dt.datetime.fromtimestamp(int(e["timestamp"]) / 1000, dt.UTC),
                stream="stdout",
                message=log_line(str(e.get("message", ""))),
            )
            for e in events[-limit:]
            if "timestamp" in e
        ]
        return entries, truncated

    def service_host_allowed(self, host: str) -> bool:
        return host.endswith(".awsapprunner.com")

    # ----- metrics -----

    def metrics(self, conn: ConnectionInfo, app: AppTarget, minutes: int, step_seconds: int) -> list[MetricSeries]:
        if not app.provider_service_id:
            return []
        # arn:aws:apprunner:REGION:ACCOUNT:service/NAME/ID
        resource = app.provider_service_id.split(":", 5)[-1].split("/")
        if len(resource) != 3:
            return []
        dimensions = [{"Name": "ServiceName", "Value": resource[1]}, {"Name": "ServiceID", "Value": resource[2]}]
        return cloudwatch_series(
            self._client(conn, "cloudwatch"), "AWS/AppRunner", dimensions, APP_RUNNER_METRICS, minutes, step_seconds
        )

    # ----- custom domains -----

    def add_domain(self, conn: ConnectionInfo, app: AppTarget, domain: str) -> DomainState:
        if not app.provider_service_id:
            raise ProviderError("not_deployed", "Deploy the application before adding a domain.")
        try:
            response = self._apprunner(conn).associate_custom_domain(
                ServiceArn=app.provider_service_id, DomainName=domain, EnableWWWSubdomain=False
            )
        except ClientError as error:
            if _error_code(error) == "InvalidRequestException" and "already" in str(error).lower():
                return self.domain_status(conn, app, domain)
            raise translate(error) from None
        except Exception as error:  # noqa: BLE001
            raise translate(error) from None
        return _domain_state(response.get("CustomDomain") or {}, str(response.get("DNSTarget") or ""), domain)

    def domain_status(self, conn: ConnectionInfo, app: AppTarget, domain: str) -> DomainState:
        if not app.provider_service_id:
            return DomainState(status="failed", records=[], message="The application is not deployed.")
        try:
            client = self._apprunner(conn)
            kwargs: dict[str, Any] = {"ServiceArn": app.provider_service_id, "MaxResults": 5}
            for _ in range(20):
                page = client.describe_custom_domains(**kwargs)
                for item in page.get("CustomDomains", []):
                    if item.get("DomainName") == domain:
                        return _domain_state(item, str(page.get("DNSTarget") or ""), domain)
                token = page.get("NextToken")
                if not token:
                    break
                kwargs["NextToken"] = token
        except Exception as error:  # noqa: BLE001
            raise translate(error) from None
        return DomainState(status="failed", records=[], message="App Runner no longer lists this domain.")

    def remove_domain(self, conn: ConnectionInfo, app: AppTarget, domain: str) -> None:
        if not app.provider_service_id:
            return
        try:
            self._apprunner(conn).disassociate_custom_domain(ServiceArn=app.provider_service_id, DomainName=domain)
        except ClientError as error:
            if _error_code(error) in ("ResourceNotFoundException", "InvalidRequestException"):
                return
            raise translate(error) from None
        except Exception as error:  # noqa: BLE001
            raise translate(error) from None

    # ----- pause and resume -----

    def set_paused(self, conn: ConnectionInfo, app: AppTarget, paused: bool) -> None:
        if not app.provider_service_id:
            raise ProviderError("not_deployed", "The application is not deployed.")
        try:
            client = self._apprunner(conn)
            status = client.describe_service(ServiceArn=app.provider_service_id)["Service"].get("Status")
            if (status == "PAUSED") == paused:
                return
            if paused:
                client.pause_service(ServiceArn=app.provider_service_id)
            else:
                client.resume_service(ServiceArn=app.provider_service_id)
        except ClientError as error:
            if _error_code(error) == "InvalidStateException":
                raise ProviderError(
                    "provider_busy", "App Runner is still applying a change. CloudBridge will retry.", retryable=True
                ) from None
            raise translate(error) from None
        except Exception as error:  # noqa: BLE001
            raise translate(error) from None


# (CloudWatch metric, statistic, CloudBridge series, unit)
APP_RUNNER_METRICS: tuple[tuple[str, str, MetricName, str], ...] = (
    ("Requests", "Sum", "requests", "count"),
    ("4xxStatusResponses", "Sum", "errors_4xx", "count"),
    ("5xxStatusResponses", "Sum", "errors_5xx", "count"),
    ("RequestLatency", "Average", "latency_ms", "ms"),
    ("ActiveInstances", "Maximum", "instances", "count"),
    ("CPUUtilization", "Average", "cpu_percent", "percent"),
    ("MemoryUtilization", "Average", "memory_percent", "percent"),
)


def cloudwatch_series(
    client: Any,
    namespace: str,
    dimensions: list[dict[str, str]],
    metrics: tuple[tuple[str, str, MetricName, str], ...],
    minutes: int,
    step_seconds: int,
) -> list[MetricSeries]:
    end = clock.utcnow()
    queries = [
        {
            "Id": f"m{i}",
            "MetricStat": {
                "Metric": {"Namespace": namespace, "MetricName": metric, "Dimensions": dimensions},
                "Period": step_seconds,
                "Stat": stat,
            },
            "ReturnData": True,
        }
        for i, (metric, stat, _, _) in enumerate(metrics)
    ]
    values: dict[str, list[MetricPoint]] = {f"m{i}": [] for i in range(len(metrics))}
    try:
        kwargs: dict[str, Any] = {
            "MetricDataQueries": queries,
            "StartTime": end - dt.timedelta(minutes=minutes),
            "EndTime": end,
            "ScanBy": "TimestampAscending",
        }
        for _ in range(10):
            page = client.get_metric_data(**kwargs)
            for result in page.get("MetricDataResults", []):
                points = values.setdefault(str(result.get("Id")), [])
                for stamp, value in zip(result.get("Timestamps", []), result.get("Values", []), strict=False):
                    points.append(MetricPoint(timestamp=clock.as_utc(stamp), value=float(value)))
            token = page.get("NextToken")
            if not token:
                break
            kwargs["NextToken"] = token
    except Exception as error:  # noqa: BLE001
        raise translate(error) from None
    return [
        MetricSeries(name=name, unit=unit, points=sorted(values[f"m{i}"], key=lambda p: p.timestamp))
        for i, (_, _, name, unit) in enumerate(metrics)
    ]


def _domain_state(item: Any, dns_target: str, domain: str) -> DomainState:
    records = []
    if dns_target:
        records.append(DnsRecord(type="CNAME", name=domain, value=dns_target, purpose="Routes traffic to App Runner"))
    for record in item.get("CertificateValidationRecords") or []:
        records.append(
            DnsRecord(
                type=str(record.get("Type", "CNAME")),
                name=str(record.get("Name", "")),
                value=str(record.get("Value", "")),
                purpose="Proves you own the domain so App Runner can issue its certificate",
            )
        )
    status = str(item.get("Status", "")).lower()
    if status == "active":
        return DomainState(status="active", records=records)
    if status == "binding_certificate":
        return DomainState(status="verifying", records=records, message="App Runner is issuing the certificate.")
    if status.endswith("failed"):
        return DomainState(status="failed", records=records, message=f"App Runner reports {status.replace('_', ' ')}.")
    return DomainState(
        status="pending_dns", records=records, message="Create the DNS records below; App Runner then verifies them."
    )
