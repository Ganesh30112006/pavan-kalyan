"""Field rules. They mirror frontend/src/utils/validation.ts (same wording), and are stricter where the
backend has to be: the frontend's checks are for usability, these are the ones that are enforced.
Each function returns an error message, or None when the value is valid."""

from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Callable
from urllib.parse import urlsplit

APPLICATION_NAME = re.compile(r"(?a)^[a-z][a-z0-9-]*[a-z0-9]$")
IMAGE_REFERENCE = re.compile(
    r"(?a)^[a-z0-9]+(?:[._-][a-z0-9]+)*(?::\d+)?(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)+(?::[\w][\w.-]{0,127})?(?:@sha256:[a-f0-9]{64})?$"
)
ENV_NAME = re.compile(r"(?a)^[A-Za-z_][A-Za-z0-9_]*$")
GIT_COMMIT = re.compile(r"(?a)^[0-9a-fA-F]{7,40}$")
AWS_ACCOUNT_ID = re.compile(r"(?a)^\d{12}$")
AWS_ROLE_ARN = re.compile(r"(?a)^arn:aws(?:-[a-z]+)*:iam::(\d{12}):role/[\w+=,.@/-]{1,512}$")
GCP_PROJECT_ID = re.compile(r"(?a)^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
GCP_WORKLOAD_IDENTITY_PROVIDER = re.compile(
    r"(?a)^projects/\d+/locations/global/workloadIdentityPools/[a-z0-9-]{4,32}/providers/[a-z0-9-]{4,32}$"
)
GCP_SERVICE_ACCOUNT = re.compile(
    r"(?a)^[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z][a-z0-9-]{4,28}[a-z0-9]\.iam\.gserviceaccount\.com$"
)
AWS_SECRET_REF = re.compile(
    r"(?a)^arn:aws(?:-[a-z]+)*:(?:secretsmanager:[a-z0-9-]+:\d{12}:secret:[\w/+=.@-]+|ssm:[a-z0-9-]+:\d{12}:parameter/[\w/+=.@-]+)$"
)
AZURE_SECRET_REF = re.compile(
    r"(?a)^https://([a-zA-Z][a-zA-Z0-9-]{1,22}[a-zA-Z0-9])\.vault\.azure\.net/secrets/([a-zA-Z0-9-]{1,127})(?:/([a-f0-9]{32}))?$"
)
GUID = re.compile(r"(?a)^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
AZURE_RESOURCE_GROUP = re.compile(r"(?a)^[\w().-]{1,90}$")
AZURE_ENVIRONMENT = re.compile(r"(?a)^[a-z][a-z0-9-]{0,58}[a-z0-9]$")
AZURE_REGISTRY = re.compile(r"(?a)^[a-zA-Z0-9]{5,50}$")
K8S_SECRET_REF = re.compile(r"(?a)^k8s:([a-z0-9](?:[-a-z0-9.]{0,251}[a-z0-9])?)/([-._a-zA-Z0-9]{1,253})$")
GCP_SECRET_REF = re.compile(r"(?a)^projects/([a-z0-9-]+)/secrets/([A-Za-z0-9_-]+)(?:/versions/(latest|\d+))?$")
_BRANCH_FORBIDDEN = re.compile(r"(?a)\s|\.\.|[~^:?*\[\\]|^/|/$|\.lock$|@\{|^-|^@$")

Check = Callable[[str], str | None]


def application_name(value: str) -> str | None:
    if not value:
        return "Application name is required."
    if len(value) < 3 or len(value) > 40:
        return "Application name must be 3 to 40 characters."
    if not APPLICATION_NAME.match(value):
        return (
            "Application name can use lowercase letters, numbers and hyphens, starting with a letter "
            "and ending with a letter or number."
        )
    if "--" in value:
        return "Application name cannot contain consecutive hyphens."
    return None


def container_image(value: str) -> str | None:
    if not value:
        return "Image reference is required."
    if re.search(r"\s", value):
        return "Image reference cannot contain spaces."
    if re.match(r"^[a-z]+://", value, re.IGNORECASE):
        return "Image reference must not include a scheme; use registry/repository:tag."
    if len(value) > 512 or not IMAGE_REFERENCE.match(value):
        return (
            "Image reference must include the registry and repository, "
            "e.g. 123456789012.dkr.ecr.us-east-1.amazonaws.com/app:1.4.0"
        )
    return None


def git_branch(value: str) -> str | None:
    if not value.strip():
        return "Branch is required."
    if len(value) > 255 or _BRANCH_FORBIDDEN.search(value) or any(ord(c) < 32 or ord(c) == 127 for c in value):
        return "Branch is not a valid branch name."
    return None


def git_commit(value: str) -> str | None:
    if not value.strip():
        return "Commit SHA is required."
    if not GIT_COMMIT.match(value):
        return "Enter a commit SHA of 7 to 40 hexadecimal characters."
    return None


def _relative(value: str, label: str, allow_root: bool) -> str | None:
    if not value.strip():
        return f"{label} is required." + (" Use . for the repository root." if allow_root else "")
    parts = value.split("/")
    if value.startswith("/") or ".." in parts or "\\" in value or len(value) > 255:
        return f'{label} must be relative to the repository root and cannot contain "..".'
    if value.startswith("-") or any(ord(c) < 32 for c in value):
        return f"{label} contains characters that are not allowed."
    return None


def dockerfile_path(value: str) -> str | None:
    return _relative(value, "Dockerfile path", allow_root=False)


def build_context(value: str) -> str | None:
    return _relative(value, "Build context", allow_root=True)


def repository_url(value: str) -> str | None:
    """Syntax only. Address checks (SSRF) happen when the repository is used; see `repository_host_is_public`."""
    try:
        parts = urlsplit(value)
        host = parts.hostname or ""
        _ = parts.port
    except ValueError:
        return "Repository URL must be a full URL, for example https://github.com/org/repo.git"
    if not parts.scheme or not parts.netloc:
        return "Repository URL must be a full URL, for example https://github.com/org/repo.git"
    if parts.scheme != "https":
        return "Repository URL must use https://"
    if parts.username or parts.password:
        return "Repository URL must not include credentials. Repository access is configured on the backend."
    if host == "localhost" or _is_ip(host) or "." not in host or host.endswith((".local", ".internal", ".localhost")):
        return "Repository URL must use a public host name, not an IP address or local host."
    if len(parts.path) < 2:
        return "Repository URL must include the repository path, for example /org/repo.git"
    if parts.query or parts.fragment:
        return "Repository URL must not include a query string or fragment."
    if len(value) > 512 or re.search(r"\s", value):
        return "Repository URL must be a full URL, for example https://github.com/org/repo.git"
    return None


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return True


def repository_host_is_public(url: str, resolver: Callable[[str], list[str]] | None = None) -> str | None:
    """Resolves the repository host and rejects private, loopback, link-local and reserved addresses."""
    host = urlsplit(url).hostname or ""
    resolve = resolver or _resolve
    try:
        addresses = resolve(host)
    except OSError:
        return "The repository host name could not be resolved."
    if not addresses:
        return "The repository host name could not be resolved."
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address.split("%", 1)[0])
        except ValueError:
            return "The repository host resolves to an address CloudBridge cannot check."
        if not ip.is_global:
            return "The repository host resolves to a private or reserved network address."
    return None


def _resolve(host: str) -> list[str]:
    return sorted({str(info[4][0]) for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)})


