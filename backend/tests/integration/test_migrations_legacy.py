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
from app.scripts.claim_legacy_data import ClaimError, assert_privileged, claim, claim_tables, report, ALL_TABLES
from app.scripts import reconcile_legacy_integrations as recon

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
        # etapa 01.2 data: creatives, templates, campaigns, research, logs, blacklists, scrapes
        conn.execute(text("INSERT INTO winning_ads (id, name, image_url) VALUES ('w-1','Tpl','/uploads/a.png')"))
        conn.execute(text("INSERT INTO generated_ads (id, brand_id, product_id, template_id, headline) "
                          "VALUES ('g-1','b-1','p-1','w-1','Legacy ad')"))
        conn.execute(text("INSERT INTO facebook_campaigns (id, name, objective, budget_type) VALUES ('fc-1','C','O','ABO')"))
        conn.execute(text("INSERT INTO facebook_adsets (id, campaign_id, name, optimization_goal) VALUES ('fa-1','fc-1','S','G')"))
        conn.execute(text("INSERT INTO facebook_ads (id, adset_id, name) VALUES ('fd-1','fa-1','Ad')"))
        conn.execute(text("INSERT INTO verticals (id, name) VALUES ('v-1','Legal')"))
        conn.execute(text("INSERT INTO saved_searches (id, query, vertical_id) VALUES ('s-1','q','v-1')"))
        conn.execute(text("INSERT INTO facebook_pages (id, page_name) VALUES ('fp-1','Page')"))
        conn.execute(text("INSERT INTO scraped_ads (id, ad_link, search_id, facebook_page_id, content_hash) "
                          "VALUES ('sa-1','http://l','s-1','fp-1','h1')"))
        conn.execute(text("INSERT INTO search_logs (id, search_query, date) VALUES ('sl-1','q','2026-01-01')"))
        conn.execute(text("INSERT INTO api_usage_logs (id, endpoint, api_calls, ads_returned, ads_saved, date) "
                          "VALUES ('al-1','e',1,1,1,'2026-01-01')"))
        conn.execute(text("INSERT INTO page_blacklist (id, page_name) VALUES ('pb-1','bad')"))
        conn.execute(text("INSERT INTO keyword_blacklist (id, keyword) VALUES ('kb-1','kw')"))
        conn.execute(text("INSERT INTO brand_scrapes (id, brand_name, page_id, page_url) VALUES ('bs-1','B','1','http://u')"))
        conn.execute(text("INSERT INTO brand_scraped_ads (id, brand_scrape_id, external_id) VALUES ('bsa-1','bs-1','x')"))
        # etapa 01.3 data: per-USER ad connections, an API key and a mutation audit
        conn.execute(text("INSERT INTO google_ads_connections (id, user_id, customer_id, encrypted_refresh_token, is_active) "
                          "VALUES ('gc-1','u-legacy-1','1111111111','LEGACY-REFRESH-G1',true), "
                          "('gc-2','u-legacy-1','2222222222','LEGACY-REFRESH-G1',false)"))
        conn.execute(text("INSERT INTO meta_ads_connections (id, user_id, ad_account_id, encrypted_access_token, is_active) "
                          "VALUES ('mc-1','u-legacy-1','act_555','LEGACY-ACCESS-M1',true)"))
        conn.execute(text("INSERT INTO tiktok_ads_connections (id, user_id, advertiser_id, encrypted_refresh_token, is_active) "
                          "VALUES ('tc-1','u-legacy-2','777','LEGACY-REFRESH-T1',true)"))
        conn.execute(text("INSERT INTO api_keys (id, name, key_hash, scopes, created_by_user_id) "
                          "VALUES ('ak-1','legacy bot','hash-1','[\"ads:read\",\"ads:publish\",\"ads:spend\"]','u-legacy-1'), "
                          "('ak-2','orphan bot','hash-2','[\"ads:read\"]',NULL)"))
        conn.execute(text("INSERT INTO mutation_audits (id, user_id, provider, account_id, action, payload_hash, mode) "
                          "VALUES ('ma-1','u-legacy-1','google_ads','1111111111','campaign.pause','h','manual')"))


def _signed(conn, org_id):
    """Bind the transaction to ``org_id`` the way the application does (signed)."""
    from app.database import _SET_CONTEXT_SQL, _context_params
    conn.execute(_SET_CONTEXT_SQL, _context_params(org_id, False))


