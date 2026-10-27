"""Azure Container Apps connection fields

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-16 19:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = (
    ("tenant_id", 36),
    ("subscription_id", 36),
    ("client_id", 36),
    ("resource_group", 90),
    ("environment_name", 64),
    ("registry_name", 50),
)


def upgrade() -> None:
    with op.batch_alter_table("provider_connections", schema=None) as batch_op:
        for name, length in COLUMNS:
            batch_op.add_column(sa.Column(name, sa.String(length=length), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("provider_connections", schema=None) as batch_op:
        for name, _ in reversed(COLUMNS):
            batch_op.drop_column(name)
