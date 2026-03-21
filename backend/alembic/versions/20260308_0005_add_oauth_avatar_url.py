"""add avatar_url to oauth_accounts

Revision ID: 20260308_0005
Revises: 20260307_0004
Create Date: 2026-03-08 16:15:00
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260308_0005"
down_revision: Union[str, None] = "20260307_0004"
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


def upgrade() -> None:
    if _table_exists("oauth_accounts") and not _column_exists("oauth_accounts", "avatar_url"):
        op.add_column("oauth_accounts", sa.Column("avatar_url", sa.String(length=512), nullable=True))


def downgrade() -> None:
    if _table_exists("oauth_accounts") and _column_exists("oauth_accounts", "avatar_url"):
        op.drop_column("oauth_accounts", "avatar_url")
