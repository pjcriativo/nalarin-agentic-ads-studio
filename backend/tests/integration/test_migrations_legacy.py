"""Migrations from an empty database and from a database holding LEGACY data.

A throw-away database (name ends in ``_test``) is created on the disposable
PostgreSQL server, migrated to the last pre-tenant revision, filled with legacy
rows, upgraded to head and inspected. Nothing here touches any other database.
"""
import os
import uuid
from pathlib import Path
from urllib.parse import urlparse

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.core.config import settings
from app.scripts.claim_legacy_data import ClaimError, assert_privileged, claim, report

PRE_TENANT = "a1d2e3f4b5c6"
BACKEND = Path(__file__).resolve().parent.parent.parent


def _alembic_cfg():
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    return cfg


@pytest.fixture
def scratch_db(monkeypatch):
    """Create ``ads_legacy_<rand>_test`` next to the test database; drop it afterwards."""
    base = make_url(settings.DATABASE_URL)
    name = f"ads_legacy_{uuid.uuid4().hex[:8]}_test"
    assert name.endswith("_test") and base.database.endswith("_test")
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = base.set(database=name)
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", url.render_as_string(hide_password=False))
    engine = create_engine(url)
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def _legacy_rows(engine):
    """Two users and pre-tenant business data owned by nobody in particular."""
    with engine.begin() as conn:
        for uid, email in (("u-legacy-1", "legacy1@example.com"), ("u-legacy-2", "legacy2@example.com")):
            conn.execute(text(
                "INSERT INTO users (id, email, hashed_password, name, is_active, is_superuser) "
                "VALUES (:i, :e, 'x', :n, true, false)"), {"i": uid, "e": email, "n": uid})
        conn.execute(text("INSERT INTO brands (id, name) VALUES ('b-1','Legacy Brand 1'), ('b-2','Legacy Brand 2')"))
        conn.execute(text("INSERT INTO products (id, brand_id, name) VALUES ('p-1','b-1','Prod 1'), ('p-2','b-2','Prod 2')"))
        conn.execute(text("INSERT INTO customer_profiles (id, name) VALUES ('c-1','Profile 1'), ('c-2','Profile 2')"))
        conn.execute(text("INSERT INTO brand_profiles (brand_id, profile_id) VALUES ('b-1','c-1'), ('b-2','c-2')"))


