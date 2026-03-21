"""widen audit JSON columns to LONGTEXT

Revision ID: 20260313_0008
Revises: 20260313_0007
Create Date: 2026-03-13 20:30:00
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260313_0008"
down_revision: Union[str, None] = "20260313_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # MySQL TEXT caps at 65 535 bytes which is too small for large skill
    # registry snapshots.  Switch the three JSON audit columns to LONGTEXT
    # (up to 4 GiB).
    for col in ("before_json", "after_json", "metadata_json"):
        op.alter_column(
            "admin_audit_events",
            col,
            existing_type=sa.Text(),
            type_=sa.Text().with_variant(sa.UnicodeText(length=2**30), "mysql"),
            existing_nullable=True,
        )


def downgrade() -> None:
    for col in ("before_json", "after_json", "metadata_json"):
        op.alter_column(
            "admin_audit_events",
            col,
            existing_type=sa.Text().with_variant(sa.UnicodeText(length=2**30), "mysql"),
            type_=sa.Text(),
            existing_nullable=True,
        )
