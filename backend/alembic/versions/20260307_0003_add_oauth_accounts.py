"""add oauth accounts table

Revision ID: 20260307_0003
Revises: 20260301_0002
Create Date: 2026-03-07 18:10:00
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260307_0003"
down_revision: Union[str, None] = "20260301_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _index_exists(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = inspector.get_indexes(table_name)
    return any(index.get("name") == index_name for index in indexes)


def upgrade() -> None:
    if not _table_exists("oauth_accounts"):
        op.create_table(
            "oauth_accounts",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.String(length=36), nullable=False),
            sa.Column("provider", sa.String(length=32), nullable=False),
            sa.Column("provider_account_id", sa.String(length=191), nullable=False),
            sa.Column("email", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("provider", "provider_account_id", name="uq_oauth_accounts_provider_account"),
        )

    if not _index_exists("oauth_accounts", "ix_oauth_accounts_user_id"):
        op.create_index("ix_oauth_accounts_user_id", "oauth_accounts", ["user_id"], unique=False)


def downgrade() -> None:
    if _table_exists("oauth_accounts"):
        if _index_exists("oauth_accounts", "ix_oauth_accounts_user_id"):
            op.drop_index("ix_oauth_accounts_user_id", table_name="oauth_accounts")
        op.drop_table("oauth_accounts")
