"""Database engine, sessions and the tenant-isolation plumbing.

Row Level Security (RLS) only protects anything if the connection is NOT a
superuser, does NOT have BYPASSRLS and is not exempt as the table owner. So:

* ``DB_RUNTIME_ROLE`` (default ``ads_app``, created by migration
  ``e3f1a7c2d9b4``) is a plain role without bypass. Every pooled connection
  runs ``SET ROLE`` to it right after connecting. Deployments that connect with
  a dedicated login role can set ``DB_RUNTIME_ROLE=`` (empty) and skip this.
* The organization of the current request lives in the transaction-local
  setting ``app.current_org``. It is (re)written at the BEGIN of every
  transaction of every session -- with an empty value when the session has no
  organization -- so a pooled connection can never carry a previous request's
  tenant over (``set_config(..., true)`` also resets at COMMIT/ROLLBACK).
* ``verify_runtime_role_is_safe`` refuses to start the app on an unsafe role
  or when the tenant tables are not protected by RLS.
"""
import re
from typing import Optional

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, declarative_base, Session

from app.core.config import settings

# PostgreSQL only - SQLite is no longer supported
engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)

_ROLE_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")

# Tables protected by a tenant_isolation RLS policy (see the RLS migration).
TENANT_TABLES = ("brands", "products", "customer_profiles", "brand_profiles")

ORG_SETTING = "app.current_org"


def _runtime_role() -> str:
    role = (settings.DB_RUNTIME_ROLE or "").strip()
    if role and not _ROLE_RE.match(role):
        raise ValueError("DB_RUNTIME_ROLE must be a plain lowercase identifier")
    return role


@event.listens_for(engine, "connect")
def _drop_to_runtime_role(dbapi_connection, _record):
    role = _runtime_role()
    if not role:
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute(f'SET ROLE "{role}"')  # identifier validated by _ROLE_RE
    finally:
        cursor.close()
    dbapi_connection.commit()


@event.listens_for(Session, "after_begin")
def _apply_org_context(session, _transaction, connection):
    """Bind this transaction to the session's organization (or to none)."""
    org_id = session.info.get("org_id") or ""
    connection.execute(
        text("SELECT set_config(:name, :org, true)"),
        {"name": ORG_SETTING, "org": org_id},
    )


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
        unprotected = conn.execute(text(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relname = ANY(:tables) "
            "AND NOT (c.relrowsecurity AND c.relforcerowsecurity)"
        ), {"tables": list(TENANT_TABLES)}).scalars().all()
        missing = set(TENANT_TABLES) - set(
            conn.execute(text(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relname = ANY(:tables)"
            ), {"tables": list(TENANT_TABLES)}).scalars().all()
        )
        if unprotected or missing:
            raise RuntimeError(
                "Tenant tables without enforced row level security: "
                f"{sorted(set(unprotected) | missing)}. Run `alembic upgrade head`."
            )
