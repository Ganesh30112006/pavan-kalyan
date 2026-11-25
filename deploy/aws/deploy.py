"""Deploys a CloudBridge image to AWS ECS Express Mode: creates the service the first time, updates it afterwards,
waits until the new image serves, then prints the service URL. Used by the CI/CD workflow.

    python deploy/aws/deploy.py --config deploy/aws/service.json --image <repository>@sha256:<digest>

Exit status: 0 when the new image is live, 1 when the rollout failed or timed out (ECS keeps or restores the
previous version), 2 on bad input. Needs boto3 and AWS credentials (in CI: GitHub OIDC, no stored keys).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

ROLLOUT_TIMEOUT_SECONDS = 25 * 60
POLL_SECONDS = 15


def load_config(path: Path) -> dict[str, Any]:
    config: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    required = (
        "region",
        "cluster",
        "service_name",
        "execution_role_arn",
        "infrastructure_role_arn",
        "task_role_arn",
        "container_port",
        "health_check_path",
        "cpu",
        "memory",
    )
    missing = [key for key in required if not config.get(key)]
    if missing:
        raise SystemExit(f"{path}: missing {', '.join(missing)}")
    return config


def service_settings(config: dict[str, Any], image: str) -> dict[str, Any]:
    """The parts of the service definition that create and update share."""
    container: dict[str, Any] = {
        "image": image,
        "containerPort": int(config["container_port"]),
        "environment": [{"name": k, "value": str(v)} for k, v in sorted((config.get("environment") or {}).items())],
    }
    secrets = config.get("secrets") or {}
    if secrets:
        container["secrets"] = [{"name": k, "valueFrom": v} for k, v in sorted(secrets.items())]
    return {
        "executionRoleArn": config["execution_role_arn"],
        "taskRoleArn": config["task_role_arn"],
        "healthCheckPath": config["health_check_path"],
        "primaryContainer": container,
        "cpu": str(config["cpu"]),
        "memory": str(config["memory"]),
        "scalingTarget": {
            "minTaskCount": int(config.get("min_tasks", 1)),
            "maxTaskCount": int(config.get("max_tasks", 1)),
            "autoScalingMetric": "AVERAGE_CPU",
            "autoScalingTargetValue": 70,
        },
    }


def find_service(ecs: Any, cluster: str, name: str) -> str | None:
    try:
        services = ecs.describe_services(cluster=cluster, services=[name]).get("services") or []
    except ClientError as error:
        if (error.response.get("Error") or {}).get("Code") == "ClusterNotFoundException":
            return None
        raise
    for service in services:
        if service.get("status") == "ACTIVE":
            return str(service["serviceArn"])
    return None


def service_url(ecs: Any, arn: str) -> str | None:
    service = ecs.describe_express_gateway_service(serviceArn=arn).get("service") or {}
    configurations = [c for c in service.get("activeConfigurations") or [] if isinstance(c, dict)]
    for configuration in sorted(configurations, key=lambda c: str(c.get("createdAt") or ""), reverse=True):
        for path in configuration.get("ingressPaths") or []:
            if path.get("accessType") == "PUBLIC" and path.get("endpoint"):
                endpoint = str(path["endpoint"])
                return endpoint if endpoint.startswith("https://") else f"https://{endpoint}"
    return None


def rollout_state(ecs: Any, cluster: str, name: str, image: str) -> str:
    """ "done" when only the deployment running `image` is left and it completed, "failed", or "running"."""
    services = ecs.describe_services(cluster=cluster, services=[name]).get("services") or []
    if not services:
        return "running"
    deployments = services[0].get("deployments") or []
    primary = next((d for d in deployments if d.get("status") == "PRIMARY"), None)
    if primary is None:
        return "running"
    definition = ecs.describe_task_definition(taskDefinition=primary["taskDefinition"]).get("taskDefinition") or {}
    images = [c.get("image") for c in definition.get("containerDefinitions") or []]
    if image not in images:
        # ECS rolled back to an earlier task definition, or has not switched yet.
        return "failed" if primary.get("rolloutState") == "COMPLETED" and len(deployments) == 1 else "running"
    if primary.get("rolloutState") == "FAILED":
        return "failed"
    if primary.get("rolloutState") == "COMPLETED" and len(deployments) == 1 and primary.get("runningCount", 0) >= 1:
        return "done"
    return "running"


def deploy(config: dict[str, Any], image: str) -> int:
    ecs = boto3.client("ecs", region_name=config["region"])
    cluster, name = config["cluster"], config["service_name"]
    settings = service_settings(config, image)
    arn = find_service(ecs, cluster, name)
    if arn is None:
        print(f"Creating ECS Express service {name} in {config['region']}.")
        response = ecs.create_express_gateway_service(
            serviceName=name,
            cluster=cluster,
            infrastructureRoleArn=config["infrastructure_role_arn"],
            tags=[{"key": "managed-by", "value": "cloudbridge-cd"}],
            **settings,
        )
        arn = str((response.get("service") or {}).get("serviceArn"))
    else:
        print(f"Updating ECS Express service {name} to {image}.")
        ecs.update_express_gateway_service(serviceArn=arn, **settings)
    deadline = time.monotonic() + ROLLOUT_TIMEOUT_SECONDS
    state = "running"
    while time.monotonic() < deadline:
        time.sleep(POLL_SECONDS)
        state = rollout_state(ecs, cluster, name, image)
        print(f"  rollout: {state}", flush=True)
        if state != "running":
            break
    url = service_url(ecs, arn)
    if state != "done":
        print(f"The rollout did not finish ({state}). ECS keeps or restores the previous version.", file=sys.stderr)
        return 1
    print(f"Live: {url}")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"url={url}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--image", required=True, help="image reference pinned by digest (repo@sha256:...)")
    args = parser.parse_args(argv)
    if "@sha256:" not in args.image:
        print("--image must be pinned by digest (repository@sha256:...).", file=sys.stderr)
        return 2
    return deploy(load_config(args.config), args.image)


if __name__ == "__main__":
    sys.exit(main())
