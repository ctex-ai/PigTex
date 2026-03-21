"""add PigTex Sync subscription schema

Revision ID: 20260314_0009
Revises: 20260313_0008
Create Date: 2026-03-14 10:30:00
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260314_0009"
down_revision: Union[str, None] = "20260313_0008"
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
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    if _table_exists("user_devices"):
        with op.batch_alter_table("user_devices") as batch_op:
            if not _column_exists("user_devices", "last_sync_push_at"):
                batch_op.add_column(sa.Column("last_sync_push_at", sa.DateTime(timezone=True), nullable=True))
            if not _column_exists("user_devices", "last_sync_pull_at"):
                batch_op.add_column(sa.Column("last_sync_pull_at", sa.DateTime(timezone=True), nullable=True))
            if not _column_exists("user_devices", "last_restore_at"):
                batch_op.add_column(sa.Column("last_restore_at", sa.DateTime(timezone=True), nullable=True))
            if not _column_exists("user_devices", "auto_sync_enabled"):
                batch_op.add_column(
                    sa.Column(
                        "auto_sync_enabled",
                        sa.Boolean(),
                        nullable=False,
                        server_default=sa.text("1"),
                    )
                )

    if _table_exists("cloud_storage_quotas"):
        with op.batch_alter_table("cloud_storage_quotas") as batch_op:
            if not _column_exists("cloud_storage_quotas", "sync_enabled"):
                batch_op.add_column(
                    sa.Column("sync_enabled", sa.Boolean(), nullable=False, server_default=sa.text("0"))
                )
            if not _column_exists("cloud_storage_quotas", "device_transfer_enabled"):
                batch_op.add_column(
                    sa.Column(
                        "device_transfer_enabled",
                        sa.Boolean(),
                        nullable=False,
                        server_default=sa.text("0"),
                    )
                )
            if not _column_exists("cloud_storage_quotas", "priority_level"):
                batch_op.add_column(
                    sa.Column("priority_level", sa.Integer(), nullable=False, server_default=sa.text("0"))
                )
            if not _column_exists("cloud_storage_quotas", "quota_source"):
                batch_op.add_column(
                    sa.Column(
                        "quota_source",
                        sa.String(length=32),
                        nullable=False,
                        server_default=sa.text("'system_default'"),
                    )
                )
            if not _column_exists("cloud_storage_quotas", "frozen_at"):
                batch_op.add_column(sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True))

    if _table_exists("cloud_snapshot_manifests"):
        with op.batch_alter_table("cloud_snapshot_manifests") as batch_op:
            if not _column_exists("cloud_snapshot_manifests", "trigger_reason"):
                batch_op.add_column(
                    sa.Column(
                        "trigger_reason",
                        sa.String(length=32),
                        nullable=False,
                        server_default=sa.text("'manual'"),
                    )
                )
            if not _column_exists("cloud_snapshot_manifests", "is_counted_for_quota"):
                batch_op.add_column(
                    sa.Column(
                        "is_counted_for_quota",
                        sa.Boolean(),
                        nullable=False,
                        server_default=sa.text("1"),
                    )
                )

    if not _table_exists("sync_billing_customers"):
        op.create_table(
            "sync_billing_customers",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column(
                "provider",
                sa.String(length=32),
                nullable=False,
                server_default=sa.text("'mock'"),
            ),
            sa.Column("provider_customer_id", sa.String(length=191), nullable=True),
            sa.Column("email", sa.String(length=255), nullable=True),
            sa.Column("metadata_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("user_id", name="uq_sync_billing_customers_user_id"),
            sa.UniqueConstraint("provider_customer_id", name="uq_sync_billing_customers_provider_customer_id"),
        )
        op.create_index("ix_sync_billing_customers_user_id", "sync_billing_customers", ["user_id"], unique=True)
        op.create_index(
            "ix_sync_billing_customers_provider_customer_id",
            "sync_billing_customers",
            ["provider_customer_id"],
            unique=True,
        )

    if not _table_exists("sync_billing_subscriptions"):
        op.create_table(
            "sync_billing_subscriptions",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("customer_id", sa.String(length=36), sa.ForeignKey("sync_billing_customers.id"), nullable=True),
            sa.Column(
                "provider",
                sa.String(length=32),
                nullable=False,
                server_default=sa.text("'mock'"),
            ),
            sa.Column("provider_subscription_id", sa.String(length=191), nullable=True),
            sa.Column("provider_price_id", sa.String(length=191), nullable=True),
            sa.Column(
                "plan_code",
                sa.String(length=32),
                nullable=False,
                server_default=sa.text("'sync'"),
            ),
            sa.Column(
                "billing_cycle",
                sa.String(length=16),
                nullable=False,
                server_default=sa.text("'monthly'"),
            ),
            sa.Column(
                "status",
                sa.String(length=32),
                nullable=False,
                server_default=sa.text("'pending'"),
            ),
            sa.Column(
                "cancel_at_period_end",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=True),
            sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
            sa.Column("grace_ends_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("metadata_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint(
                "provider_subscription_id",
                name="uq_sync_billing_subscriptions_provider_subscription_id",
            ),
        )
        op.create_index("ix_sync_billing_subscriptions_user_id", "sync_billing_subscriptions", ["user_id"])
        op.create_index("ix_sync_billing_subscriptions_customer_id", "sync_billing_subscriptions", ["customer_id"])
        op.create_index(
            "ix_sync_billing_subscriptions_provider_subscription_id",
            "sync_billing_subscriptions",
            ["provider_subscription_id"],
            unique=True,
        )

    if not _table_exists("sync_billing_events"):
        op.create_table(
            "sync_billing_events",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "provider",
                sa.String(length=32),
                nullable=False,
                server_default=sa.text("'mock'"),
            ),
            sa.Column("event_type", sa.String(length=64), nullable=False),
            sa.Column("provider_event_id", sa.String(length=191), nullable=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=True),
            sa.Column(
                "subscription_id",
                sa.String(length=36),
                sa.ForeignKey("sync_billing_subscriptions.id"),
                nullable=True,
            ),
            sa.Column(
                "signature_valid",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column(
                "processed",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_sync_billing_events_provider_event_id", "sync_billing_events", ["provider_event_id"])
        op.create_index("ix_sync_billing_events_user_id", "sync_billing_events", ["user_id"])
        op.create_index("ix_sync_billing_events_subscription_id", "sync_billing_events", ["subscription_id"])


def downgrade() -> None:
    if _table_exists("sync_billing_events"):
        op.drop_index("ix_sync_billing_events_subscription_id", table_name="sync_billing_events")
        op.drop_index("ix_sync_billing_events_user_id", table_name="sync_billing_events")
        op.drop_index("ix_sync_billing_events_provider_event_id", table_name="sync_billing_events")
        op.drop_table("sync_billing_events")

    if _table_exists("sync_billing_subscriptions"):
        op.drop_index(
            "ix_sync_billing_subscriptions_provider_subscription_id",
            table_name="sync_billing_subscriptions",
        )
        op.drop_index("ix_sync_billing_subscriptions_customer_id", table_name="sync_billing_subscriptions")
        op.drop_index("ix_sync_billing_subscriptions_user_id", table_name="sync_billing_subscriptions")
        op.drop_table("sync_billing_subscriptions")

    if _table_exists("sync_billing_customers"):
        op.drop_index(
            "ix_sync_billing_customers_provider_customer_id",
            table_name="sync_billing_customers",
        )
        op.drop_index("ix_sync_billing_customers_user_id", table_name="sync_billing_customers")
        op.drop_table("sync_billing_customers")
