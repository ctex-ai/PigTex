"""cleanup website-specific schema for pigtex-only database

Revision ID: 20260307_0004
Revises: 20260307_0003
Create Date: 2026-03-07 18:55:00
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260307_0004"
down_revision: Union[str, None] = "20260307_0003"
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
    return any(col.get("name") == column_name for col in inspector.get_columns(table_name))


def _index_exists(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return False
    indexes = inspector.get_indexes(table_name)
    return any(index.get("name") == index_name for index in indexes)


def upgrade() -> None:
    # Drop known Website-only tables.
    website_tables = [
        "checklist_items",
        "checklists",
        "changelog",
        "creditbatch",
        "transaction",
        "invoice",
        "notification",
        "account",
        "session",
        "verificationtoken",
        "_prisma_migrations",
    ]

    op.execute("SET FOREIGN_KEY_CHECKS=0")
    try:
        for table_name in website_tables:
            if _table_exists(table_name):
                op.drop_table(table_name)
    finally:
        op.execute("SET FOREIGN_KEY_CHECKS=1")

    # Remove website-only columns from users table.
    if _table_exists("users"):
        if _index_exists("users", "users_discord_id_key"):
            op.drop_index("users_discord_id_key", table_name="users")

        removable_user_columns = [
            "avatar",
            "discord_id",
            "balance",
            "reserved_balance",
            "role",
            "status",
            "email_verified",
            "two_factor_enabled",
            "two_factor_secret",
        ]
        for column_name in removable_user_columns:
            if _column_exists("users", column_name):
                op.drop_column("users", column_name)


def downgrade() -> None:
    # Irreversible cleanup: dropped tables/columns include user data and website-specific state.
    pass