class TestFromEmptyDatabase:
    def test_upgrade_to_head_downgrade_and_upgrade_again(self, scratch_db):
        cfg = _alembic_cfg()
        command.upgrade(cfg, "head")
        with scratch_db.connect() as conn:
            tables = set(text_ for (text_,) in conn.execute(text(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")))
            assert {"organizations", "organization_members", "brands"} <= tables
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "e3f1a7c2d9b4"
        command.downgrade(cfg, PRE_TENANT)
        with scratch_db.connect() as conn:
            tables = set(t for (t,) in conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'")))
            assert "organizations" not in tables
            cols = {c for (c,) in conn.execute(text(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'brands'"))}
            assert "organization_id" not in cols
        command.upgrade(cfg, "head")  # and forward again

    def test_baseline_is_a_frozen_snapshot_independent_of_current_models(self):
        import ast
        path = BACKEND / "alembic" / "versions" / "fba8f217905e_initial_schema_baseline.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        imported += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        calls = [n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
        assert not [m for m in imported if m.startswith("app")], imported   # never imports the live models
        assert "create_all" not in calls and "drop_all" not in calls
        assert "organization" not in path.read_text(encoding="utf-8").split('"""')[2]  # DDL has no tenant objects


class TestLegacyData:
    @pytest.fixture
    def migrated(self, scratch_db):
        cfg = _alembic_cfg()
        command.upgrade(cfg, PRE_TENANT)
        _legacy_rows(scratch_db)
        command.upgrade(cfg, "head")
        return scratch_db

    def test_existing_users_are_preserved_with_a_personal_owned_organization(self, migrated):
        with migrated.connect() as conn:
            users = conn.execute(text("SELECT id FROM users ORDER BY id")).scalars().all()
            assert users == ["u-legacy-1", "u-legacy-2"]
            rows = conn.execute(text(
                "SELECT m.user_id, m.role, m.status, o.name FROM organization_members m "
                "JOIN organizations o ON o.id = m.organization_id ORDER BY m.user_id")).all()
        assert [(r[0], r[1], r[2]) for r in rows] == [("u-legacy-1", "owner", "active"), ("u-legacy-2", "owner", "active")]
        assert all(r[3].endswith("(personal)") for r in rows)

    def test_legacy_rows_are_not_assigned_to_anyone(self, migrated):
        with migrated.connect() as conn:
            counts = report(conn)
        assert counts == {"brands": 2, "products": 2, "customer_profiles": 2, "brand_profiles": 2}

    def test_legacy_rows_are_invisible_to_every_organization(self, migrated):
        engine = create_engine(migrated.url)
        from sqlalchemy import event
        from app.database import _drop_to_runtime_role
        event.listen(engine, "connect", _drop_to_runtime_role)
        with engine.connect() as conn:
            org_ids = conn.execute(text("SELECT id FROM organizations")).scalars().all()
        assert len(org_ids) == 2
        for org_id in org_ids:
            with engine.begin() as conn:
                conn.execute(text("SELECT set_config('app.current_org', :o, true)"), {"o": org_id})
                for table in ("brands", "products", "customer_profiles", "brand_profiles"):
                    assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0, table
        engine.dispose()

    def test_claim_is_a_dry_run_by_default_and_applies_only_on_request(self, migrated):
        with migrated.connect() as conn:
            org_id = conn.execute(text("SELECT id FROM organizations ORDER BY slug LIMIT 1")).scalar()
            would = claim(conn, org_id, ["b-1"], ["c-1"], apply=False)
            assert would == {"brands": 1, "customer_profiles": 1, "products": 1, "brand_profiles": 1}
            assert report(conn)["brands"] == 2  # nothing written
            done = claim(conn, org_id, ["b-1"], ["c-1"], apply=True)
            conn.commit()
            assert done == would
            rest = report(conn)
        assert rest == {"brands": 1, "products": 1, "customer_profiles": 1, "brand_profiles": 1}

    def test_claimed_rows_become_visible_to_that_organization_only(self, migrated):
        with migrated.connect() as conn:
            org_a, org_b = conn.execute(text("SELECT id FROM organizations ORDER BY slug")).scalars().all()
            claim(conn, org_a, ["b-1"], ["c-1"], apply=True)
            conn.commit()
        engine = create_engine(migrated.url)
        from sqlalchemy import event
        from app.database import _drop_to_runtime_role
        event.listen(engine, "connect", _drop_to_runtime_role)
        seen = {}
        for label, org in (("a", org_a), ("b", org_b)):
            with engine.begin() as conn:
                conn.execute(text("SELECT set_config('app.current_org', :o, true)"), {"o": org})
                seen[label] = (conn.execute(text("SELECT count(*) FROM brands")).scalar(),
                               conn.execute(text("SELECT count(*) FROM products")).scalar())
        engine.dispose()
        assert seen == {"a": (1, 1), "b": (0, 0)}

    def test_claim_refuses_to_split_a_brand_from_its_linked_profile(self, migrated):
        with migrated.connect() as conn:
            org_a = conn.execute(text("SELECT id FROM organizations ORDER BY slug LIMIT 1")).scalar()
            with pytest.raises(ClaimError, match="cross organizations"):
                claim(conn, org_a, ["b-1"], [], apply=True)
            conn.rollback()
            assert report(conn)["brands"] == 2

    def test_claim_rejects_unknown_ids_and_unknown_organizations(self, migrated):
        with migrated.connect() as conn:
            org_a = conn.execute(text("SELECT id FROM organizations LIMIT 1")).scalar()
            with pytest.raises(ClaimError, match="not found or already assigned"):
                claim(conn, org_a, ["nope"], [], apply=True)
            conn.rollback()
            with pytest.raises(ClaimError, match="does not exist"):
                claim(conn, str(uuid.uuid4()), ["b-1"], ["c-1"], apply=True)

    def test_claim_cannot_run_as_the_unprivileged_runtime_role(self, migrated):
        engine = create_engine(migrated.url)
        from sqlalchemy import event
        from app.database import _drop_to_runtime_role
        event.listen(engine, "connect", _drop_to_runtime_role)
        with engine.connect() as conn:
            with pytest.raises(ClaimError, match="subject to row level security"):
                assert_privileged(conn)
        engine.dispose()

    def test_already_assigned_rows_cannot_be_claimed_again(self, migrated):
        with migrated.connect() as conn:
            org_a, org_b = conn.execute(text("SELECT id FROM organizations ORDER BY slug")).scalars().all()
            claim(conn, org_a, ["b-1"], ["c-1"], apply=True)
            conn.commit()
            with pytest.raises(ClaimError, match="already assigned"):
                claim(conn, org_b, ["b-1"], ["c-1"], apply=True)
            conn.rollback()

    def test_not_valid_check_blocks_new_ownerless_rows_but_not_legacy_ones(self, migrated):
        with migrated.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM brands WHERE organization_id IS NULL")).scalar() == 2
            with pytest.raises(Exception, match="ck_brands_org_required"):
                conn.execute(text("INSERT INTO brands (id, name) VALUES ('new-ownerless','x')"))
            conn.rollback()
