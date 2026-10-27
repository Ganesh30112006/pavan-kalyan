"""Runtime configuration, read from CLOUDBRIDGE_* environment variables (and an optional .env file)."""

from __future__ import annotations

from functools import cached_property
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_AWS_REGIONS = (
    "us-east-1,us-east-2,us-west-2,ca-central-1,eu-central-1,eu-west-1,eu-west-2,eu-west-3,"
    "ap-south-1,ap-southeast-1,ap-southeast-2,ap-northeast-1"
)
DEFAULT_AZURE_REGIONS = (
    "eastus,eastus2,westus2,westus3,centralus,canadacentral,northeurope,westeurope,uksouth,francecentral,"
    "germanywestcentral,centralindia,southeastasia,eastasia,japaneast,australiaeast,uaenorth"
)
DEFAULT_GCP_REGIONS = (
    "us-central1,us-east1,us-east4,us-west1,northamerica-northeast1,europe-west1,europe-west2,europe-west3,"
    "europe-west4,asia-south1,asia-southeast1,asia-northeast1,australia-southeast1"
)


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CLOUDBRIDGE_", env_file=".env", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite:///./cloudbridge.db"
    log_level: str = "INFO"

    # Browser-facing security
    cors_origins: str = ""
    """Comma-separated origins allowed to call the API with credentials (only needed when the API is cross-origin)."""
    cookie_secure: bool = True
    cookie_domain: str | None = None
    session_idle_minutes: int = 480
    session_absolute_hours: int = 168
    login_max_attempts: int = 10
    login_window_seconds: int = 300
    max_body_bytes: int = 1_048_576
    trust_proxy_headers: bool = False
    """Use X-Forwarded-For for the client address (only behind a trusted reverse proxy)."""
    maintenance_mode: bool = False
    enable_api_docs: bool | None = None

    # Background worker
    run_worker: bool = True
    """Run the deployment worker inside the API process. Set false when running `python -m app.worker` separately."""
    worker_poll_seconds: float = 2.0
    health_check_interval_seconds: int = 60
    build_timeout_minutes: int = 60
    rollout_timeout_minutes: int = 30
    verification_timeout_seconds: int = 300
    connection_validation_timeout_seconds: int = 120

    # AWS (App Runner). CloudBridge's own identity comes from the standard AWS credential chain
    # (instance or task role); customer accounts are reached only through sts:AssumeRole with an external ID.
    aws_principal_arn: str | None = None
    """The ARN of CloudBridge's own AWS role, shown to users as the principal their role must trust."""
    aws_regions: str = DEFAULT_AWS_REGIONS
    aws_sts_region: str = "us-east-1"
    aws_apprunner_access_role_name: str = "CloudBridgeAppRunnerECRAccess"
    aws_apprunner_instance_role_name: str = "CloudBridgeAppRunnerInstance"
    aws_codebuild_role_name: str = "CloudBridgeCodeBuild"
    aws_ecr_repository_prefix: str = "cloudbridge"
    aws_ecs_execution_role_name: str = "CloudBridgeECSExecution"
    aws_ecs_infrastructure_role_name: str = "CloudBridgeECSInfrastructure"

    # Kubernetes. AKS clusters are reached with a Microsoft Entra token for the connection's app registration
    # (the same federated identity as Azure); other clusters can trust CloudBridge's OIDC token directly.
    kubernetes_aks_scope: str = "6dae42f8-4368-4678-94ff-3960e28e3630/.default"

    # Alerts (Slack, Microsoft Teams, signed webhooks)
    allow_private_alert_urls: bool = False
    """Development only: allow alert webhooks on private addresses (e.g. a local test receiver)."""

    # Google Cloud (Cloud Run). CloudBridge authenticates with workload identity federation.
    gcp_subject_token_source: Literal["none", "aws", "file"] = "none"
    """How CloudBridge proves its identity to Google STS: its AWS identity, or an OIDC token file."""
    gcp_subject_token_file: str | None = None
    gcp_trusted_principal: str | None = None
    """Shown to users as the identity their workload identity pool must trust."""
    gcp_regions: str = DEFAULT_GCP_REGIONS
    gcp_artifact_repository: str = "cloudbridge"

    # Microsoft Azure (Container Apps). CloudBridge authenticates with a federated credential: it presents a
    # signed token for its own identity (from AWS outbound identity federation, or an OIDC token file) to
    # Microsoft Entra ID, which trusts it through a federated credential on the customer's app registration.
    azure_token_source: Literal["none", "aws", "file"] = "none"
    """Where CloudBridge's federated token comes from: its AWS identity (sts:GetWebIdentityToken) or a token file."""
    azure_token_file: str | None = None
    azure_federation_issuer: str | None = None
    """Shown to users: the issuer their federated credential must trust."""
    azure_federation_subject: str | None = None
    """Shown to users: the subject their federated credential must trust."""
    azure_regions: str = DEFAULT_AZURE_REGIONS
    azure_runtime_identity_name: str = "cloudbridge-runtime"
    """Managed identity (in the connection's resource group) that pulls private images and reads Key Vault secrets."""
    azure_acr_repository_prefix: str = "cloudbridge"

    # E-mail notifications (optional; notification preferences are offered only when configured)
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str | None = None
    smtp_starttls: bool = True
    public_url: str | None = None
    """Base URL of the web app, used for links in e-mails."""

    @model_validator(mode="after")
    def _check_production(self) -> Settings:
        if self.environment == "production":
            if not self.cookie_secure:
                raise ValueError("CLOUDBRIDGE_COOKIE_SECURE must be true in production.")
            if "*" in self.cors_origin_list:
                raise ValueError("CLOUDBRIDGE_CORS_ORIGINS cannot contain '*'.")
            if self.allow_private_alert_urls:
                raise ValueError("CLOUDBRIDGE_ALLOW_PRIVATE_ALERT_URLS cannot be used in production.")
        if "*" in self.cors_origin_list:
            raise ValueError("CLOUDBRIDGE_CORS_ORIGINS cannot contain '*' because credentials are allowed.")
        if self.azure_token_source == "file" and not self.azure_token_file:
            raise ValueError("CLOUDBRIDGE_AZURE_TOKEN_FILE is required when the Azure token source is 'file'.")
        if self.gcp_subject_token_source == "file" and not self.gcp_subject_token_file:
            raise ValueError("CLOUDBRIDGE_GCP_SUBJECT_TOKEN_FILE is required when the subject token source is 'file'.")
        return self

    @cached_property
    def cors_origin_list(self) -> list[str]:
        return _csv(self.cors_origins)

    @cached_property
    def aws_region_list(self) -> list[str]:
        return _csv(self.aws_regions)

    @cached_property
    def azure_region_list(self) -> list[str]:
        return _csv(self.azure_regions)

    @cached_property
    def gcp_region_list(self) -> list[str]:
        return _csv(self.gcp_regions)

    @property
    def notifications_enabled(self) -> bool:
        return bool(self.smtp_host and self.smtp_from)

    @property
    def api_docs_enabled(self) -> bool:
        if self.enable_api_docs is not None:
            return self.enable_api_docs
        return self.environment != "production"
