"""tenant isolation for ad-platform integrations, API keys and operations (etapa 01.3)

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-11-25

* ``google/meta/tiktok_ads_connections``, ``mutation_audits`` and the new
  ``ad_account_links`` / ``spend_approvals`` get ``organization_id`` + FORCE RLS
  ``tenant_isolation`` over the signed tenant context.
* Existing rows (per-USER connections, keys, audits) keep ``organization_id =
  NULL``: QUARANTINE. Encrypted tokens and every existing link between a user and
  an account are preserved untouched; nothing is claimed or deleted. A
  ``CHECK ... NOT VALID`` blocks new ownerless rows.
* ``api_keys`` gets ``organization_id`` and RLS whose policy ALSO lets a session
  read the one row whose ``key_hash`` it presents (authentication happens before
  any organization is known). Legacy keys stay readable only that way and are
  refused by the application until an operator binds them.
* ``oauth_states``: server-side, single-use OAuth state (control table, no RLS).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, Sequence[str], None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EXISTING = ("google_ads_connections", "meta_ads_connections", "tiktok_ads_connections", "mutation_audits")
NEW_TENANT = ("ad_account_links", "spend_approvals")
POLICY = "organization_id = (SELECT app_current_org())"
KEY_POLICY = (
    "organization_id = (SELECT app_current_org()) "
    "OR key_hash = NULLIF(current_setting('app.api_key_hash', true), '')"
)
UNIQUES = (
    ("google_ads_connections", "customer_id", "uq_google_ads_connections_org_customer"),
    ("meta_ads_connections", "ad_account_id", "uq_meta_ads_connections_org_account"),
    ("tiktok_ads_connections", "advertiser_id", "uq_tiktok_ads_connections_org_advertiser"),
)


def _add_org(table: str) -> None:
    op.add_column(table, sa.Column("organization_id", sa.String(), nullable=True))
    op.create_foreign_key(f"fk_{table}_organization_id", table, "organizations",
                          ["organization_id"], ["id"], ondelete="RESTRICT")
    op.create_index(f"ix_{table}_organization_id", table, ["organization_id"])


def upgrade() -> None:
    for table in EXISTING:
        _add_org(table)
    op.add_column("google_ads_connections", sa.Column("login_customer_id", sa.String(), nullable=True))
    op.add_column("mutation_audits", sa.Column("api_key_id", sa.String(), nullable=True))
    op.add_column("mutation_audits", sa.Column("approval_id", sa.String(), nullable=True))
    for table, column, name in UNIQUES:
        op.create_unique_constraint(name, table, ["organization_id", column])

    # ---- new tables
    op.create_table(
        "ad_account_links",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), nullable=True),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("external_account_id", sa.String(), nullable=False),
        sa.Column("account_name", sa.String(), nullable=True),
        sa.Column("login_customer_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("authorized_by_user_id", sa.String(), nullable=True),
        sa.Column("authorized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name="fk_ad_account_links_organization_id", ondelete="RESTRICT"),
        sa.UniqueConstraint("organization_id", "provider", "external_account_id", name="uq_ad_account_links_org_provider_account"),
        sa.CheckConstraint("provider IN ('google_ads','meta','tiktok')", name="ck_ad_account_links_provider"),
        sa.CheckConstraint("status IN ('pending','active','revoked')", name="ck_ad_account_links_status"),
    )
    op.create_index("ix_ad_account_links_organization_id", "ad_account_links", ["organization_id"])

    op.create_table(
        "spend_approvals",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), nullable=True),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("payload_hash", sa.String(), nullable=False),
        sa.Column("requested_budget_micros", sa.BigInteger(), nullable=True),
        sa.Column("max_budget_micros", sa.BigInteger(), nullable=True),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("requested_by_user_id", sa.String(), nullable=True),
        sa.Column("requested_by_api_key_id", sa.String(), nullable=True),
        sa.Column("approved_by_user_id", sa.String(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name="fk_spend_approvals_organization_id", ondelete="RESTRICT"),
        sa.UniqueConstraint("organization_id", "idempotency_key", name="uq_spend_approvals_org_idem"),
        sa.CheckConstraint("status IN ('pending','approved','rejected','consumed','expired')", name="ck_spend_approvals_status"),
    )
    op.create_index("ix_spend_approvals_organization_id", "spend_approvals", ["organization_id"])

    op.create_table(
        "oauth_states",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", sa.String(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("cookie_hash", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_oauth_states_expires_at", "oauth_states", ["expires_at"])

    # ---- api_keys
    op.add_column("api_keys", sa.Column("organization_id", sa.String(), nullable=True))
    op.create_foreign_key("fk_api_keys_organization_id", "api_keys", "organizations",
                          ["organization_id"], ["id"], ondelete="RESTRICT")
    op.create_index("ix_api_keys_organization_id", "api_keys", ["organization_id"])

    # ---- quarantine guards + RLS
    for table in EXISTING + NEW_TENANT + ("api_keys",):
        op.execute(sa.text(
            f"ALTER TABLE {table} ADD CONSTRAINT ck_{table}_org_required CHECK (organization_id IS NOT NULL) NOT VALID"
        ))
    for table in EXISTING + NEW_TENANT:
        op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"CREATE POLICY tenant_isolation ON {table} USING ({POLICY}) WITH CHECK ({POLICY})"))
    op.execute(sa.text("ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE api_keys FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"CREATE POLICY tenant_isolation ON api_keys USING ({KEY_POLICY}) WITH CHECK ({KEY_POLICY})"))


def downgrade() -> None:
    for table in ("api_keys",) + NEW_TENANT[::-1] + EXISTING[::-1]:
        op.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation ON {table}"))
        op.execute(sa.text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))
    for table in ("api_keys",) + EXISTING:
        op.execute(sa.text(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS ck_{table}_org_required"))
    op.drop_index("ix_api_keys_organization_id", table_name="api_keys")
    op.drop_constraint("fk_api_keys_organization_id", "api_keys", type_="foreignkey")
    op.drop_column("api_keys", "organization_id")
    op.drop_table("oauth_states")
    op.drop_table("spend_approvals")
    op.drop_table("ad_account_links")
    for table, _column, name in UNIQUES:
        op.drop_constraint(name, table, type_="unique")
    op.drop_column("mutation_audits", "approval_id")
    op.drop_column("mutation_audits", "api_key_id")
    op.drop_column("google_ads_connections", "login_customer_id")
    for table in reversed(EXISTING):
        op.drop_index(f"ix_{table}_organization_id", table_name=table)
        op.drop_constraint(f"fk_{table}_organization_id", table, type_="foreignkey")
        op.drop_column(table, "organization_id")
