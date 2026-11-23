"""AI Gateway: credentials (BYOK), model catalog, routing, budgets, usage (etapa 02.1)

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-11-26

Five organization-scoped tables, all under FORCE RLS ``tenant_isolation`` over the
signed tenant context. New tables, so ``organization_id`` is NOT NULL from the start
(there is no legacy data to quarantine).

* ``ai_credentials``: Fernet-encrypted provider keys; at most one ACTIVE per
  (organization, provider) (partial unique index); revoked rows are wiped.
* ``ai_models``: the organization's model catalog (names/capabilities/prices are data).
* ``ai_routing``: primary + fallbacks per operation (composite FK keeps the primary in
  the same organization).
* ``ai_budgets``: limits + the counters of the atomic reservation.
* ``ai_usage_logs``: append-only for the application role (UPDATE/DELETE/TRUNCATE revoked).
"""
import os
import re
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, Sequence[str], None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ("ai_credentials", "ai_models", "ai_routing", "ai_budgets", "ai_usage_logs")
POLICY = "organization_id = (SELECT app_current_org())"
_ROLE_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _role() -> str:
    role = os.getenv("DB_RUNTIME_ROLE", "ads_app").strip()
    if role and not _ROLE_RE.match(role):
        raise ValueError("DB_RUNTIME_ROLE must be a plain lowercase identifier")
    return role


def _org_col() -> sa.Column:
    return sa.Column("organization_id", sa.String(), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False)


def _now() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"))


def upgrade() -> None:
    op.create_table(
        "ai_credentials",
        sa.Column("id", sa.String(), primary_key=True), _org_col(),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=True),
        sa.Column("encrypted_key", sa.Text(), nullable=False, server_default=""),
        sa.Column("last4", sa.String(), nullable=False, server_default=""),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        _now(),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_verification_ok", sa.Boolean(), nullable=True),
        sa.CheckConstraint("status IN ('active','revoked')", name="ck_ai_credentials_status"),
        sa.CheckConstraint("char_length(last4) <= 4", name="ck_ai_credentials_last4"),
    )
    op.create_index("ix_ai_credentials_organization_id", "ai_credentials", ["organization_id"])
    op.create_index("uq_ai_credentials_active_provider", "ai_credentials", ["organization_id", "provider"],
                    unique=True, postgresql_where=sa.text("status = 'active'"))

    op.create_table(
        "ai_models",
        sa.Column("id", sa.String(), primary_key=True), _org_col(),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("model_id", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("supports_tools", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("supports_vision", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("supports_json", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("context_window", sa.Integer(), nullable=True),
        sa.Column("max_output_tokens", sa.Integer(), nullable=True),
        sa.Column("input_price_micros_per_mtok", sa.BigInteger(), nullable=True),
        sa.Column("output_price_micros_per_mtok", sa.BigInteger(), nullable=True),
        _now(),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("organization_id", "provider", "model_id", name="uq_ai_models_org_provider_model"),
        sa.UniqueConstraint("id", "organization_id", name="uq_ai_models_id_org"),
        sa.CheckConstraint("input_price_micros_per_mtok IS NULL OR input_price_micros_per_mtok >= 0", name="ck_ai_models_in_price"),
        sa.CheckConstraint("output_price_micros_per_mtok IS NULL OR output_price_micros_per_mtok >= 0", name="ck_ai_models_out_price"),
    )
    op.create_index("ix_ai_models_organization_id", "ai_models", ["organization_id"])

    op.create_table(
        "ai_routing",
        sa.Column("id", sa.String(), primary_key=True), _org_col(),
        sa.Column("operation", sa.String(), nullable=False),
        sa.Column("primary_model_id", sa.String(), nullable=True),
        sa.Column("fallback_model_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("organization_id", "operation", name="uq_ai_routing_org_operation"),
    )
    op.create_index("ix_ai_routing_organization_id", "ai_routing", ["organization_id"])
    op.execute(sa.text(
        "ALTER TABLE ai_routing ADD CONSTRAINT fk_ai_routing_primary_org FOREIGN KEY (primary_model_id, organization_id) "
        "REFERENCES ai_models (id, organization_id) ON DELETE SET NULL (primary_model_id)"
    ))

    op.create_table(
        "ai_budgets",
        sa.Column("id", sa.String(), primary_key=True), _org_col(),
        sa.Column("period", sa.String(), nullable=False),
        sa.Column("limit_micros", sa.BigInteger(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("spent_micros", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("reserved_micros", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("organization_id", "period", name="uq_ai_budgets_org_period"),
        sa.CheckConstraint("period IN ('daily','monthly')", name="ck_ai_budgets_period"),
        sa.CheckConstraint("limit_micros >= 0 AND spent_micros >= 0 AND reserved_micros >= 0", name="ck_ai_budgets_nonneg"),
    )
    op.create_index("ix_ai_budgets_organization_id", "ai_budgets", ["organization_id"])

    op.create_table(
        "ai_usage_logs",
        sa.Column("id", sa.String(), primary_key=True), _org_col(),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("request_id", sa.String(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("operation", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("model_id", sa.String(), nullable=False),
        sa.Column("credential_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error_kind", sa.String(), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cached_input_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("reasoning_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("estimated_cost_micros", sa.BigInteger(), nullable=True),
        sa.Column("cost_micros", sa.BigInteger(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        _now(),
        sa.CheckConstraint("status IN ('success','error')", name="ck_ai_usage_status"),
    )
    op.create_index("ix_ai_usage_logs_org_created", "ai_usage_logs", ["organization_id", "created_at"])
    op.create_index("ix_ai_usage_logs_org_user", "ai_usage_logs", ["organization_id", "user_id"])

    for table in TABLES:
        op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"CREATE POLICY tenant_isolation ON {table} USING ({POLICY}) WITH CHECK ({POLICY})"))

    role = _role()
    if role:
        # Append-only usage trail for the application.
        op.execute(sa.text(f'REVOKE UPDATE, DELETE, TRUNCATE ON ai_usage_logs FROM "{role}"'))


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