def health_check_path(value: str) -> str | None:
    if not value.startswith("/"):
        return "Health check path must start with /."
    if re.search(r"\s", value) or len(value) > 200:
        return "Health check path cannot contain spaces or exceed 200 characters."
    return None


def env_var_name(value: str) -> str | None:
    if not value:
        return "Name is required."
    if not ENV_NAME.match(value):
        return "Use letters, numbers and underscores, starting with a letter or underscore."
    if len(value) > 128:
        return "Use 128 characters or fewer."
    return None


# Variables that only ever hold long-lived cloud credentials, which CloudBridge must never store.
_CREDENTIAL_NAMES = frozenset(
    {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AZURE_CLIENT_SECRET", "ARM_CLIENT_SECRET"}
)
# Values that are unmistakably credentials (formats with fixed prefixes or structure, so ordinary
# configuration values do not match).
_CREDENTIAL_VALUES = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\b[sr]k_live_[A-Za-z0-9]{16,}\b"),
    re.compile(r"AccountKey=[A-Za-z0-9+/=]{40,}"),
    re.compile(r'"type"\s*:\s*"service_account"'),
)


def plain_credential(name: str, value: str) -> str | None:
    """Rejects a plain environment variable that carries a credential: it belongs in the cloud's secret
    manager, added as a secret reference, so CloudBridge never stores it."""
    if name.upper() in _CREDENTIAL_NAMES or any(p.search(value) for p in _CREDENTIAL_VALUES):
        return "This looks like a credential. Put it in your secret manager and add a secret reference instead."
    return None


