"""add organization_id to brands, products, customer_profiles, brand_profiles (2/3)

Revision ID: e2b3c4d5e6f7
Revises: d4a9c1e7b2f0
Create Date: 2026-11-23

Legacy rows are NOT assigned to any organization (organization_id stays NULL):
we cannot know which tenant owns them and guessing would hand private data to
the wrong customer. Instead:

* ``CHECK (organization_id IS NOT NULL) NOT VALID`` is added to every table:
  it is enforced for every NEW or UPDATED row but not checked against the
  legacy rows already stored. After the operator has claimed (or discarded)
  the legacy rows, a later migration runs ``VALIDATE CONSTRAINT`` and makes the
  columns NOT NULL.
* Composite foreign keys make cross-organization links impossible at the
  database level (a product can only belong to a brand of its own
  organization, and a brand/profile link only joins rows of one organization).
  They are MATCH SIMPLE, so legacy NULL-organization rows are unaffected.
* A BEFORE INSERT trigger copies the organization of the brand into new
  brand_profiles rows (the ORM inserts that association table implicitly).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e2b3c4d5e6f7"
down_revision: Union[str, Sequence[str], None] = "d4a9c1e7b2f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ("brands", "products", "customer_profiles", "brand_profiles")


def upgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column("organization_id", sa.String(), nullable=True))
        op.create_foreign_key(
            f"fk_{table}_organization_id", table, "organizations",
            ["organization_id"], ["id"], ondelete="RESTRICT",
        )
        op.create_index(f"ix_{table}_organization_id", table, ["organization_id"])
        op.execute(sa.text(
            f"ALTER TABLE {table} ADD CONSTRAINT ck_{table}_org_required "
            "CHECK (organization_id IS NOT NULL) NOT VALID"
        ))

    op.create_unique_constraint("uq_brands_id_org", "brands", ["id", "organization_id"])
    op.create_unique_constraint("uq_customer_profiles_id_org", "customer_profiles", ["id", "organization_id"])

    op.create_foreign_key(
        "fk_products_brand_org", "products", "brands",
        ["brand_id", "organization_id"], ["id", "organization_id"], ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_brand_profiles_brand_org", "brand_profiles", "brands",
        ["brand_id", "organization_id"], ["id", "organization_id"], ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_brand_profiles_profile_org", "brand_profiles", "customer_profiles",
        ["profile_id", "organization_id"], ["id", "organization_id"], ondelete="CASCADE",
    )

    op.execute(sa.text("""
        CREATE FUNCTION brand_profiles_set_org() RETURNS trigger AS $$
        BEGIN
            IF NEW.organization_id IS NULL THEN
                SELECT b.organization_id INTO NEW.organization_id FROM brands b WHERE b.id = NEW.brand_id;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """))
    op.execute(sa.text(
        "CREATE TRIGGER trg_brand_profiles_set_org BEFORE INSERT ON brand_profiles "
        "FOR EACH ROW EXECUTE FUNCTION brand_profiles_set_org()"
    ))


def downgrade() -> None:
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_brand_profiles_set_org ON brand_profiles"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS brand_profiles_set_org()"))
    op.drop_constraint("fk_brand_profiles_profile_org", "brand_profiles", type_="foreignkey")
    op.drop_constraint("fk_brand_profiles_brand_org", "brand_profiles", type_="foreignkey")
    op.drop_constraint("fk_products_brand_org", "products", type_="foreignkey")
    op.drop_constraint("uq_customer_profiles_id_org", "customer_profiles", type_="unique")
    op.drop_constraint("uq_brands_id_org", "brands", type_="unique")
    for table in reversed(TABLES):
        op.execute(sa.text(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS ck_{table}_org_required"))
        op.drop_index(f"ix_{table}_organization_id", table_name=table)
        op.drop_constraint(f"fk_{table}_organization_id", table, type_="foreignkey")
        op.drop_column(table, "organization_id")
