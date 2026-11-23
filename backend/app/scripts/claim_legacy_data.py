"""Operator tool: assign LEGACY (pre multi-tenant) records to an organization.

Why this exists
---------------
Brands, products and customer profiles created before multi-tenancy have no
owner (organization_id IS NULL). The migrations deliberately do NOT guess an
owner -- that could hand one customer's private data to another -- and row
level security makes NULL-organization rows invisible to every organization.
A human operator who KNOWS who owns what runs this tool to claim them.

Usage (from backend/, with a PRIVILEGED database URL: superuser or BYPASSRLS,
because the rows to be claimed are hidden from the application's own role)::

    ADMIN_DATABASE_URL=postgresql://... python -m app.scripts.claim_legacy_data report
    python -m app.scripts.claim_legacy_data claim --org-id ORG --brands all --profiles all
    python -m app.scripts.claim_legacy_data claim --org-id ORG --brands id1,id2 --profiles id3 --apply

``claim`` is a DRY RUN unless ``--apply`` is given. Claiming brands brings
along their products and their brand<->profile links. It refuses (changing
nothing) when a claimed brand is linked to a profile that is not being claimed
into the same organization, because a link must never cross organizations.
"""
import argparse
import os
import sys
from typing import Dict, Iterable, List, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

TABLES = ("brands", "products", "customer_profiles", "brand_profiles")
# Etapa 01.2 tables, PARENTS FIRST (composite foreign keys: a child can only be
# claimed into an organization once its parent already belongs to it).
OTHER_TABLES = (
    "winning_ads", "facebook_campaigns", "facebook_adsets", "facebook_ads", "generated_ads",
    "verticals", "facebook_pages", "saved_searches", "scraped_ads", "search_logs",
    "api_usage_logs", "page_blacklist", "keyword_blacklist", "brand_scrapes", "brand_scraped_ads",
)
ALL_TABLES = TABLES + OTHER_TABLES


class ClaimError(Exception):
    pass


def assert_privileged(conn: Connection) -> None:
    row = conn.execute(text(
        "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
    )).one()
    if not (row.rolsuper or row.rolbypassrls):
        raise ClaimError(
            f"Role '{row.rolname}' is subject to row level security and cannot see legacy rows. "
            "Use a superuser/BYPASSRLS role (ADMIN_DATABASE_URL)."
        )


def report(conn: Connection) -> Dict[str, int]:
    """Number of unassigned (organization_id IS NULL) rows per tenant table."""
    return {
        table: conn.execute(text(f"SELECT count(*) FROM {table} WHERE organization_id IS NULL")).scalar()
        for table in ALL_TABLES
    }


def claim_tables(conn: Connection, org_id: str, tables: Iterable[str], apply: bool = False) -> Dict[str, int]:
    """Assign EVERY quarantined row of the given (etapa 01.2) tables to ``org_id``.

    Explicit operator action, dry run unless ``apply``. Tables are processed
    parents-first; a child whose parent is still ownerless or belongs to another
    organization is rejected by the composite foreign keys and the whole claim
    is rolled back (nothing is half-assigned).
    """
    assert_privileged(conn)
    wanted = list(tables)
    unknown = [t for t in wanted if t not in OTHER_TABLES]
    if unknown:
        raise ClaimError(f"Not claimable here (use `claim` for brands/products/profiles): {unknown}")
    org = conn.execute(text("SELECT is_active FROM organizations WHERE id = :o"), {"o": org_id}).first()
    if org is None or not org.is_active:
        raise ClaimError(f"Organization {org_id} does not exist or is inactive")
    changed: Dict[str, int] = {}
    try:
        for table in [t for t in OTHER_TABLES if t in wanted]:
            changed[table] = conn.execute(
                text(f"UPDATE {table} SET organization_id = :org WHERE organization_id IS NULL"), {"org": org_id}
            ).rowcount
    except Exception as exc:  # FK / unique violation: refuse, change nothing
        conn.rollback()
        raise ClaimError(f"Claim refused, nothing changed: {str(exc).splitlines()[0]}") from exc
    if not apply:
        conn.rollback()
    return changed


def _resolve_ids(conn: Connection, table: str, wanted: Optional[Iterable[str]]) -> List[str]:
    """``wanted`` None -> nothing; ['all'] -> every unassigned row; else the given ids."""
    if wanted is None:
        return []
    wanted = list(wanted)
    unassigned = set(conn.execute(text(f"SELECT id FROM {table} WHERE organization_id IS NULL")).scalars())
    if wanted == ["all"]:
        return sorted(unassigned)
    missing = [i for i in wanted if i not in unassigned]
    if missing:
        raise ClaimError(f"{table}: not found or already assigned: {missing}")
    return wanted


