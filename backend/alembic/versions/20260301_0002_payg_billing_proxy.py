"""payg billing and provider proxy schema

Revision ID: 20260301_0002
Revises: 20260228_0001
Create Date: 2026-03-01 11:40:00
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260301_0002"
down_revision: Union[str, None] = "20260228_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _uuid() -> str:
    return str(uuid.uuid4())


def upgrade() -> None:
    op.create_table(
        "wallet_accounts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("balance_credits", sa.Numeric(18, 2), nullable=False, server_default=sa.text("0.00")),
        sa.Column("status", sa.String(length=20), nullable=False, server_default=sa.text("'active'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_wallet_accounts_user_id"),
    )
    op.create_index("ix_wallet_accounts_user_id", "wallet_accounts", ["user_id"], unique=True)

    op.create_table(
        "wallet_ledger_entries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("wallet_id", sa.String(length=36), nullable=False),
        sa.Column("entry_type", sa.String(length=20), nullable=False),
        sa.Column("amount_credits", sa.Numeric(18, 2), nullable=False),
        sa.Column("balance_before", sa.Numeric(18, 2), nullable=False),
        sa.Column("balance_after", sa.Numeric(18, 2), nullable=False),
        sa.Column("reference_type", sa.String(length=50), nullable=False),
        sa.Column("reference_id", sa.String(length=100), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["wallet_id"], ["wallet_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "entry_type",
            "idempotency_key",
            name="uq_wallet_ledger_user_type_idempotency",
        ),
    )
    op.create_index("ix_wallet_ledger_entries_user_id", "wallet_ledger_entries", ["user_id"], unique=False)
    op.create_index("ix_wallet_ledger_entries_wallet_id", "wallet_ledger_entries", ["wallet_id"], unique=False)
    op.create_index("ix_wallet_ledger_entries_reference_id", "wallet_ledger_entries", ["reference_id"], unique=False)
    op.create_index(
        "ix_wallet_ledger_entries_idempotency_key",
        "wallet_ledger_entries",
        ["idempotency_key"],
        unique=False,
    )
    op.create_index("ix_wallet_ledger_entries_created_at", "wallet_ledger_entries", ["created_at"], unique=False)

    op.create_table(
        "provider_accounts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("account_key", sa.String(length=50), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("api_key_encrypted", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_key", name="uq_provider_accounts_account_key"),
    )
    op.create_index("ix_provider_accounts_account_key", "provider_accounts", ["account_key"], unique=True)

    op.create_table(
        "model_pricing_rules",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("model_id", sa.String(length=120), nullable=False),
        sa.Column("provider_group", sa.String(length=50), nullable=False),
        sa.Column("provider_final_price_per_call", sa.Numeric(18, 6), nullable=False),
        sa.Column("ratio_hint", sa.Numeric(10, 4), nullable=True),
        sa.Column("markup", sa.Numeric(10, 4), nullable=False),
        sa.Column("charge_credits_override", sa.Numeric(18, 2), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("model_id", name="uq_model_pricing_rules_model_id"),
    )
    op.create_index("ix_model_pricing_rules_model_id", "model_pricing_rules", ["model_id"], unique=True)

    op.create_table(
        "proxy_requests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("model_id", sa.String(length=120), nullable=False),
        sa.Column("endpoint_type", sa.String(length=50), nullable=False),
        sa.Column("provider_group", sa.String(length=50), nullable=False),
        sa.Column("provider_request_id", sa.String(length=120), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("provider_http_status", sa.Integer(), nullable=True),
        sa.Column("charge_credits", sa.Numeric(18, 2), nullable=False, server_default=sa.text("0.00")),
        sa.Column("cost_provider_credits", sa.Numeric(18, 6), nullable=False, server_default=sa.text("0.000000")),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_proxy_requests_user_id", "proxy_requests", ["user_id"], unique=False)
    op.create_index("ix_proxy_requests_model_id", "proxy_requests", ["model_id"], unique=False)
    op.create_index("ix_proxy_requests_created_at", "proxy_requests", ["created_at"], unique=False)

    op.create_table(
        "payos_orders",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("order_code", sa.String(length=64), nullable=False),
        sa.Column("amount_vnd", sa.BigInteger(), nullable=False),
        sa.Column("credits_grant", sa.Numeric(18, 2), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("payos_checkout_url", sa.Text(), nullable=True),
        sa.Column("raw_response_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_code", name="uq_payos_orders_order_code"),
    )
    op.create_index("ix_payos_orders_user_id", "payos_orders", ["user_id"], unique=False)
    op.create_index("ix_payos_orders_order_code", "payos_orders", ["order_code"], unique=True)

    op.create_table(
        "payos_webhook_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("event_id", sa.String(length=120), nullable=True),
        sa.Column("order_code", sa.String(length=64), nullable=False),
        sa.Column("signature_valid", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("processed", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_payos_webhook_events_event_id", "payos_webhook_events", ["event_id"], unique=False)
    op.create_index(
        "ix_payos_webhook_events_order_code",
        "payos_webhook_events",
        ["order_code"],
        unique=False,
    )

    provider_table = sa.table(
        "provider_accounts",
        sa.column("id", sa.String(length=36)),
        sa.column("account_key", sa.String(length=50)),
        sa.column("base_url", sa.String(length=500)),
        sa.column("api_key_encrypted", sa.Text()),
        sa.column("is_active", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )

    now = datetime.utcnow()
    op.bulk_insert(
        provider_table,
        [
            {
                "id": _uuid(),
                "account_key": "gemini",
                "base_url": "",
                "api_key_encrypted": "",
                "is_active": True,
                "created_at": now,
                "updated_at": now,
            },
            {
                "id": _uuid(),
                "account_key": "default",
                "base_url": "",
                "api_key_encrypted": "",
                "is_active": True,
                "created_at": now,
                "updated_at": now,
            },
        ],
    )

    pricing_table = sa.table(
        "model_pricing_rules",
        sa.column("id", sa.String(length=36)),
        sa.column("model_id", sa.String(length=120)),
        sa.column("provider_group", sa.String(length=50)),
        sa.column("provider_final_price_per_call", sa.Numeric(18, 6)),
        sa.column("ratio_hint", sa.Numeric(10, 4)),
        sa.column("markup", sa.Numeric(10, 4)),
        sa.column("charge_credits_override", sa.Numeric(18, 2)),
        sa.column("is_active", sa.Boolean()),
        sa.column("effective_from", sa.DateTime(timezone=True)),
        sa.column("effective_to", sa.DateTime(timezone=True)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )

    op.bulk_insert(
        pricing_table,
        [
            {
                "id": _uuid(),
                "model_id": "gemini-2.5-flash-image",
                "provider_group": "gemini",
                "provider_final_price_per_call": 0.042000,
                "ratio_hint": 0.6000,
                "markup": 2.0000,
                "charge_credits_override": None,
                "is_active": True,
                "effective_from": now,
                "effective_to": None,
                "created_at": now,
                "updated_at": now,
            },
            {
                "id": _uuid(),
                "model_id": "gemini-3-pro-image-preview",
                "provider_group": "gemini",
                "provider_final_price_per_call": 0.480000,
                "ratio_hint": 0.6000,
                "markup": 2.0000,
                "charge_credits_override": None,
                "is_active": True,
                "effective_from": now,
                "effective_to": None,
                "created_at": now,
                "updated_at": now,
            },
            {
                "id": _uuid(),
                "model_id": "omni-moderation-latest",
                "provider_group": "default",
                "provider_final_price_per_call": 0.010000,
                "ratio_hint": 1.0000,
                "markup": 2.0000,
                "charge_credits_override": None,
                "is_active": True,
                "effective_from": now,
                "effective_to": None,
                "created_at": now,
                "updated_at": now,
            },
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_payos_webhook_events_order_code", table_name="payos_webhook_events")
    op.drop_index("ix_payos_webhook_events_event_id", table_name="payos_webhook_events")
    op.drop_table("payos_webhook_events")

    op.drop_index("ix_payos_orders_order_code", table_name="payos_orders")
    op.drop_index("ix_payos_orders_user_id", table_name="payos_orders")
    op.drop_table("payos_orders")

    op.drop_index("ix_proxy_requests_created_at", table_name="proxy_requests")
    op.drop_index("ix_proxy_requests_model_id", table_name="proxy_requests")
    op.drop_index("ix_proxy_requests_user_id", table_name="proxy_requests")
    op.drop_table("proxy_requests")

    op.drop_index("ix_model_pricing_rules_model_id", table_name="model_pricing_rules")
    op.drop_table("model_pricing_rules")

    op.drop_index("ix_provider_accounts_account_key", table_name="provider_accounts")
    op.drop_table("provider_accounts")

    op.drop_index("ix_wallet_ledger_entries_created_at", table_name="wallet_ledger_entries")
    op.drop_index("ix_wallet_ledger_entries_idempotency_key", table_name="wallet_ledger_entries")
    op.drop_index("ix_wallet_ledger_entries_reference_id", table_name="wallet_ledger_entries")
    op.drop_index("ix_wallet_ledger_entries_wallet_id", table_name="wallet_ledger_entries")
    op.drop_index("ix_wallet_ledger_entries_user_id", table_name="wallet_ledger_entries")
    op.drop_table("wallet_ledger_entries")

    op.drop_index("ix_wallet_accounts_user_id", table_name="wallet_accounts")
    op.drop_table("wallet_accounts")
