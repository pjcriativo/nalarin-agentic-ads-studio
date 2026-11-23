"""row level security for the tenant tables (3/3)

Revision ID: e3f1a7c2d9b4
Revises: e2b3c4d5e6f7
Create Date: 2026-11-23

* Creates the unprivileged runtime role (default ``ads_app``; override with
  DB_RUNTIME_ROLE): NOLOGIN, NOSUPERUSER, NOBYPASSRLS. The application drops to
  it with SET ROLE on every connection (app/database.py), so RLS applies even
  when the login role is a superuser (as the stock postgres user is).
* Grants it plain DML on the schema (and default privileges for tables created
  by future migrations) -- but not on alembic_version.
* Enables AND FORCES row level security on the tenant tables, with one policy:
  a row is visible/writable only when its organization_id equals the
  transaction-local setting ``app.current_org``. No setting (or an empty one)
  matches nothing: the policy fails closed. Legacy rows (organization_id NULL)
  therefore match nothing either.

Operational notes: FORCE means even the table owner is filtered, so data
migrations on these tables must run as a superuser/BYPASSRLS role (the legacy
claim tool does), and RLS never applies to the ORIGINAL login role if it is a
superuser -- which is exactly why the app does not run as it.
"""
import os
import re
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e3f1a7c2d9b4"
down_revision: Union[str, Sequence[str], None] = "e2b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = ("brands", "products", "customer_profiles", "brand_profiles")
_ROLE_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_PREDICATE = "organization_id = (SELECT NULLIF(current_setting('app.current_org', true), ''))"


def _runtime_role() -> str:
    role = os.getenv("DB_RUNTIME_ROLE", "ads_app").strip()
    if role and not _ROLE_RE.match(role):
        raise ValueError("DB_RUNTIME_ROLE must be a plain lowercase identifier")
    return role


def upgrade() -> None:
    role = _runtime_role()
    if role:
        op.execute(sa.text(f"""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                    CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
                END IF;
                IF NOT pg_has_role(current_user, '{role}', 'MEMBER') THEN
                    EXECUTE format('GRANT %I TO %I', '{role}', current_user);
                END IF;
            END
            $$
        """))
        op.execute(sa.text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
        op.execute(sa.text(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{role}"'))
        op.execute(sa.text(
            f'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{role}"'
        ))
        op.execute(sa.text(f'REVOKE ALL ON alembic_version FROM "{role}"'))

    for table in TENANT_TABLES:
        op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(
            f"CREATE POLICY tenant_isolation ON {table} USING ({_PREDICATE}) WITH CHECK ({_PREDICATE})"
        ))


def downgrade() -> None:
    for table in reversed(TENANT_TABLES):
        op.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation ON {table}"))
        op.execute(sa.text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))
    # The runtime role and its grants are cluster-level objects that other
    # databases may share; they are intentionally left in place.
