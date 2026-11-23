"""signed tenant context, platform-admin capability and audit log

Revision ID: f1a2b3c4d5e6
Revises: e3f1a7c2d9b4
Create Date: 2026-11-24

RLS used to trust the raw ``app.current_org`` setting, which any SQL executed
in the session (e.g. through an injection bug) could overwrite. From here on
policies call ``app_current_org()``, a SECURITY DEFINER function that only
honours the setting when it carries a valid HMAC signature
(``app.ctx_sig`` over ``org|platform_admin|nonce``) made with a key stored in
``tenant_ctx_secret`` -- a table the runtime role cannot read. The application
holds the same key (TENANT_CONTEXT_KEY, or derived from SECRET_KEY).

Also creates the append-only ``audit_logs`` table (member changes, denied
access) and re-points the four existing tenant policies at the signed context.
"""
import os
import re
import sys
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from app.core.ctx_key import tenant_context_key  # noqa: E402  (stdlib-only module)

revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "e3f1a7c2d9b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_TABLES = ("brands", "products", "customer_profiles", "brand_profiles")
_ROLE_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_OLD_PREDICATE = "organization_id = (SELECT NULLIF(current_setting('app.current_org', true), ''))"
_NEW_PREDICATE = "organization_id = (SELECT app_current_org())"


def _runtime_role() -> str:
    role = os.getenv("DB_RUNTIME_ROLE", "ads_app").strip()
    if role and not _ROLE_RE.match(role):
        raise ValueError("DB_RUNTIME_ROLE must be a plain lowercase identifier")
    return role


def upgrade() -> None:
    role = _runtime_role()
    bind = op.get_bind()

    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    ext_schema = bind.execute(sa.text(
        "SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace "
        "WHERE e.extname = 'pgcrypto'"
    )).scalar()
    if not ext_schema or not re.match(r"^[a-z_][a-z0-9_]*$", ext_schema):
        raise RuntimeError("pgcrypto is required for the signed tenant context")

    # --- secret (never readable by the runtime role) ------------------------
    op.execute(sa.text(
        "CREATE TABLE tenant_ctx_secret (id smallint PRIMARY KEY CHECK (id = 1), secret text NOT NULL)"
    ))
    op.execute(sa.text("INSERT INTO tenant_ctx_secret (id, secret) VALUES (1, :s)").bindparams(s=tenant_context_key()))
    op.execute(sa.text("ALTER TABLE tenant_ctx_secret ENABLE ROW LEVEL SECURITY"))  # no policy: deny by default
    op.execute(sa.text("REVOKE ALL ON tenant_ctx_secret FROM PUBLIC"))
    if role:
        # ALTER DEFAULT PRIVILEGES from the previous migration auto-granted DML.
        op.execute(sa.text(f'REVOKE ALL ON tenant_ctx_secret FROM "{role}"'))

    # --- verification functions ---------------------------------------------
    op.execute(sa.text(f"""
        CREATE FUNCTION app_ctx_valid() RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $$
            SELECT coalesce(
                encode({ext_schema}.hmac(
                    convert_to(
                        coalesce(current_setting('app.current_org', true), '') || '|' ||
                        coalesce(current_setting('app.platform_admin', true), '') || '|' ||
                        coalesce(current_setting('app.ctx_nonce', true), ''), 'UTF8'),
                    convert_to((SELECT secret FROM public.tenant_ctx_secret WHERE id = 1), 'UTF8'),
                    'sha256'), 'hex') = current_setting('app.ctx_sig', true),
                false)
        $$
    """))
    op.execute(sa.text("""
        CREATE FUNCTION app_current_org() RETURNS text
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $$
            SELECT CASE WHEN public.app_ctx_valid()
                        THEN NULLIF(current_setting('app.current_org', true), '') END
        $$
    """))
    op.execute(sa.text("""
        CREATE FUNCTION app_is_platform_admin() RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $$
            SELECT public.app_ctx_valid() AND coalesce(current_setting('app.platform_admin', true), '') = 'on'
        $$
    """))
    for fn in ("app_ctx_valid()", "app_current_org()", "app_is_platform_admin()"):
        op.execute(sa.text(f"REVOKE ALL ON FUNCTION {fn} FROM PUBLIC"))
        if role:
            op.execute(sa.text(f'GRANT EXECUTE ON FUNCTION {fn} TO "{role}"'))
        else:
            op.execute(sa.text(f"GRANT EXECUTE ON FUNCTION {fn} TO PUBLIC"))

    # --- existing tenant policies now use the signed context ------------------
    for table in OLD_TABLES:
        op.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation ON {table}"))
        op.execute(sa.text(
            f"CREATE POLICY tenant_isolation ON {table} USING ({_NEW_PREDICATE}) WITH CHECK ({_NEW_PREDICATE})"
        ))

    # --- audit log -----------------------------------------------------------
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), nullable=True),
        sa.Column("actor_user_id", sa.String(), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("outcome", sa.String(), nullable=False),
        sa.Column("target", sa.String(), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("ip_address", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_audit_logs_organization_id", "audit_logs", ["organization_id"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])
    op.execute(sa.text("ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE audit_logs FORCE ROW LEVEL SECURITY"))
    # Denied attempts may be recorded by a session that has no (valid) organization;
    # successful events must be recorded inside the organization they belong to.
    op.execute(sa.text(
        "CREATE POLICY audit_insert ON audit_logs FOR INSERT "
        "WITH CHECK (outcome = 'denied' OR organization_id = (SELECT app_current_org()))"
    ))
    op.execute(sa.text(
        "CREATE POLICY audit_select ON audit_logs FOR SELECT USING (organization_id = (SELECT app_current_org()))"
    ))
    if role:
        # Append-only for the application.
        op.execute(sa.text(f'REVOKE UPDATE, DELETE, TRUNCATE ON audit_logs FROM "{role}"'))


def downgrade() -> None:
    role = _runtime_role()
    op.drop_table("audit_logs")
    for table in OLD_TABLES:
        op.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation ON {table}"))
        op.execute(sa.text(
            f"CREATE POLICY tenant_isolation ON {table} USING ({_OLD_PREDICATE}) WITH CHECK ({_OLD_PREDICATE})"
        ))
    for fn in ("app_is_platform_admin()", "app_current_org()", "app_ctx_valid()"):
        op.execute(sa.text(f"DROP FUNCTION IF EXISTS {fn}"))
    op.execute(sa.text("DROP TABLE IF EXISTS tenant_ctx_secret"))
