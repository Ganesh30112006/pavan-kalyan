"""Auto-deploy, environments, approvals, schedules, custom domains, alerts, API tokens, ECS and Kubernetes

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-17 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db import UTCDateTime

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FALSE = sa.text("0")


def upgrade() -> None:
    with op.batch_alter_table("provider_connections", schema=None) as batch_op:
        batch_op.add_column(sa.Column("ecs_cluster", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("api_server", sa.String(length=512), nullable=True))
        batch_op.add_column(sa.Column("ca_certificate", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("namespace", sa.String(length=63), nullable=True))
        batch_op.add_column(sa.Column("kubernetes_auth", sa.String(length=10), nullable=True))
        batch_op.add_column(sa.Column("token_audience", sa.String(length=200), nullable=True))

    with op.batch_alter_table("applications", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("environment", sa.String(length=20), nullable=False, server_default="production")
        )
        batch_op.add_column(sa.Column("promote_to_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("preview_of_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("preview_number", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("auto_deploy", sa.Boolean(), nullable=False, server_default=FALSE))
        batch_op.add_column(sa.Column("auto_deploy_branch", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("previews_enabled", sa.Boolean(), nullable=False, server_default=FALSE))
        batch_op.add_column(sa.Column("webhook_secret", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("config_from_repo", sa.Boolean(), nullable=False, server_default=FALSE))
        batch_op.add_column(sa.Column("requires_approval", sa.Boolean(), nullable=False, server_default=FALSE))
        batch_op.add_column(sa.Column("sleep_schedule", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("paused", sa.Boolean(), nullable=False, server_default=FALSE))
        batch_op.add_column(sa.Column("paused_by_schedule", sa.Boolean(), nullable=False, server_default=FALSE))
        batch_op.add_column(sa.Column("pending_auto_deploy", sa.JSON(), nullable=True))
        batch_op.create_index("ix_applications_preview_of_id", ["preview_of_id"], unique=False)

    with op.batch_alter_table("deployments", schema=None) as batch_op:
        batch_op.add_column(sa.Column("trigger", sa.String(length=20), nullable=False, server_default="manual"))
        batch_op.add_column(sa.Column("approved_by", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("approved_at", UTCDateTime(), nullable=True))

    op.create_table(
        "custom_domains",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(length=36),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "application_id",
            sa.String(length=36),
            sa.ForeignKey("applications.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("domain", sa.String(length=253), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("records", sa.JSON(), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("checked_at", UTCDateTime(), nullable=True),
        sa.Column("locked_until", UTCDateTime(), nullable=True),
        sa.UniqueConstraint("organization_id", "domain", name="uq_domain_org_domain"),
    )
    op.create_table(
        "alert_channels",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(length=36),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("signing_secret", sa.String(length=64), nullable=True),
        sa.Column("events", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("last_delivery_at", UTCDateTime(), nullable=True),
        sa.Column("last_delivery_status", sa.String(length=80), nullable=True),
    )
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(length=36),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
        ),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("prefix", sa.String(length=16), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("last_used_at", UTCDateTime(), nullable=True),
        sa.Column("expires_at", UTCDateTime(), nullable=True),
        sa.Column("revoked_at", UTCDateTime(), nullable=True),
    )
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("application_id", sa.String(length=36), nullable=False, index=True),
        sa.Column("event", sa.String(length=40), nullable=False),
        sa.Column("outcome", sa.String(length=80), nullable=False),
        sa.Column("received_at", UTCDateTime(), nullable=False, index=True),
    )


def downgrade() -> None:
    op.drop_table("webhook_deliveries")
    op.drop_table("api_tokens")
    op.drop_table("alert_channels")
    op.drop_table("custom_domains")
    with op.batch_alter_table("deployments", schema=None) as batch_op:
        for name in ("approved_at", "approved_by", "trigger"):
            batch_op.drop_column(name)
    with op.batch_alter_table("applications", schema=None) as batch_op:
        batch_op.drop_index("ix_applications_preview_of_id")
        for name in (
            "pending_auto_deploy",
            "paused_by_schedule",
            "paused",
            "sleep_schedule",
            "requires_approval",
            "config_from_repo",
            "webhook_secret",
            "previews_enabled",
            "auto_deploy_branch",
            "auto_deploy",
            "preview_number",
            "preview_of_id",
            "promote_to_id",
            "environment",
        ):
            batch_op.drop_column(name)
    with op.batch_alter_table("provider_connections", schema=None) as batch_op:
        for name in ("token_audience", "kubernetes_auth", "namespace", "ca_certificate", "api_server", "ecs_cluster"):
            batch_op.drop_column(name)
