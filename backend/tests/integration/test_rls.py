"""Row level security, exercised directly on PostgreSQL.

These tests talk to the database the way the application does (runtime role,
organization bound through the session) but without the HTTP layer, so they
prove the DATABASE enforces the tenant boundary even if application code were
wrong: reused connections, concurrent requests, writes into another tenant,
and a missing context.
"""
import random
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

import app.database as appdb
from app.core.config import settings
from app.database import (
    SessionLocal, TENANT_TABLES, _drop_to_runtime_role, verify_runtime_role_is_safe,
)
from app.models import Brand, Organization


@pytest.fixture
def two_orgs(db_session):
    """Two organizations with a few brands each, seeded as superuser."""
    orgs = []
    for tag in ("A", "B"):
        org = Organization(id=str(uuid.uuid4()), name=f"RLS {tag}", slug=f"rls-{tag.lower()}-{uuid.uuid4().hex[:6]}")
        db_session.add(org)
        db_session.flush()
        for i in range(3):
            db_session.add(Brand(id=str(uuid.uuid4()), organization_id=org.id, name=f"{tag}{i}"))
        orgs.append(org)
    db_session.commit()
    return orgs


def session_for(org_id=None):
    db = SessionLocal()
    if org_id:
        db.info["org_id"] = org_id
    return db


def names(db):
    return sorted(n for (n,) in db.execute(text("SELECT name FROM brands")))


class TestRuntimeRole:
    def test_application_connections_use_an_unprivileged_role(self):
        db = SessionLocal()
        try:
            row = db.execute(text(
                "SELECT current_user, r.rolsuper, r.rolbypassrls "
                "FROM pg_roles r WHERE r.rolname = current_user")).one()
        finally:
            db.close()
        assert row[0] == settings.DB_RUNTIME_ROLE
        assert row[1] is False and row[2] is False

    def test_startup_guard_accepts_the_runtime_role(self):
        verify_runtime_role_is_safe()  # must not raise

    def test_startup_guard_rejects_a_superuser_connection(self, db_session):
        superuser_engine = db_session.get_bind()  # seeding connection: superuser, no SET ROLE
        with pytest.raises(RuntimeError, match="Unsafe database role"):
            verify_runtime_role_is_safe(superuser_engine)

    def test_startup_guard_rejects_unprotected_tenant_tables(self, db_session):
        db_session.execute(text("ALTER TABLE products NO FORCE ROW LEVEL SECURITY"))
        db_session.commit()
        try:
            with pytest.raises(RuntimeError, match="without enforced row level security"):
                verify_runtime_role_is_safe()
        finally:
            db_session.execute(text("ALTER TABLE products FORCE ROW LEVEL SECURITY"))
            db_session.commit()

    def test_every_tenant_table_has_forced_rls_and_a_policy(self, db_session):
        rows = dict(db_session.execute(text(
            "SELECT c.relname, c.relrowsecurity AND c.relforcerowsecurity FROM pg_class c "
            "WHERE c.relname = ANY(:t)"), {"t": list(TENANT_TABLES)}).all())
        assert rows == {t: True for t in TENANT_TABLES}
        policies = {t for (t,) in db_session.execute(text("SELECT tablename FROM pg_policies"))}
        assert set(TENANT_TABLES) <= policies

    def test_runtime_role_cannot_switch_rls_off(self):
        db = SessionLocal()
        try:
            with pytest.raises(Exception):
                db.execute(text("ALTER TABLE brands DISABLE ROW LEVEL SECURITY"))
        finally:
            db.rollback()
            db.close()


class TestPolicyVisibility:
    def test_each_organization_sees_only_its_rows(self, two_orgs):
        a, b = two_orgs
        with_a, with_b = session_for(a.id), session_for(b.id)
        try:
            assert names(with_a) == ["A0", "A1", "A2"]
            assert names(with_b) == ["B0", "B1", "B2"]
        finally:
            with_a.close(); with_b.close()

    def test_no_organization_context_fails_closed(self, two_orgs):
        db = session_for(None)
        try:
            assert names(db) == []
        finally:
            db.close()

    def test_unknown_organization_sees_nothing(self, two_orgs):
        db = session_for(str(uuid.uuid4()))
        try:
            assert names(db) == []
        finally:
            db.close()

    def test_cannot_update_or_delete_another_organizations_rows(self, two_orgs, db_session):
        a, b = two_orgs
        db = session_for(a.id)
        try:
            updated = db.execute(text("UPDATE brands SET name = 'pwned' WHERE organization_id = :o"), {"o": b.id}).rowcount
            deleted = db.execute(text("DELETE FROM brands WHERE organization_id = :o"), {"o": b.id}).rowcount
            db.commit()
        finally:
            db.close()
        assert (updated, deleted) == (0, 0)
        assert db_session.execute(text("SELECT count(*) FROM brands WHERE organization_id = :o"), {"o": b.id}).scalar() == 3

    def test_cannot_insert_into_another_organization(self, two_orgs):
        a, b = two_orgs
        db = session_for(a.id)
        try:
            with pytest.raises(Exception, match="row-level security"):
                db.execute(text("INSERT INTO brands (id, organization_id, name) VALUES (:i, :o, 'x')"),
                           {"i": str(uuid.uuid4()), "o": b.id})
        finally:
            db.rollback(); db.close()

    def test_cannot_move_a_row_to_another_organization(self, two_orgs):
        a, b = two_orgs
        db = session_for(a.id)
        try:
            with pytest.raises(Exception, match="row-level security"):
                db.execute(text("UPDATE brands SET organization_id = :b WHERE organization_id = :a"), {"a": a.id, "b": b.id})
        finally:
            db.rollback(); db.close()

    def test_new_rows_without_an_organization_are_rejected(self, db_session):
        """Even the superuser cannot create ownerless tenant rows any more."""
        with pytest.raises(Exception, match="ck_brands_org_required"):
            db_session.execute(text("INSERT INTO brands (id, name) VALUES (:i, 'ownerless')"), {"i": str(uuid.uuid4())})
        db_session.rollback()

    def test_orm_guard_blocks_a_cross_organization_object(self, two_orgs):
        a, b = two_orgs
        db = session_for(a.id)
        try:
            db.add(Brand(id=str(uuid.uuid4()), organization_id=b.id, name="smuggled"))
            with pytest.raises(PermissionError):
                db.flush()
        finally:
            db.rollback(); db.close()

    def test_orm_stamps_the_session_organization_on_new_rows(self, two_orgs, db_session):
        a, _ = two_orgs
        db = session_for(a.id)
        try:
            brand = Brand(id=str(uuid.uuid4()), name="stamped")
            db.add(brand)
            db.commit()
            bid = brand.id
        finally:
            db.close()
        assert db_session.execute(text("SELECT organization_id FROM brands WHERE id = :i"), {"i": bid}).scalar() == a.id

    def test_composite_foreign_keys_forbid_cross_tenant_links(self, two_orgs, db_session):
        a, b = two_orgs
        brand_b = db_session.execute(text("SELECT id FROM brands WHERE organization_id = :o LIMIT 1"), {"o": b.id}).scalar()
        with pytest.raises(Exception, match="fk_products_brand_org"):
            db_session.execute(text(
                "INSERT INTO products (id, organization_id, brand_id, name) VALUES (:i, :a, :b, 'x')"),
                {"i": str(uuid.uuid4()), "a": a.id, "b": brand_b})
        db_session.rollback()