class TestFromEmptyDatabase:
    def test_upgrade_to_head_downgrade_and_upgrade_again(self, scratch_db):
        cfg = _alembic_cfg()
        command.upgrade(cfg, "head")
        with scratch_db.connect() as conn:
            tables = set(text_ for (text_,) in conn.execute(text(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")))
            assert {"organizations", "organization_members", "brands"} <= tables
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "b8c9d0e1f2a3"
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
        expected = {t: 1 for t in ALL_TABLES}
        expected.update({"brands": 2, "products": 2, "customer_profiles": 2, "brand_profiles": 2})
        assert counts == expected  # every legacy row is quarantined (organization_id NULL), none deleted

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
                _signed(conn, org_id)
                from app.database import TENANT_TABLES
                for table in TENANT_TABLES:
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
        assert (rest["brands"], rest["products"], rest["customer_profiles"], rest["brand_profiles"]) == (1, 1, 1, 1)

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
                _signed(conn, org)
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


    def test_claim_tables_is_explicit_dry_run_first_and_parents_first(self, migrated):
        with migrated.connect() as conn:
            org_a = conn.execute(text("SELECT id FROM organizations ORDER BY slug LIMIT 1")).scalar()
            # a child whose brand is still ownerless is refused as a whole: nothing half-assigned
            with pytest.raises(ClaimError, match="refused"):
                claim_tables(conn, org_a, ["winning_ads", "generated_ads"], apply=True)
            conn.rollback()
            assert report(conn)["winning_ads"] == 1 and report(conn)["generated_ads"] == 1
            claim(conn, org_a, ["b-1"], ["c-1"], apply=True)
            conn.commit()
            dry = claim_tables(conn, org_a, ["generated_ads", "winning_ads"], apply=False)
            assert dry == {"winning_ads": 1, "generated_ads": 1}
            assert report(conn)["generated_ads"] == 1  # dry run wrote nothing
            claim_tables(conn, org_a, ["generated_ads", "winning_ads"], apply=True)
            conn.commit()
            assert report(conn)["generated_ads"] == 0 and report(conn)["winning_ads"] == 0
            with pytest.raises(ClaimError, match="Not claimable here"):
                claim_tables(conn, org_a, ["brands"], apply=True)

    def test_no_legacy_row_was_deleted_by_the_migration(self, migrated):
        with migrated.connect() as conn:
            for table in ("generated_ads", "winning_ads", "facebook_ads", "scraped_ads", "brand_scraped_ads"):
                assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 1, table


class TestLegacyIntegrations:
    """Etapa 01.3: per-USER connections / keys / audits survive the migration in quarantine."""

    @pytest.fixture
    def migrated(self, scratch_db):
        cfg = _alembic_cfg()
        command.upgrade(cfg, PRE_TENANT)
        _legacy_rows(scratch_db)
        command.upgrade(cfg, "head")
        return scratch_db

    @staticmethod
    def orgs(conn):
        rows = conn.execute(text(
            "SELECT m.user_id, m.organization_id FROM organization_members m WHERE m.role = 'owner'")).all()
        return {user: org for user, org in rows}

    def test_every_legacy_row_and_token_is_preserved_untouched_and_unowned(self, migrated):
        with migrated.connect() as conn:
            counts = recon.report(conn)
            tokens = dict(conn.execute(text(
                "SELECT id, encrypted_refresh_token FROM google_ads_connections")).all())
            assert conn.execute(text("SELECT encrypted_access_token FROM meta_ads_connections")).scalar() == "LEGACY-ACCESS-M1"
            owners = conn.execute(text("SELECT count(*) FROM google_ads_connections WHERE organization_id IS NOT NULL")).scalar()
        assert counts["google_ads_connections"] == {"u-legacy-1": 2}
        assert counts["meta_ads_connections"] == {"u-legacy-1": 1} and counts["tiktok_ads_connections"] == {"u-legacy-2": 1}
        assert counts["api_keys"] == 2 and counts["mutation_audits"] == 1
        assert tokens == {"gc-1": "LEGACY-REFRESH-G1", "gc-2": "LEGACY-REFRESH-G1"} and owners == 0

    def test_quarantined_rows_are_invisible_to_every_organization_and_keys_authenticate_nobody(self, migrated):
        from sqlalchemy import event
        from app.database import _drop_to_runtime_role
        engine = create_engine(migrated.url)
        event.listen(engine, "connect", _drop_to_runtime_role)
        with engine.connect() as conn:
            org_ids = conn.execute(text("SELECT id FROM organizations")).scalars().all()
        for org_id in org_ids:
            with engine.begin() as conn:
                _signed(conn, org_id)
                for table in ("google_ads_connections", "meta_ads_connections", "tiktok_ads_connections",
                              "mutation_audits", "ad_account_links", "api_keys"):
                    assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0, table
        with engine.begin() as conn:      # presenting a hash reveals that single row ... which the app then refuses
            conn.execute(text("SELECT set_config('app.api_key_hash', 'hash-1', true)"))
            assert conn.execute(text("SELECT organization_id FROM api_keys")).all() == [(None,)]
        engine.dispose()

    def test_new_ownerless_rows_are_blocked_by_the_not_valid_checks(self, migrated):
        with migrated.connect() as conn:
            for stmt, name in (
                ("INSERT INTO api_keys (id, name, key_hash, scopes) VALUES ('n','n','hh','[]')", "ck_api_keys_org_required"),
                ("INSERT INTO google_ads_connections (id, user_id, customer_id, encrypted_refresh_token) "
                 "VALUES ('n','u-legacy-1','9','x')", "ck_google_ads_connections_org_required"),
            ):
                with pytest.raises(Exception, match=name):
                    conn.execute(text(stmt))
                conn.rollback()

    def test_claiming_connections_is_a_dry_run_then_creates_links_without_touching_tokens(self, migrated):
        with migrated.connect() as conn:
            org = self.orgs(conn)["u-legacy-1"]
            would = recon.claim_connections(conn, org, "u-legacy-1", apply=False)
            assert would == {"google_ads_connections": 2, "meta_ads_connections": 1, "tiktok_ads_connections": 0}
            assert recon.report(conn)["google_ads_connections"] == {"u-legacy-1": 2}            # nothing written
            recon.claim_connections(conn, org, "u-legacy-1", apply=True)
            conn.commit()
            links = {(p, a): st for p, a, st in conn.execute(text(
                "SELECT provider, external_account_id, status FROM ad_account_links WHERE organization_id = :o"), {"o": org})}
            tokens = dict(conn.execute(text("SELECT id, encrypted_refresh_token FROM google_ads_connections")).all())
        # the selected account becomes an ACTIVE link; the other stays a PENDING candidate; Meta drops the act_ prefix
        assert links == {("google_ads", "1111111111"): "active", ("google_ads", "2222222222"): "pending",
                         ("meta", "555"): "active"}
        assert tokens == {"gc-1": "LEGACY-REFRESH-G1", "gc-2": "LEGACY-REFRESH-G1"}
        with migrated.connect() as conn:                                   # u-legacy-2's TikTok connection is untouched
            assert recon.report(conn)["tiktok_ads_connections"] == {"u-legacy-2": 1}

    def test_connection_claim_refuses_non_members_non_admins_and_inactive_organizations(self, migrated):
        with migrated.connect() as conn:
            orgs = self.orgs(conn)
            with pytest.raises(recon.ReconcileError, match="not an active member"):
                recon.claim_connections(conn, orgs["u-legacy-2"], "u-legacy-1", apply=True)   # someone else's organization
            conn.rollback()
            conn.execute(text("UPDATE organization_members SET role = 'viewer' WHERE user_id = 'u-legacy-1'"))
            conn.commit()
            with pytest.raises(recon.ReconcileError, match="only"):
                recon.claim_connections(conn, orgs["u-legacy-1"], "u-legacy-1", apply=True)
            conn.rollback()
            with pytest.raises(recon.ReconcileError, match="does not exist"):
                recon.claim_connections(conn, str(uuid.uuid4()), "u-legacy-1", apply=True)
            conn.rollback()
            assert recon.report(conn)["google_ads_connections"] == {"u-legacy-1": 2}

    def test_connection_claim_is_atomic_when_an_account_is_already_linked(self, migrated):
        with migrated.connect() as conn:
            org = self.orgs(conn)["u-legacy-1"]
            conn.execute(text(
                "INSERT INTO ad_account_links (id, organization_id, provider, external_account_id, status) "
                "VALUES ('pre', :o, 'meta', '555', 'active')"), {"o": org})
            conn.commit()
            with pytest.raises(recon.ReconcileError, match="nothing changed"):
                recon.claim_connections(conn, org, "u-legacy-1", apply=True)
            conn.rollback()
            report_now = recon.report(conn)
            assert report_now["google_ads_connections"] == {"u-legacy-1": 2}     # Google part rolled back too

    def test_api_key_claim_strips_publish_and_spend_and_refuses_ownerless_keys(self, migrated):
        with migrated.connect() as conn:
            org = self.orgs(conn)["u-legacy-1"]
            with pytest.raises(recon.ReconcileError, match="no creator"):
                recon.claim_api_keys(conn, org, ["ak-2"], apply=True)
            conn.rollback()
            assert recon.claim_api_keys(conn, org, ["ak-1"], apply=False) == 1
            assert conn.execute(text("SELECT organization_id FROM api_keys WHERE id = 'ak-1'")).scalar() is None
            recon.claim_api_keys(conn, org, ["ak-1"], apply=True)
            conn.commit()
            row = conn.execute(text("SELECT organization_id, scopes FROM api_keys WHERE id = 'ak-1'")).one()
            with pytest.raises(recon.ReconcileError, match="already bound"):
                recon.claim_api_keys(conn, org, ["ak-1"], apply=True)
            conn.rollback()
        assert row[0] == org and sorted(row[1]) == ["ads:read"]

    def test_audit_claim_assigns_only_that_users_rows(self, migrated):
        with migrated.connect() as conn:
            orgs = self.orgs(conn)
            assert recon.claim_audits(conn, orgs["u-legacy-1"], "u-legacy-1", apply=True) == 1
            conn.commit()
            assert recon.report(conn)["mutation_audits"] == 0

    def test_reconcile_cannot_run_as_the_unprivileged_runtime_role(self, migrated):
        from sqlalchemy import event
        from app.database import _drop_to_runtime_role
        engine = create_engine(migrated.url)
        event.listen(engine, "connect", _drop_to_runtime_role)
        with engine.connect() as conn:
            with pytest.raises(recon.ReconcileError, match="subject to row level security"):
                recon.claim_audits(conn, "x", "y", apply=True)
        engine.dispose()