def secret_reference_provider(value: str) -> str | None:
    if AWS_SECRET_REF.match(value):
        return "aws_app_runner"
    if GCP_SECRET_REF.match(value):
        return "gcp_cloud_run"
    if AZURE_SECRET_REF.match(value):
        return "azure_container_apps"
    if K8S_SECRET_REF.match(value):
        return "kubernetes"
    return None


def secret_reference(value: str) -> str | None:
    if not value.strip():
        return "Secret reference is required."
    if len(value) > 2048 or secret_reference_provider(value) is None:
        return (
            "Use an AWS Secrets Manager / SSM parameter ARN, a Google Secret Manager name (projects/…/secrets/…), "
            "an Azure Key Vault secret URL (https://VAULT.vault.azure.net/secrets/NAME) "
            "or a Kubernetes secret key (k8s:SECRET/KEY)."
        )
    return None


def aws_account_id(value: str) -> str | None:
    return None if AWS_ACCOUNT_ID.match(value) else "An AWS account ID is 12 digits."


def aws_role_arn(value: str) -> str | None:
    if len(value) <= 2048 and AWS_ROLE_ARN.match(value):
        return None
    return "Enter an IAM role ARN, e.g. arn:aws:iam::123456789012:role/CloudBridgeDeploy"


def gcp_project_id(value: str) -> str | None:
    if GCP_PROJECT_ID.match(value):
        return None
    return "Project IDs are 6–30 lowercase letters, digits or hyphens, starting with a letter."


def gcp_workload_identity_provider(value: str) -> str | None:
    if GCP_WORKLOAD_IDENTITY_PROVIDER.match(value):
        return None
    return "Use the full resource name: projects/NUMBER/locations/global/workloadIdentityPools/POOL/providers/PROVIDER"


def gcp_service_account(value: str) -> str | None:
    if GCP_SERVICE_ACCOUNT.match(value):
        return None
    return "Enter a service account email ending in .iam.gserviceaccount.com"


def azure_guid(value: str, label: str) -> str | None:
    return None if GUID.match(value) else f"{label} is a GUID, e.g. 00000000-0000-0000-0000-000000000000"


def azure_resource_group(value: str) -> str | None:
    if AZURE_RESOURCE_GROUP.match(value) and not value.endswith("."):
        return None
    return "Resource group names are 1–90 letters, digits, underscores, hyphens, periods or parentheses."


def azure_environment(value: str) -> str | None:
    if AZURE_ENVIRONMENT.match(value) and "--" not in value:
        return None
    return "Environment names are 2–60 lowercase letters, digits or hyphens, starting with a letter."


def azure_registry(value: str) -> str | None:
    return None if AZURE_REGISTRY.match(value) else "Registry names are 5–50 letters or digits (without .azurecr.io)."


ECS_CLUSTER = re.compile(r"(?a)^[A-Za-z0-9_-]{1,255}$")
K8S_NAMESPACE = re.compile(r"(?a)^[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?$")
PEM_CERTIFICATE = re.compile(r"-----BEGIN CERTIFICATE-----[A-Za-z0-9+/=\s]+-----END CERTIFICATE-----")


def ecs_cluster(value: str) -> str | None:
    return None if ECS_CLUSTER.match(value) else "Cluster names are up to 255 letters, digits, hyphens or underscores."


def kubernetes_api_server(value: str, resolver: Callable[[str], list[str]] | None = None) -> str | None:
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.query:
        return "Enter the cluster's API server address, e.g. https://my-cluster-dns-abc123.hcp.eastus.azmk8s.io:443"
    # CloudBridge calls this address, so it must be a public one (the same guard as repository hosts).
    return repository_host_is_public(f"https://{parts.hostname}/", resolver)


def kubernetes_namespace(value: str) -> str | None:
    if K8S_NAMESPACE.match(value) and value not in ("kube-system", "kube-public", "kube-node-lease"):
        return None
    return "Use a namespace name of lowercase letters, digits and hyphens (not a kube-* system namespace)."


def pem_certificate(value: str) -> str | None:
    if PEM_CERTIFICATE.search(value) and "PRIVATE KEY" not in value:
        return None
    return "Paste the cluster's CA certificate (-----BEGIN CERTIFICATE----- …). Never paste a private key."


def looks_like_email(value: str) -> bool:
    return bool(re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", value))
