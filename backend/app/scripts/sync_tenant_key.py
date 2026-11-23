"""Operator tool: store the application's tenant-context key in the database.

Use it after changing TENANT_CONTEXT_KEY / SECRET_KEY (the API refuses to start
while the database holds a different key). Needs the OWNER connection (the
runtime role cannot read or write the secret)::

    ADMIN_DATABASE_URL=postgresql://owner... python -m app.scripts.sync_tenant_key
"""
import os
import sys

from sqlalchemy import create_engine, text

from app.core.ctx_key import tenant_context_key


def main() -> int:
    url = os.getenv("ADMIN_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        print("Set ADMIN_DATABASE_URL or DATABASE_URL", file=sys.stderr)
        return 2
    engine = create_engine(url)  # no SET ROLE: owner privileges
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO tenant_ctx_secret (id, secret) VALUES (1, :s) "
            "ON CONFLICT (id) DO UPDATE SET secret = EXCLUDED.secret"
        ), {"s": tenant_context_key()})
    print("tenant context key synchronized")
    return 0


if __name__ == "__main__":
    sys.exit(main())