def claim(
    conn: Connection,
    org_id: str,
    brand_ids: Optional[Iterable[str]] = None,
    profile_ids: Optional[Iterable[str]] = None,
    apply: bool = False,
) -> Dict[str, int]:
    """Assign legacy rows to ``org_id``. Runs in the caller's transaction and
    rolls the work back unless ``apply`` is true. Returns rows changed per table."""
    assert_privileged(conn)
    org = conn.execute(text("SELECT is_active FROM organizations WHERE id = :o"), {"o": org_id}).first()
    if org is None or not org.is_active:
        raise ClaimError(f"Organization {org_id} does not exist or is inactive")

    brands = _resolve_ids(conn, "brands", brand_ids)
    profiles = _resolve_ids(conn, "customer_profiles", profile_ids)

    # A claimed brand may only be linked to profiles that end up in the same organization.
    if brands:
        stray = conn.execute(text(
            "SELECT bp.brand_id, bp.profile_id FROM brand_profiles bp "
            "WHERE bp.brand_id = ANY(CAST(:brands AS text[])) AND bp.organization_id IS NULL "
            "AND NOT (bp.profile_id = ANY(CAST(:profiles AS text[])))"
        ), {"brands": brands, "profiles": profiles}).all()
        if stray:
            raise ClaimError(
                "These brand<->profile links would cross organizations (claim the profile too, "
                f"or detach the link first): {[(b, p) for b, p in stray]}"
            )

    changed: Dict[str, int] = {}
    params = {"org": org_id, "brands": brands, "profiles": profiles}
    # Order matters for the composite foreign keys: parents before children.
    changed["brands"] = conn.execute(text(
        "UPDATE brands SET organization_id = :org WHERE id = ANY(CAST(:brands AS text[])) AND organization_id IS NULL"
    ), params).rowcount
    changed["customer_profiles"] = conn.execute(text(
        "UPDATE customer_profiles SET organization_id = :org WHERE id = ANY(CAST(:profiles AS text[])) AND organization_id IS NULL"
    ), params).rowcount
    changed["products"] = conn.execute(text(
        "UPDATE products SET organization_id = :org WHERE brand_id = ANY(CAST(:brands AS text[])) AND organization_id IS NULL"
    ), params).rowcount
    changed["brand_profiles"] = conn.execute(text(
        "UPDATE brand_profiles SET organization_id = :org "
        "WHERE brand_id = ANY(CAST(:brands AS text[])) AND profile_id = ANY(CAST(:profiles AS text[])) AND organization_id IS NULL"
    ), params).rowcount

    if not apply:
        conn.rollback()
    return changed


def _csv_or_all(value: Optional[str]) -> Optional[List[str]]:
    if value is None:
        return None
    value = value.strip()
    return ["all"] if value == "all" else [v.strip() for v in value.split(",") if v.strip()]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("report", help="count unassigned legacy rows")
    c = sub.add_parser("claim", help="assign legacy rows to an organization (dry run by default)")
    c.add_argument("--org-id", required=True)
    c.add_argument("--brands", help="'all' or comma separated brand ids")
    c.add_argument("--profiles", help="'all' or comma separated customer profile ids")
    c.add_argument("--apply", action="store_true", help="really write (default: dry run)")
    t = sub.add_parser("claim-tables", help="assign all quarantined rows of the etapa 01.2 tables (dry run by default)")
    t.add_argument("--org-id", required=True)
    t.add_argument("--tables", required=True, help="comma separated table names")
    t.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    url = os.getenv("ADMIN_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        print("Set ADMIN_DATABASE_URL (a privileged role) or DATABASE_URL", file=sys.stderr)
        return 2
    engine = create_engine(url)  # plain engine: NO SET ROLE, so legacy rows are reachable
    try:
        with engine.connect() as conn:
            if args.cmd == "report":
                assert_privileged(conn)
                for table, n in report(conn).items():
                    print(f"{table:20s} unassigned: {n}")
                return 0
            if args.cmd == "claim-tables":
                changed = claim_tables(conn, args.org_id, [x.strip() for x in args.tables.split(",") if x.strip()], args.apply)
                for table, n in changed.items():
                    print(f"{table:20s} {'assigned' if args.apply else 'would assign'}: {n}")
                if args.apply:
                    conn.commit()
                else:
                    print("Dry run: nothing was written. Re-run with --apply.")
                return 0
            changed = claim(conn, args.org_id, _csv_or_all(args.brands), _csv_or_all(args.profiles), args.apply)
            for table, n in changed.items():
                print(f"{table:20s} {'assigned' if args.apply else 'would assign'}: {n}")
            if args.apply:
                conn.commit()
            else:
                print("Dry run: nothing was written. Re-run with --apply.")
            return 0
    except ClaimError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
