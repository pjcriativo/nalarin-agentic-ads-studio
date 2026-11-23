"""Operator tool: create the dedicated, unprivileged LOGIN role of the API.

Why: when the API connects as the database owner and merely runs ``SET ROLE
ads_app``, any SQL executed in that session can run ``RESET ROLE`` and is the
owner (a superuser on most managed PostgreSQL) -- bypassing row level security.
A dedicated LOGIN role (NOSUPERUSER NOBYPASSRLS NOCREATEROLE, member only of the
runtime role) has nothing to escape to. Production REQUIRES it (the API refuses
to start otherwise; see ``settings.secure_db_mode_required``).

Run once per environment with the OWNER connection::

    ADMIN_DATABASE_URL=postgresql://owner... RUNTIME_DB_PASSWORD='<strong secret>' \\
        python -m app.scripts.provision_runtime_login --login-role ads_app_login

then give the API ``RUNTIME_DATABASE_URL=postgresql://ads_app_login:<password>@host:port/db``
(keep ``DATABASE_URL`` = the owner connection for migrations) and ``APP_ENV=production``.
The password is read from the environment, never from the command line, and is
never printed. Re-running rotates the password.
"""
import argparse
import os
import re
import sys

from sqlalchemy import create_engine, text

_ROLE_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def provision(conn, login_role: str, runtime_role: str, password: str) -> None:
    if not _ROLE_RE.match(login_role) or not _ROLE_RE.match(runtime_role):
        raise ValueError("role names must be plain lowercase identifiers")
    if len(password) < 16:
        raise ValueError("RUNTIME_DB_PASSWORD must be at least 16 characters")
    exists = conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": login_role}).first()
    verb = "ALTER" if exists else "CREATE"
    # Identifier and literal are quoted by the SERVER (quote_ident / quote_literal); the
    # statement is then run on the raw DBAPI cursor so no client-side % processing touches it.
    ident, literal = conn.execute(text("SELECT quote_ident(:r), quote_literal(:p)"),
                                  {"r": login_role, "p": password}).one()
    statement = (f"{verb} ROLE {ident} LOGIN PASSWORD {literal} "
                 "NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB NOREPLICATION INHERIT")
    cursor = conn.connection.cursor()
    try:
        cursor.execute(statement)
    finally:
        cursor.close()
    conn.execute(text(f'GRANT "{runtime_role}" TO "{login_role}"'))
    # The login role must reach nothing but the runtime role.
    conn.execute(text(f'REVOKE ALL ON alembic_version FROM "{login_role}"'))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--login-role", default="ads_app_login")
    parser.add_argument("--runtime-role", default=os.getenv("DB_RUNTIME_ROLE", "ads_app"))
    args = parser.parse_args(argv)
    url = os.getenv("ADMIN_DATABASE_URL") or os.getenv("DATABASE_URL")
    password = os.getenv("RUNTIME_DB_PASSWORD", "")
    if not url or not password:
        print("Set ADMIN_DATABASE_URL (owner) and RUNTIME_DB_PASSWORD", file=sys.stderr)
        return 2
    engine = create_engine(url)
    with engine.begin() as conn:
        provision(conn, args.login_role, args.runtime_role, password)
    print(f"login role '{args.login_role}' provisioned (member of '{args.runtime_role}'); "
          "set RUNTIME_DATABASE_URL accordingly. Password not shown.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
