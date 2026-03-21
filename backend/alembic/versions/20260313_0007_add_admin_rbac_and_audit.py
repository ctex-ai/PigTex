"""add admin role and audit trail

Revision ID: 20260313_0007
Revises: 20260309_0006
Create Date: 2026-03-13 16:45:00
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260313_0007"
down_revision: Union[str, None] = "20260309_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _column_exists(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return False
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    if _table_exists("users") and not _column_exists("users", "role"):
        op.add_column(
            "users",
            sa.Column("role", sa.String(length=32), nullable=False, server_default="user"),
        )

    if not _table_exists("admin_audit_events"):
        op.create_table(
            "admin_audit_events",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("actor_user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("action", sa.String(length=64), nullable=False),
            sa.Column("resource_type", sa.String(length=64), nullable=False),
            sa.Column("resource_id", sa.String(length=191), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="success"),
            sa.Column("summary", sa.String(length=255), nullable=True),
            sa.Column("before_json", sa.Text(), nullable=True),
            sa.Column("after_json", sa.Text(), nullable=True),
            sa.Column("metadata_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_admin_audit_events_actor_user_id", "admin_audit_events", ["actor_user_id"])
        op.create_index("ix_admin_audit_events_action", "admin_audit_events", ["action"])
        op.create_index("ix_admin_audit_events_resource_type", "admin_audit_events", ["resource_type"])
        op.create_index("ix_admin_audit_events_resource_id", "admin_audit_events", ["resource_id"])


def downgrade() -> None:
    if _table_exists("admin_audit_events"):
        op.drop_index("ix_admin_audit_events_resource_id", table_name="admin_audit_events")
        op.drop_index("ix_admin_audit_events_resource_type", table_name="admin_audit_events")
        op.drop_index("ix_admin_audit_events_action", table_name="admin_audit_events")
        op.drop_index("ix_admin_audit_events_actor_user_id", table_name="admin_audit_events")
        op.drop_table("admin_audit_events")

    if _table_exists("users") and _column_exists("users", "role"):
        op.drop_column("users", "role")
