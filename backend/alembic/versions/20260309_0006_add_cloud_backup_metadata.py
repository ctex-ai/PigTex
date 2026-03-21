"""add cloud backup metadata tables

Revision ID: 20260309_0006
Revises: 20260308_0005
Create Date: 2026-03-09 10:30:00
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260309_0006"
down_revision: Union[str, None] = "20260308_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    if not _table_exists("user_devices"):
        op.create_table(
            "user_devices",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("device_key", sa.String(length=191), nullable=False),
            sa.Column("device_name", sa.String(length=100), nullable=False),
            sa.Column("platform", sa.String(length=32), nullable=False),
            sa.Column("app_version", sa.String(length=50), nullable=True),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("last_backup_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("user_id", "device_key", name="uq_user_devices_user_device_key"),
        )
        op.create_index("ix_user_devices_user_id", "user_devices", ["user_id"])

    if not _table_exists("cloud_storage_quotas"):
        op.create_table(
            "cloud_storage_quotas",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("plan_code", sa.String(length=64), nullable=False),
            sa.Column("quota_bytes", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("retention_days", sa.Integer(), nullable=False, server_default="30"),
            sa.Column("max_devices", sa.Integer(), nullable=False, server_default="5"),
            sa.Column("max_snapshots", sa.Integer(), nullable=False, server_default="30"),
            sa.Column("usage_bytes_cached", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("user_id", name="uq_cloud_storage_quotas_user_id"),
        )
        op.create_index("ix_cloud_storage_quotas_user_id", "cloud_storage_quotas", ["user_id"], unique=True)

    if not _table_exists("cloud_snapshot_manifests"):
        op.create_table(
            "cloud_snapshot_manifests",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("device_id", sa.String(length=36), sa.ForeignKey("user_devices.id"), nullable=False),
            sa.Column("scope_type", sa.String(length=32), nullable=False, server_default="account"),
            sa.Column("scope_id", sa.String(length=64), nullable=True),
            sa.Column("snapshot_kind", sa.String(length=32), nullable=False, server_default="full"),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="upload_requested"),
            sa.Column("manifest_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("base_snapshot_id", sa.String(length=36), nullable=True),
            sa.Column("bucket_name", sa.String(length=191), nullable=False),
            sa.Column("manifest_object_key", sa.String(length=512), nullable=False),
            sa.Column("payload_object_key", sa.String(length=512), nullable=False),
            sa.Column("storage_class", sa.String(length=32), nullable=False, server_default="STANDARD"),
            sa.Column("payload_size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("payload_sha256", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("encrypted", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("encryption_scheme", sa.String(length=64), nullable=True),
            sa.Column("counts_json", sa.Text(), nullable=True),
            sa.Column("metadata_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_cloud_snapshot_manifests_user_id", "cloud_snapshot_manifests", ["user_id"])
        op.create_index("ix_cloud_snapshot_manifests_device_id", "cloud_snapshot_manifests", ["device_id"])
        op.create_index("ix_cloud_snapshot_manifests_base_snapshot_id", "cloud_snapshot_manifests", ["base_snapshot_id"])

    if not _table_exists("cloud_restore_jobs"):
        op.create_table(
            "cloud_restore_jobs",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("snapshot_id", sa.String(length=36), sa.ForeignKey("cloud_snapshot_manifests.id"), nullable=False),
            sa.Column("target_device_id", sa.String(length=36), sa.ForeignKey("user_devices.id"), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="requested"),
            sa.Column("error_code", sa.String(length=64), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_cloud_restore_jobs_user_id", "cloud_restore_jobs", ["user_id"])
        op.create_index("ix_cloud_restore_jobs_snapshot_id", "cloud_restore_jobs", ["snapshot_id"])
        op.create_index("ix_cloud_restore_jobs_target_device_id", "cloud_restore_jobs", ["target_device_id"])


def downgrade() -> None:
    if _table_exists("cloud_restore_jobs"):
        op.drop_index("ix_cloud_restore_jobs_target_device_id", table_name="cloud_restore_jobs")
        op.drop_index("ix_cloud_restore_jobs_snapshot_id", table_name="cloud_restore_jobs")
        op.drop_index("ix_cloud_restore_jobs_user_id", table_name="cloud_restore_jobs")
        op.drop_table("cloud_restore_jobs")

    if _table_exists("cloud_snapshot_manifests"):
        op.drop_index("ix_cloud_snapshot_manifests_base_snapshot_id", table_name="cloud_snapshot_manifests")
        op.drop_index("ix_cloud_snapshot_manifests_device_id", table_name="cloud_snapshot_manifests")
        op.drop_index("ix_cloud_snapshot_manifests_user_id", table_name="cloud_snapshot_manifests")
        op.drop_table("cloud_snapshot_manifests")

    if _table_exists("cloud_storage_quotas"):
        op.drop_index("ix_cloud_storage_quotas_user_id", table_name="cloud_storage_quotas")
        op.drop_table("cloud_storage_quotas")

    if _table_exists("user_devices"):
        op.drop_index("ix_user_devices_user_id", table_name="user_devices")
        op.drop_table("user_devices")
