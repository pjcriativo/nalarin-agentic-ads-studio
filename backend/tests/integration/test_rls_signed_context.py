"""Hardening of the tenant context (etapa 01.2).

The raw ``app.current_org`` setting can be overwritten by any SQL running in the
session. RLS therefore only honours it when it carries a valid HMAC signature
that the runtime role cannot produce. These tests act as an attacker who has
arbitrary SQL execution inside an application session.
"""
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.database import (
    CATALOG_TABLES, SessionLocal, TENANT_TABLES, bind_context, verify_runtime_role_is_safe,
)
from app.models import GeneratedAd, Organization


@pytest.fixture
def orgs(db_session):
    made = []
    for tag in ("A", "B"):
        org = Organization(id=str(uuid.uuid4()), name=f"Sig {tag}", slug=f"sig-{tag.lower()}-{uuid.uuid4().hex[:6]}")
        db_session.add(org)
        db_session.flush()
        for i in range(3):
            db_session.add(GeneratedAd(id=str(uuid.uuid4()), organization_id=org.id, headline=f"{tag}{i}"))
        made.append(org)
    db_session.commit()
    return made


def headlines(db):
    return sorted(h for (h,) in db.execute(text("SELECT headline FROM generated_ads")))


class TestForgedContext:
    def test_raw_setting_without_signature_matches_nothing(self, orgs):
        db = SessionLocal()
        try:
            db.execute(text("SELECT set_config('app.current_org', :o, true)"), {"o": orgs[0].id})
            assert headlines(db) == []
        finally:
            db.close()

    def test_signed_session_cannot_switch_tenant_by_overwriting_the_setting(self, orgs):
        a, b = orgs
        db = SessionLocal()
        try:
            bind_context(db, a.id)
            assert headlines(db) == ["A0", "A1", "A2"]
            db.execute(text("SELECT set_config('app.current_org', :o, true)"), {"o": b.id})  # injected SQL
            assert headlines(db) == []  # signature no longer matches: fail closed, NOT tenant B
        finally:
            db.close()

    def test_guessed_or_replayed_signatures_are_rejected(self, orgs):
        a, b = orgs
        db = SessionLocal()
        try:
            bind_context(db, a.id)
            sig = db.execute(text("SELECT current_setting('app.ctx_sig')")).scalar()
            nonce = db.execute(text("SELECT current_setting('app.ctx_nonce')")).scalar()
            db.execute(text("SELECT set_config('app.current_org', :o, true)"), {"o": b.id})
            db.execute(text("SELECT set_config('app.ctx_sig', :s, true)"), {"s": sig})   # A's signature, B's org
            db.execute(text("SELECT set_config('app.ctx_nonce', :n, true)"), {"n": nonce})
            assert headlines(db) == []
            for guess in ("", "0" * 64, "forged"):
                db.execute(text("SELECT set_config('app.ctx_sig', :s, true)"), {"s": guess})
                assert headlines(db) == []
        finally:
            db.close()

    def test_platform_admin_flag_cannot_be_self_granted(self, orgs):
        db = SessionLocal()
        try:
            bind_context(db, orgs[0].id)
            db.execute(text("SELECT set_config('app.platform_admin', 'on', true)"))
            assert db.execute(text("SELECT app_is_platform_admin()")).scalar() is False
        finally:
            db.close()

    def test_the_secret_and_the_verification_internals_are_unreadable_to_the_runtime_role(self, orgs):
        db = SessionLocal()
        try:
            bind_context(db, orgs[0].id)
            with pytest.raises(Exception, match="permission denied"):
                db.execute(text("SELECT secret FROM tenant_ctx_secret"))
        finally:
            db.rollback()
            db.close()

    def test_every_tenant_table_returns_nothing_without_a_valid_context(self, orgs):
        db = SessionLocal()
        try:
            db.execute(text("SELECT set_config('app.current_org', :o, true)"), {"o": orgs[0].id})
            for table in TENANT_TABLES:
                assert db.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0, table
        finally:
            db.close()

    def test_every_tenant_table_has_forced_rls_and_a_policy(self, db_session):
        rows = dict(((n, (rls, forced)) for n, rls, forced in db_session.execute(text(
            "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = ANY(:t)"),
            {"t": list(TENANT_TABLES + CATALOG_TABLES + ("audit_logs",))})))
        assert all(v == (True, True) for v in rows.values()) and len(rows) == len(TENANT_TABLES) + len(CATALOG_TABLES) + 1
        with_policy = {t for (t,) in db_session.execute(text("SELECT DISTINCT tablename FROM pg_policies"))}
        assert set(TENANT_TABLES) | set(CATALOG_TABLES) | {"audit_logs"} <= with_policy


