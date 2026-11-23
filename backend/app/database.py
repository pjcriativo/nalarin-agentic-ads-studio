"""Database engine, sessions and the tenant-isolation plumbing.

Row Level Security (RLS) only protects anything if the connection is NOT a
superuser, does NOT have BYPASSRLS and is not exempt as the table owner. So:

* ``DB_RUNTIME_ROLE`` (default ``ads_app``, created by migration
  ``e3f1a7c2d9b4``) is a plain role without bypass. Every pooled connection
  runs ``SET ROLE`` to it right after connecting. Deployments that connect with
  a dedicated login role can set ``DB_RUNTIME_ROLE=`` (empty) and skip this.
* The organization of the current request lives in the transaction-local
  settings ``app.current_org`` / ``app.platform_admin``, authenticated by an
  HMAC signature over ``org|admin|nonce`` (``app.ctx_sig``; see
  app/core/ctx_key.py). RLS policies read the context ONLY through the
  SECURITY DEFINER function ``app_current_org()``, which recomputes the
  signature with a key the runtime role cannot read: SQL injected into a
  session can overwrite the settings but cannot forge a valid signature for
  another tenant. The settings are (re)written at the BEGIN of every
  transaction of every session -- empty when the session has no organization --
  so a pooled connection can never carry a previous request's tenant over
  (``set_config(..., true)`` also resets at COMMIT/ROLLBACK).
* ``RUNTIME_DATABASE_URL`` (recommended in production) connects as a dedicated
  unprivileged LOGIN role, so injected SQL cannot even ``RESET ROLE`` to the
  owner. ``REQUIRE_LOGIN_RUNTIME_ROLE=true`` makes the API refuse to start
  without it.
* ``verify_runtime_role_is_safe`` refuses to start the app on an unsafe role
  or when the tenant tables are not protected by RLS.
"""
import re
import uuid
from typing import Optional

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, declarative_base, Session

from app.core.config import settings
from app.core.ctx_key import sign_context

# PostgreSQL only - SQLite is no longer supported.
# The API connects with RUNTIME_DATABASE_URL (unprivileged login) when set;
# DATABASE_URL remains the owner connection used by Alembic and operator tools.
engine = create_engine(settings.RUNTIME_DATABASE_URL or settings.DATABASE_URL, pool_pre_ping=True)

_ROLE_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_ORG_ID_RE = re.compile(r"^[0-9a-fA-F-]{8,64}$")

# Private business tables: organization_id + FORCE RLS `tenant_isolation`.
TENANT_TABLES = (
    "brands", "products", "customer_profiles", "brand_profiles",
    "generated_ads", "winning_ads",
    "facebook_campaigns", "facebook_adsets", "facebook_ads",
    "verticals", "facebook_pages", "saved_searches", "scraped_ads",
    "search_logs", "api_usage_logs", "page_blacklist", "keyword_blacklist",
    "brand_scrapes", "brand_scraped_ads",
)

# Global catalogs: readable by every tenant, writable only with the signed
# platform-admin context (see bind_context(platform_admin=True)).
CATALOG_TABLES = ("prompts", "ad_styles")

# Append-only audit trail (insert for everyone, read only inside the org).
AUDIT_TABLES = ("audit_logs",)

ORG_SETTING = "app.current_org"
ADMIN_SETTING = "app.platform_admin"
NONCE_SETTING = "app.ctx_nonce"
SIG_SETTING = "app.ctx_sig"


def _runtime_role() -> str:
    role = (settings.DB_RUNTIME_ROLE or "").strip()
    if role and not _ROLE_RE.match(role):
        raise ValueError("DB_RUNTIME_ROLE must be a plain lowercase identifier")
    return role


@event.listens_for(engine, "connect")
def _drop_to_runtime_role(dbapi_connection, _record):
    role = _runtime_role()
    if not role or settings.RUNTIME_DATABASE_URL:
        return  # a dedicated login role needs no SET ROLE (and cannot escape it)
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute(f'SET ROLE "{role}"')  # identifier validated by _ROLE_RE
    finally:
        cursor.close()
    dbapi_connection.commit()


def _context_params(org_id: Optional[str], platform_admin: bool) -> dict:
    """Signed context for one transaction (empty/unsigned-equivalent when the
    session has no organization and is not a platform-admin session)."""
    org = org_id or ""
    if org and not _ORG_ID_RE.match(org):
        raise ValueError("Malformed organization id")
    admin = "on" if platform_admin else ""
    nonce = uuid.uuid4().hex
    return {"org": org, "admin": admin, "nonce": nonce, "sig": sign_context(org, admin, nonce)}


