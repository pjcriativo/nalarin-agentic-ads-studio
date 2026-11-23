"""Operator tool: reconcile LEGACY (pre etapa 01.3) integration data.

Before etapa 01.3 ad-platform connections, API keys and mutation audits belonged to a
USER. The migration keeps every row (encrypted tokens, user links, key hashes) but
leaves ``organization_id = NULL``: QUARANTINE. Quarantined rows authenticate and
authorize nobody -- a legacy API key is refused, a legacy connection is invisible.

Nothing is claimed automatically. A human operator, who knows which organization a
user's integrations really belong to, runs (with a PRIVILEGED database URL, because
the quarantined rows are hidden from the application role)::

    ADMIN_DATABASE_URL=postgresql://owner... python -m app.scripts.reconcile_legacy_integrations report
    python -m app.scripts.reconcile_legacy_integrations claim-connections --org-id ORG --user-id USER   # dry run
    python -m app.scripts.reconcile_legacy_integrations claim-connections --org-id ORG --user-id USER --apply
    python -m app.scripts.reconcile_legacy_integrations claim-api-keys   --org-id ORG --keys ID1,ID2 --apply
    python -m app.scripts.reconcile_legacy_integrations claim-audits     --org-id ORG --user-id USER --apply

Safety rules enforced here (the command REFUSES and changes nothing otherwise):

* the organization must exist and be active;
* the user (the connection owner / key creator) must be an ACTIVE member of that
  organization with a role allowed to manage connections / API keys (admin, owner);
* connections: claiming creates the explicit ``ad_account_links`` rows (``active`` for
  the account that was selected, ``pending`` for the others) -- the organization then
  still controls selection; an account already linked to the organization is a conflict;
* keys: only ``ads:read`` / ``ads:draft`` survive; ``ads:publish`` / ``ads:spend`` are stripped;
* every command is a DRY RUN unless ``--apply`` is given, and is one transaction.
"""
import argparse
import os
import sys
from typing import Dict, List, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

PROVIDER_TABLES = {
    "google_ads": ("google_ads_connections", "customer_id"),
    "meta": ("meta_ads_connections", "ad_account_id"),
    "tiktok": ("tiktok_ads_connections", "advertiser_id"),
}
BOT_SCOPES = {"ads:read", "ads:draft"}
MANAGER_ROLES = ("admin", "owner")


class ReconcileError(Exception):
    pass


def assert_privileged(conn: Connection) -> None:
    row = conn.execute(text(
        "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")).one()
    if not (row.rolsuper or row.rolbypassrls):
        raise ReconcileError(f"Role '{row.rolname}' is subject to row level security and cannot see legacy rows. "
                             "Use a superuser/BYPASSRLS role (ADMIN_DATABASE_URL).")


def report(conn: Connection) -> Dict[str, object]:
    out: Dict[str, object] = {}
    for provider, (table, _col) in PROVIDER_TABLES.items():
        rows = conn.execute(text(
            f"SELECT user_id, count(*) FROM {table} WHERE organization_id IS NULL GROUP BY user_id ORDER BY user_id")).all()
        out[table] = {r[0]: r[1] for r in rows}
    out["api_keys"] = conn.execute(text("SELECT count(*) FROM api_keys WHERE organization_id IS NULL")).scalar()
    out["mutation_audits"] = conn.execute(text("SELECT count(*) FROM mutation_audits WHERE organization_id IS NULL")).scalar()
    return out


def _require_org_and_manager(conn: Connection, org_id: str, user_id: str) -> None:
    org = conn.execute(text("SELECT is_active FROM organizations WHERE id = :o"), {"o": org_id}).first()
    if org is None or not org.is_active:
        raise ReconcileError(f"Organization {org_id} does not exist or is inactive")
    member = conn.execute(text(
        "SELECT role FROM organization_members WHERE organization_id = :o AND user_id = :u AND status = 'active'"),
        {"o": org_id, "u": user_id}).first()
    if member is None:
        raise ReconcileError(f"User {user_id} is not an active member of organization {org_id}")
    if member.role not in MANAGER_ROLES:
        raise ReconcileError(f"User {user_id} has role '{member.role}': only {MANAGER_ROLES} may own integrations")