class TestConnectionReuseAndConcurrency:
    def test_pooled_connection_never_carries_the_previous_tenant(self, orgs):
        a, b = orgs
        from app.database import engine
        seen = []
        for org in (a, b, None, a, None, b):
            db = SessionLocal()
            try:
                if org:
                    bind_context(db, org.id)
                seen.append(headlines(db))
                db.commit()
            finally:
                db.close()
        assert seen == [["A0", "A1", "A2"], ["B0", "B1", "B2"], [], ["A0", "A1", "A2"], [], ["B0", "B1", "B2"]]
        assert engine.pool.checkedout() == 0

    def test_concurrent_requests_of_two_tenants_never_mix(self, orgs):
        ids = [o.id for o in orgs]  # read ORM attributes before going multi-threaded
        errors = []

        def work(i):
            org_id = ids[i % 2]
            expected = [f"{'AB'[i % 2]}{n}" for n in range(3)]
            for _ in range(15):
                db = SessionLocal()
                try:
                    bind_context(db, org_id)
                    if headlines(db) != expected:
                        errors.append((i, headlines(db)))
                    db.rollback()
                finally:
                    db.close()

        with ThreadPoolExecutor(max_workers=16) as pool:
            list(pool.map(work, range(32)))
        assert errors == []


class TestRunningAsALoginRole:
    """With RUNTIME_DATABASE_URL the process cannot even escape to the owner role."""

    @pytest.fixture
    def login_engine(self, db_session):
        name = f"ads_login_{uuid.uuid4().hex[:6]}"
        db_session.execute(text(f"CREATE ROLE {name} LOGIN PASSWORD 'pw' NOSUPERUSER NOBYPASSRLS"))
        db_session.execute(text(f"GRANT {settings.DB_RUNTIME_ROLE} TO {name}"))
        db_session.commit()
        url = make_url(settings.DATABASE_URL).set(username=name, password="pw")
        engine = create_engine(url)  # NO SET ROLE: it logs in as the unprivileged role
        try:
            yield engine
        finally:
            engine.dispose()
            db_session.execute(text(f"DROP OWNED BY {name}"))
            db_session.execute(text(f"DROP ROLE {name}"))
            db_session.commit()

    def test_cannot_become_the_owner_and_rls_applies(self, login_engine, orgs):
        with login_engine.connect() as conn:
            with pytest.raises(Exception, match="permission denied|must be member"):
                conn.execute(text("SET ROLE ads_studio_test"))
            conn.rollback()
            with pytest.raises(Exception):
                conn.execute(text("RESET ROLE; SET ROLE postgres"))
            conn.rollback()
            assert conn.execute(text("SELECT count(*) FROM generated_ads")).scalar() == 0
        Session = sessionmaker(bind=login_engine)
        db = Session()
        try:
            bind_context(db, orgs[0].id)
            assert headlines(db) == ["A0", "A1", "A2"]
        finally:
            db.close()

    def test_strict_mode_accepts_a_login_role_and_rejects_the_owner(self, login_engine, db_session, monkeypatch):
        monkeypatch.setattr(settings, "REQUIRE_LOGIN_RUNTIME_ROLE", True)
        verify_runtime_role_is_safe(login_engine)  # unprivileged login: fine
        from app.database import engine
        with pytest.raises(RuntimeError, match="RUNTIME_DATABASE_URL|privileged"):
            verify_runtime_role_is_safe(engine)  # owner + SET ROLE: injected SQL could RESET ROLE


class TestStartupGuardChecksTheKey:
    def test_mismatching_key_is_detected_with_an_actionable_message(self, db_session):
        old = db_session.execute(text("SELECT secret FROM tenant_ctx_secret")).scalar()
        db_session.execute(text("UPDATE tenant_ctx_secret SET secret = 'some-other-key-0123456789-abcdefghijkl'"))
        db_session.commit()
        try:
            with pytest.raises(RuntimeError, match="sync_tenant_key"):
                verify_runtime_role_is_safe()
        finally:
            db_session.execute(text("UPDATE tenant_ctx_secret SET secret = :s"), {"s": old})
            db_session.commit()
        verify_runtime_role_is_safe()

    def test_guard_requires_audit_and_catalog_tables_to_be_protected(self, db_session):
        db_session.execute(text("ALTER TABLE prompts NO FORCE ROW LEVEL SECURITY"))
        db_session.commit()
        try:
            with pytest.raises(RuntimeError, match="prompts"):
                verify_runtime_role_is_safe()
        finally:
            db_session.execute(text("ALTER TABLE prompts FORCE ROW LEVEL SECURITY"))
            db_session.commit()


class TestContextParams:
    def test_malformed_organization_ids_are_refused_before_reaching_sql(self):
        db = SessionLocal()
        try:
            for bad in ("x'; DROP TABLE brands;--", "a" * 200, "../../etc"):
                with pytest.raises(ValueError):
                    bind_context(db, bad)
        finally:
            db.close()

    def test_every_transaction_gets_a_fresh_nonce(self, orgs):
        db = SessionLocal()
        try:
            bind_context(db, orgs[0].id)
            first = db.execute(text("SELECT current_setting('app.ctx_nonce')")).scalar()
            db.commit()
            second = db.execute(text("SELECT current_setting('app.ctx_nonce')")).scalar()
            assert first != second
        finally:
            db.close()