class TestConnectionReuseAndConcurrency:
    @pytest.fixture
    def single_connection_engine(self):
        """A pool of exactly ONE connection, with the same runtime-role setup."""
        eng = create_engine(settings.DATABASE_URL, pool_size=1, max_overflow=0)
        event.listen(eng, "connect", _drop_to_runtime_role)
        yield eng
        eng.dispose()

    def test_tenant_does_not_leak_to_the_next_user_of_the_same_connection(self, two_orgs, single_connection_engine):
        a, _ = two_orgs
        Session = sessionmaker(bind=single_connection_engine)

        first = Session()
        first.info["org_id"] = a.id
        pid_first = first.execute(text("SELECT pg_backend_pid()")).scalar()
        assert names(first) == ["A0", "A1", "A2"]
        first.commit()   # context is transaction-local: it dies here
        first.close()

        second = Session()  # no organization, SAME physical connection
        try:
            assert second.execute(text("SELECT pg_backend_pid()")).scalar() == pid_first
            assert second.execute(text("SELECT current_setting('app.current_org', true)")).scalar() in ("", None)
            assert names(second) == []
        finally:
            second.close()

    def test_context_survives_commit_inside_one_session_and_resets_after_close(self, two_orgs):
        a, _ = two_orgs
        db = session_for(a.id)
        try:
            assert names(db) == ["A0", "A1", "A2"]
            db.commit()                       # new transaction -> after_begin re-applies the org
            assert names(db) == ["A0", "A1", "A2"]
            db.info["org_id"] = None          # context removed
            db.commit()
            assert names(db) == []
        finally:
            db.close()

    def test_rollback_after_an_error_does_not_poison_the_connection(self, two_orgs, single_connection_engine):
        a, b = two_orgs
        Session = sessionmaker(bind=single_connection_engine)
        db = Session()
        db.info["org_id"] = a.id
        with pytest.raises(Exception):
            db.execute(text("INSERT INTO brands (id, organization_id, name) VALUES (:i, :o, 'x')"),
                       {"i": str(uuid.uuid4()), "o": b.id})
        db.rollback()
        db.close()
        other = Session()
        other.info["org_id"] = b.id
        try:
            assert names(other) == ["B0", "B1", "B2"]
        finally:
            other.close()

    def test_concurrent_requests_never_see_each_others_rows(self, two_orgs):
        a, b = two_orgs
        expected = {a.id: ["A0", "A1", "A2"], b.id: ["B0", "B1", "B2"]}
        errors = []
        barrier = threading.Barrier(16)

        def worker(seed):
            rng = random.Random(seed)
            barrier.wait()
            for _ in range(60):
                org = rng.choice([a.id, b.id, None])
                db = session_for(org)
                try:
                    seen = names(db)
                    want = expected.get(org, [])
                    if seen != want:
                        errors.append((org, seen))
                finally:
                    db.close()

        # more workers than pooled connections -> connections are constantly reused
        with ThreadPoolExecutor(max_workers=16) as pool:
            list(pool.map(worker, range(16)))
        assert errors == []

    def test_concurrent_writers_each_land_in_their_own_organization(self, two_orgs, db_session):
        a, b = two_orgs
        counts = {a.id: 0, b.id: 0}
        lock = threading.Lock()

        def writer(i):
            org = a.id if i % 2 == 0 else b.id
            db = session_for(org)
            try:
                db.add(Brand(id=str(uuid.uuid4()), name=f"conc-{i}"))   # org stamped from the session
                db.commit()
                with lock:
                    counts[org] += 1
            finally:
                db.close()

        with ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(writer, range(60)))
        for org in (a, b):
            stored = db_session.execute(text(
                "SELECT count(*) FROM brands WHERE organization_id = :o AND name LIKE 'conc-%'"), {"o": org.id}).scalar()
            assert stored == counts[org.id] == 30