def claim_connections(conn: Connection, org_id: str, user_id: str, apply: bool = False) -> Dict[str, int]:
    """Assign the user's quarantined connections to ``org_id`` and create their links."""
    assert_privileged(conn)
    _require_org_and_manager(conn, org_id, user_id)
    changed: Dict[str, int] = {}
    try:
        for provider, (table, col) in PROVIDER_TABLES.items():
            norm = {
                "google_ads": f"regexp_replace({col}, '[^0-9]', '', 'g')",
                "meta": f"regexp_replace({col}, '^act_', '')",
                "tiktok": col,
            }[provider]
            rows = conn.execute(text(
                f"SELECT id, {col} AS account, {norm} AS normalized, account_name, is_active FROM {table} "
                "WHERE organization_id IS NULL AND user_id = :u"), {"u": user_id}).all()
            for row in rows:
                conn.execute(text(f"UPDATE {table} SET organization_id = :o WHERE id = :id"), {"o": org_id, "id": row.id})
                conn.execute(text(
                    "INSERT INTO ad_account_links (id, organization_id, provider, external_account_id, account_name, "
                    "status, authorized_by_user_id, authorized_at) VALUES (gen_random_uuid()::text, :o, :p, :a, :n, :s, "
                    ":u, CASE WHEN :s = 'active' THEN now() END)"),
                    {"o": org_id, "p": provider, "a": row.normalized, "n": row.account_name,
                     "s": "active" if row.is_active else "pending", "u": user_id})
            changed[table] = len(rows)
    except Exception as exc:  # unique conflicts (account already linked / connected in the org)
        conn.rollback()
        raise ReconcileError(f"Claim refused, nothing changed: {str(exc).splitlines()[0]}") from exc
    if not apply:
        conn.rollback()
    return changed


def claim_api_keys(conn: Connection, org_id: str, key_ids: List[str], apply: bool = False) -> int:
    assert_privileged(conn)
    changed = 0
    for key_id in key_ids:
        key = conn.execute(text(
            "SELECT id, scopes, created_by_user_id FROM api_keys WHERE id = :id AND organization_id IS NULL"),
            {"id": key_id}).first()
        if key is None:
            raise ReconcileError(f"API key {key_id} not found or already bound to an organization")
        if not key.created_by_user_id:
            raise ReconcileError(f"API key {key_id} has no creator: refusing to guess an owner")
        _require_org_and_manager(conn, org_id, key.created_by_user_id)
        scopes = sorted(set(key.scopes or []) & BOT_SCOPES)
        conn.execute(text("UPDATE api_keys SET organization_id = :o, scopes = CAST(:s AS json) WHERE id = :id"),
                     {"o": org_id, "s": __import__("json").dumps(scopes), "id": key_id})
        changed += 1
    if not apply:
        conn.rollback()
    return changed


def claim_audits(conn: Connection, org_id: str, user_id: str, apply: bool = False) -> int:
    assert_privileged(conn)
    _require_org_and_manager(conn, org_id, user_id)
    changed = conn.execute(text(
        "UPDATE mutation_audits SET organization_id = :o WHERE organization_id IS NULL AND user_id = :u"),
        {"o": org_id, "u": user_id}).rowcount
    if not apply:
        conn.rollback()
    return changed


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("report")
    c = sub.add_parser("claim-connections")
    c.add_argument("--org-id", required=True)
    c.add_argument("--user-id", required=True)
    c.add_argument("--apply", action="store_true")
    k = sub.add_parser("claim-api-keys")
    k.add_argument("--org-id", required=True)
    k.add_argument("--keys", required=True, help="comma separated api key ids")
    k.add_argument("--apply", action="store_true")
    a = sub.add_parser("claim-audits")
    a.add_argument("--org-id", required=True)
    a.add_argument("--user-id", required=True)
    a.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    url = os.getenv("ADMIN_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        print("Set ADMIN_DATABASE_URL (a privileged role) or DATABASE_URL", file=sys.stderr)
        return 2
    engine = create_engine(url)  # plain engine: NO SET ROLE
    try:
        with engine.connect() as conn:
            if args.cmd == "report":
                assert_privileged(conn)
                for name, value in report(conn).items():
                    print(f"{name:28s} {value}")
                return 0
            if args.cmd == "claim-connections":
                result = claim_connections(conn, args.org_id, args.user_id, args.apply)
            elif args.cmd == "claim-api-keys":
                result = claim_api_keys(conn, args.org_id, [x.strip() for x in args.keys.split(",") if x.strip()], args.apply)
            else:
                result = claim_audits(conn, args.org_id, args.user_id, args.apply)
            print(result)
            if args.apply:
                conn.commit()
            else:
                print("Dry run: nothing was written. Re-run with --apply.")
            return 0
    except ReconcileError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
