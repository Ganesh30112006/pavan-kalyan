"""ORM models. Every tenant-owned row carries organization_id, and every query filters on it.

JSON columns are replaced, never mutated in place, so SQLAlchemy detects the change.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.clock import new_id, utcnow
from app.db import Base, UTCDateTime

ID = String(36)


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ID, ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    display_name: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(20))
    password_hash: Mapped[str] = mapped_column(String(255))
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    time_format: Mapped[str] = mapped_column(String(3), default="24h")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ID, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_seen_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, index=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)


class NotificationPreference(Base):
    __tablename__ = "notification_preferences"

    user_id: Mapped[str] = mapped_column(ID, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    email_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    events: Mapped[dict[str, bool]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)


class ProviderConnection(Base):
    __tablename__ = "provider_connections"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_connection_org_name"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ID, ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    provider: Mapped[str] = mapped_column(String(32))
    default_region: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="pending_validation", index=True)
    account_id: Mapped[str | None] = mapped_column(String(12), nullable=True)
    role_arn: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    project_id: Mapped[str | None] = mapped_column(String(30), nullable=True)
    workload_identity_provider: Mapped[str | None] = mapped_column(String(300), nullable=True)
    service_account_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    tenant_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    subscription_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    client_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    resource_group: Mapped[str | None] = mapped_column(String(90), nullable=True)
    environment_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    registry_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    ecs_cluster: Mapped[str | None] = mapped_column(String(255), nullable=True)
    api_server: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ca_certificate: Mapped[str | None] = mapped_column(Text, nullable=True)
    namespace: Mapped[str | None] = mapped_column(String(63), nullable=True)
    kubernetes_auth: Mapped[str | None] = mapped_column(String(10), nullable=True)
    token_audience: Mapped[str | None] = mapped_column(String(200), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trusted_principal: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    validation: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    validation_requested_by_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_validated_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    locked_until: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (
        # Names are unique among live applications; deleted ones keep their row for deployment history.
        Index(
            "uq_application_org_name_live",
            "organization_id",
            "name",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ID, ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(40))
    description: Mapped[str | None] = mapped_column(String(280), nullable=True)
    application_type: Mapped[str] = mapped_column(String(20))
    runtime: Mapped[str] = mapped_column(String(20))
    provider: Mapped[str] = mapped_column(String(32))
    # No foreign key: deleted applications keep their row (for deployment history) after their
    # connection is removed. Live applications always reference an existing connection (enforced on delete).
    connection_id: Mapped[str] = mapped_column(ID, index=True)
    region: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="draft")
    health: Mapped[str] = mapped_column(String(20), default="unknown")
    health_checked_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)

    source_type: Mapped[str] = mapped_column(String(20))
    source_image: Mapped[str | None] = mapped_column(String(512), nullable=True)
    repository_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ref_type: Mapped[str | None] = mapped_column(String(10), nullable=True)
    git_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dockerfile_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    build_context: Mapped[str | None] = mapped_column(String(255), nullable=True)

    current_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    current_deployment_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    image_uri: Mapped[str | None] = mapped_column(String(512), nullable=True)
    image_digest: Mapped[str | None] = mapped_column(String(80), nullable=True)
    service_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    provider_service_id: Mapped[str | None] = mapped_column(String(512), nullable=True)

    latest_deployment_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    active_deployment_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    revision_counter: Mapped[int] = mapped_column(Integer, default=0)

    environment: Mapped[str] = mapped_column(String(20), default="production")
    promote_to_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    preview_of_id: Mapped[str | None] = mapped_column(ID, nullable=True, index=True)
    preview_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    auto_deploy: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_deploy_branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    previews_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    webhook_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    """Signs GitHub webhooks for this application. Generated by CloudBridge; shown once."""
    config_from_repo: Mapped[bool] = mapped_column(Boolean, default=False)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    sleep_schedule: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    paused_by_schedule: Mapped[bool] = mapped_column(Boolean, default=False)
    pending_auto_deploy: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    """A push that arrived while a deployment was running; deployed as soon as the application is free."""

    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_by_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    delete_requested_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    delete_requested_by_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    delete_attempts: Mapped[int] = mapped_column(Integer, default=0)
    delete_state: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    deleted_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True, index=True)
    locked_until: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)


class ApplicationSpec(Base):
    """The requested configuration (versioned for optimistic locking) and what the provider last ran."""

    __tablename__ = "application_specs"

    application_id: Mapped[str] = mapped_column(ID, ForeignKey("applications.id", ondelete="CASCADE"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    port: Mapped[int] = mapped_column(Integer)
    cpu: Mapped[str] = mapped_column(String(16))
    memory: Mapped[str] = mapped_column(String(16))
    min_instances: Mapped[int] = mapped_column(Integer)
    max_instances: Mapped[int] = mapped_column(Integer)
    concurrency: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exposure: Mapped[str] = mapped_column(String(10))
    health_check_path: Mapped[str | None] = mapped_column(String(200), nullable=True)
    environment: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)
    secret_refs: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)
    updated_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)
    effective: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    adjustments: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)


class Deployment(Base):
    __tablename__ = "deployments"

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ID, ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    application_id: Mapped[str] = mapped_column(ID, ForeignKey("applications.id"), index=True)
    application_name: Mapped[str] = mapped_column(String(40))
    provider: Mapped[str] = mapped_column(String(32))
    region: Mapped[str | None] = mapped_column(String(64), nullable=True)
    kind: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20), index=True)
    version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    image_uri: Mapped[str | None] = mapped_column(String(512), nullable=True)
    image_digest: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source_ref: Mapped[str | None] = mapped_column(String(300), nullable=True)
    initiated_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    initiated_by_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    started_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    rollback_of: Mapped[str | None] = mapped_column(ID, nullable=True)
    trigger: Mapped[str] = mapped_column(String(20), default="manual")
    approved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    approved_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    stages: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    service_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    spec_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    """Worker bookkeeping: provider operation handles, timers and retry counters. Never returned by the API."""
    cancel_requested_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    next_attempt_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    locked_until: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)


class DeploymentEvent(Base):
    __tablename__ = "deployment_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    deployment_id: Mapped[str] = mapped_column(ID, ForeignKey("deployments.id", ondelete="CASCADE"), index=True)
    timestamp: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)
    level: Mapped[str] = mapped_column(String(10))
    stage: Mapped[str | None] = mapped_column(String(20), nullable=True)
    message: Mapped[str] = mapped_column(Text)


class DeploymentLog(Base):
    __tablename__ = "deployment_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    deployment_id: Mapped[str] = mapped_column(ID, ForeignKey("deployments.id", ondelete="CASCADE"), index=True)
    timestamp: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)
    stream: Mapped[str] = mapped_column(String(10))
    message: Mapped[str] = mapped_column(Text)


class AuditEvent(Base):
    """Append-only: rows are inserted by the backend and never updated or deleted through the API."""

    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ID, ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    occurred_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    actor_id: Mapped[str | None] = mapped_column(ID, nullable=True)
    actor_display_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    actor_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    resource_type: Mapped[str] = mapped_column(String(32))
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    outcome: Mapped[str] = mapped_column(String(10))
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)


class CustomDomain(Base):
    __tablename__ = "custom_domains"
    __table_args__ = (UniqueConstraint("organization_id", "domain", name="uq_domain_org_domain"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ID, ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    application_id: Mapped[str] = mapped_column(ID, ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    domain: Mapped[str] = mapped_column(String(253))
    status: Mapped[str] = mapped_column(String(20), default="pending_dns")
    records: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)
    checked_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    locked_until: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)


class AlertChannel(Base):
    __tablename__ = "alert_channels"

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ID, ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(10))
    url: Mapped[str] = mapped_column(String(2048))
    """The webhook address. It works like a password, so the API only ever returns its host."""
    signing_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    events: Mapped[list[str]] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_delivery_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_delivery_status: Mapped[str | None] = mapped_column(String(80), nullable=True)


class ApiToken(Base):
    """Personal access tokens for the CLI and CI. Only a SHA-256 hash is stored."""

    __tablename__ = "api_tokens"

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ID, ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ID, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    prefix: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_used_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    expires_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)


class WebhookDelivery(Base):
    """GitHub delivery IDs already handled, so a redelivered webhook does not deploy twice."""

    __tablename__ = "webhook_deliveries"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    application_id: Mapped[str] = mapped_column(ID, index=True)
    event: Mapped[str] = mapped_column(String(40))
    outcome: Mapped[str] = mapped_column(String(80))
    received_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("user_id", "scope", "key", name="uq_idempotency_user_scope_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[str] = mapped_column(ID, ForeignKey("organizations.id", ondelete="CASCADE"))
    user_id: Mapped[str] = mapped_column(ID, ForeignKey("users.id", ondelete="CASCADE"))
    scope: Mapped[str] = mapped_column(String(200))
    key: Mapped[str] = mapped_column(String(200))
    request_hash: Mapped[str] = mapped_column(String(64))
    status_code: Mapped[int] = mapped_column(Integer)
    resource_type: Mapped[str] = mapped_column(String(32))
    resource_id: Mapped[str] = mapped_column(ID)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
