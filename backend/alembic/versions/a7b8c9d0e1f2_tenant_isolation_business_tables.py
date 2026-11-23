"""tenant isolation for the remaining business tables (etapa 01.2)

Revision ID: a7b8c9d0e1f2
Revises: f1a2b3c4d5e6
Create Date: 2026-11-24

Private data (creatives, templates, campaigns, research, logs, blacklists,
brand scrapes) gets ``organization_id`` + FORCE RLS ``tenant_isolation`` over
the signed tenant context, exactly like brands/products/profiles:

* Legacy rows keep ``organization_id = NULL`` (QUARANTINE): invisible to every
  organization, never auto-claimed, never deleted. A ``CHECK ... NOT VALID``
  blocks NEW ownerless rows without validating the legacy ones.
* Composite (id, organization_id) foreign keys make cross-organization links
  impossible at the database level, including generated_ads -> brand / product /
  template. ``ON DELETE SET NULL (col)`` (PostgreSQL 15+) nulls only the link
  column and keeps the organization of the row.
* Global uniqueness (scraped ad hashes, page/keyword blacklists, page names,
  vertical names) becomes per organization; otherwise one tenant could probe or
  block another one through unique-violation errors.
* ``prompts`` and ``ad_styles`` are GLOBAL catalogs: every tenant can read,
  only the signed platform-admin context can write.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, Sequence[str], None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# parents before children
NEW_TABLES = (
    "winning_ads", "facebook_campaigns", "facebook_adsets", "facebook_ads",
    "generated_ads", "verticals", "facebook_pages", "saved_searches", "scraped_ads",
    "search_logs", "api_usage_logs", "page_blacklist", "keyword_blacklist",
    "brand_scrapes", "brand_scraped_ads",
)
CATALOGS = ("prompts", "ad_styles")
POLICY = "organization_id = (SELECT app_current_org())"

# tables that become the target of a composite foreign key
UNIQUE_ID_ORG = ("products", "winning_ads", "facebook_campaigns", "facebook_adsets",
                 "verticals", "facebook_pages", "saved_searches", "brand_scrapes")

# (child, column, parent, constraint name, on delete)
COMPOSITE_FKS = (
    ("facebook_adsets", "campaign_id", "facebook_campaigns", "fk_facebook_adsets_campaign_org", "CASCADE"),
    ("facebook_ads", "adset_id", "facebook_adsets", "fk_facebook_ads_adset_org", "CASCADE"),
    ("generated_ads", "brand_id", "brands", "fk_generated_ads_brand_org", "SET NULL (brand_id)"),
    ("generated_ads", "product_id", "products", "fk_generated_ads_product_org", "SET NULL (product_id)"),
    ("generated_ads", "template_id", "winning_ads", "fk_generated_ads_template_org", "SET NULL (template_id)"),
    ("facebook_pages", "vertical_id", "verticals", "fk_facebook_pages_vertical_org", "SET NULL (vertical_id)"),
    ("saved_searches", "vertical_id", "verticals", "fk_saved_searches_vertical_org", "SET NULL (vertical_id)"),
    ("search_logs", "vertical_id", "verticals", "fk_search_logs_vertical_org", "SET NULL (vertical_id)"),
    ("scraped_ads", "search_id", "saved_searches", "fk_scraped_ads_search_org", "CASCADE"),
    ("scraped_ads", "facebook_page_id", "facebook_pages", "fk_scraped_ads_page_org", "SET NULL (facebook_page_id)"),
    ("brand_scraped_ads", "brand_scrape_id", "brand_scrapes", "fk_brand_scraped_ads_scrape_org", "CASCADE"),
)

# (table, column, old unique index, new unique constraint)
PER_ORG_UNIQUES = (
    ("scraped_ads", "content_hash", "ix_scraped_ads_content_hash", "uq_scraped_ads_org_content_hash"),
    ("scraped_ads", "external_id", "ix_scraped_ads_external_id", "uq_scraped_ads_org_external_id"),
    ("facebook_pages", "page_name", "ix_facebook_pages_page_name", "uq_facebook_pages_org_page_name"),
    ("page_blacklist", "page_name", "ix_page_blacklist_page_name", "uq_page_blacklist_org_page_name"),
    ("keyword_blacklist", "keyword", "ix_keyword_blacklist_keyword", "uq_keyword_blacklist_org_keyword"),
    ("verticals", "name", "ix_verticals_name", "uq_verticals_org_name"),
)


def upgrade() -> None:
    bind = op.get_bind()
    if int(bind.execute(sa.text("SHOW server_version_num")).scalar_one()) < 150000:
        raise RuntimeError("PostgreSQL 15+ is required (ON DELETE SET NULL (column) on composite foreign keys)")

    for table in NEW_TABLES:
        op.add_column(table, sa.Column("organization_id", sa.String(), nullable=True))
        op.create_foreign_key(f"fk_{table}_organization_id", table, "organizations",
                              ["organization_id"], ["id"], ondelete="RESTRICT")
        op.create_index(f"ix_{table}_organization_id", table, ["organization_id"])
        op.execute(sa.text(
            f"ALTER TABLE {table} ADD CONSTRAINT ck_{table}_org_required "
            "CHECK (organization_id IS NOT NULL) NOT VALID"
        ))

    for table in UNIQUE_ID_ORG:
        op.create_unique_constraint(f"uq_{table}_id_org", table, ["id", "organization_id"])

    for table, column, old_index, new_constraint in PER_ORG_UNIQUES:
        op.drop_index(old_index, table_name=table)
        op.create_index(old_index, table, [column])  # plain lookup index
        op.create_unique_constraint(new_constraint, table, ["organization_id", column])

    for child, column, parent, name, on_delete in COMPOSITE_FKS:
        op.execute(sa.text(
            f"ALTER TABLE {child} ADD CONSTRAINT {name} FOREIGN KEY ({column}, organization_id) "
            f"REFERENCES {parent} (id, organization_id) ON DELETE {on_delete}"
        ))

    for table in NEW_TABLES:
        op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"CREATE POLICY tenant_isolation ON {table} USING ({POLICY}) WITH CHECK ({POLICY})"))

    for table in CATALOGS:
        op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"CREATE POLICY catalog_read ON {table} FOR SELECT USING (true)"))
        op.execute(sa.text(
            f"CREATE POLICY catalog_admin_write ON {table} FOR ALL "
            "USING (app_is_platform_admin()) WITH CHECK (app_is_platform_admin())"
        ))


def downgrade() -> None:
    for table in CATALOGS:
        op.execute(sa.text(f"DROP POLICY IF EXISTS catalog_admin_write ON {table}"))
        op.execute(sa.text(f"DROP POLICY IF EXISTS catalog_read ON {table}"))
        op.execute(sa.text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))

    for table in reversed(NEW_TABLES):
        op.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation ON {table}"))
        op.execute(sa.text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))

    for child, _column, _parent, name, _on_delete in reversed(COMPOSITE_FKS):
        op.execute(sa.text(f"ALTER TABLE {child} DROP CONSTRAINT IF EXISTS {name}"))

    for table, column, old_index, new_constraint in reversed(PER_ORG_UNIQUES):
        op.drop_constraint(new_constraint, table, type_="unique")
        op.drop_index(old_index, table_name=table)
        # Restoring global uniqueness fails if two organizations now hold the same
        # value: a deliberate stop (data would otherwise have to be discarded).
        op.create_index(old_index, table, [column], unique=True)

    for table in reversed(UNIQUE_ID_ORG):
        op.drop_constraint(f"uq_{table}_id_org", table, type_="unique")

    for table in reversed(NEW_TABLES):
        op.execute(sa.text(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS ck_{table}_org_required"))
        op.drop_index(f"ix_{table}_organization_id", table_name=table)
        op.drop_constraint(f"fk_{table}_organization_id", table, type_="foreignkey")
        op.drop_column(table, "organization_id")