_SET_CONTEXT_SQL = text(
    "SELECT set_config('app.current_org', :org, true), set_config('app.platform_admin', :admin, true), "
    "set_config('app.ctx_nonce', :nonce, true), set_config('app.ctx_sig', :sig, true)"
)


@event.listens_for(Session, "after_begin")
def _apply_org_context(session, _transaction, connection):
    """Bind this transaction to the session's (signed) organization, or none."""
    connection.execute(
        _SET_CONTEXT_SQL,
        _context_params(session.info.get("org_id"), bool(session.info.get("platform_admin"))),
    )


def bind_context(db: Session, org_id: Optional[str], platform_admin: bool = False) -> None:
    """Bind ``db`` to an organization (and optionally the platform-admin
    capability) for this and every later transaction of the session."""
    db.info["org_id"] = org_id
    db.info["platform_admin"] = platform_admin
    db.execute(_SET_CONTEXT_SQL, _context_params(org_id, platform_admin))


@event.listens_for(Session, "before_flush")
def _stamp_and_guard_tenant_rows(session, _flush_context, _instances):
    """Defense in depth on top of RLS: new tenant rows get the session's
    organization, and a row that names a different organization is rejected
    before it ever reaches the database."""
    org_id: Optional[str] = session.info.get("org_id")
    for obj in session.new:
        if not hasattr(type(obj), "organization_id") or not hasattr(obj, "organization_id"):
            continue
        if getattr(type(obj), "__tablename__", None) not in TENANT_TABLES:
            continue
        if obj.organization_id is None and org_id:
            obj.organization_id = org_id
        elif org_id and obj.organization_id != org_id:
            raise PermissionError("Cross-organization write blocked")


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def session_org(db: Session) -> Optional[str]:
    """Organization the session is bound to (None when unbound)."""
    return db.info.get("org_id")

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def verify_runtime_role_is_safe(bind=None) -> None:
    """Fail fast when RLS could be bypassed. Called at application startup."""
    bind = bind or engine
    protected = TENANT_TABLES + CATALOG_TABLES + AUDIT_TABLES
    with bind.connect() as conn:
        row = conn.execute(text(
            "SELECT r.rolname, r.rolsuper, r.rolbypassrls FROM pg_roles r WHERE r.rolname = current_user"
        )).one()
        if row.rolsuper or row.rolbypassrls:
            raise RuntimeError(
                f"Unsafe database role '{row.rolname}' (superuser={row.rolsuper}, "
                f"bypassrls={row.rolbypassrls}): row level security would be bypassed. "
                "Run migrations, then set DB_RUNTIME_ROLE to a role without bypass "
                "(default 'ads_app') or connect with such a login role."
            )
        if settings.REQUIRE_LOGIN_RUNTIME_ROLE:
            login = conn.execute(text(
                "SELECT r.rolsuper, r.rolbypassrls FROM pg_roles r WHERE r.rolname = session_user"
            )).one()
            if login.rolsuper or login.rolbypassrls:
                raise RuntimeError(
                    "REQUIRE_LOGIN_RUNTIME_ROLE is set but the connection login role is privileged: "
                    "injected SQL could RESET ROLE and bypass RLS. Set RUNTIME_DATABASE_URL to a "
                    "dedicated unprivileged login role."
                )
        found = {
            name: (rls, forced) for name, rls, forced in conn.execute(text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relname = ANY(:tables)"
            ), {"tables": list(protected)})
        }
        bad = sorted(t for t in protected if t not in found or not (found[t][0] and found[t][1]))
        if bad:
            raise RuntimeError(
                "Tenant tables without enforced row level security: "
                f"{bad}. Run `alembic upgrade head`."
            )

        # The database must accept OUR signature (same key on both sides) and
        # reject a forged one, otherwise every tenant query would silently
        # return nothing (or, worse, trust an unsigned context).
        probe = str(uuid.uuid4())
        params = _context_params(probe, False)
        conn.execute(_SET_CONTEXT_SQL, params)
        accepted = conn.execute(text("SELECT app_current_org()")).scalar()
        conn.execute(text("SELECT set_config('app.ctx_sig', 'forged', true)"))
        forged = conn.execute(text("SELECT app_current_org()")).scalar()
        conn.rollback()
        if accepted != probe:
            raise RuntimeError(
                "The database rejected the signed tenant context: TENANT_CONTEXT_KEY / SECRET_KEY "
                "differs from the key stored by migrations. Run "
                "`python -m app.scripts.sync_tenant_key` with the owner DATABASE_URL."
            )
        if forged is not None:
            raise RuntimeError("The database accepted a forged tenant context signature")
