"""Schema-level guarantees of the agent tables: RLS, composite foreign keys, constraints, append-only history and the
single platform policy used by the recovery job. Raw SQL on purpose: these must hold even if application code is wrong."""
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.unit.agents_support import create_task_row, world_of


@pytest.fixture
def w(make_user, make_org, add_member, login_headers):
    return world_of(make_user, make_org, add_member, login_headers)


def session_for(org_id=None, platform=False):
    from app.database import SessionLocal, bind_context
    db = SessionLocal()
    bind_context(db, org_id, platform_admin=platform)
    return db


def insert_step(db, org_id, task_id, seq, **extra):
    db.execute(text("INSERT INTO agent_steps (id, organization_id, task_id, sequence, kind, status) VALUES (:i, :o, :t, :s, 'llm', 'ok')"),
               {"i": str(uuid.uuid4()), "o": org_id, "t": task_id, "s": seq})


TABLES = ("agent_tasks", "agent_runs", "agent_steps", "agent_settings")


class TestRowLevelSecurity:
    def seed(self, w, db_session):
        task_id = create_task_row(w.org, w.users["manager"], agent_key="product_analyst")
        db = session_for(w.org.id)
        try:
            db.execute(text("INSERT INTO agent_runs (id, organization_id, task_id, sequence, agent_key, agent_version, instructions_hash, objective) "
                            "VALUES ('r1', :o, :t, 1, 'product_analyst', '1.0.0', 'h', 'obj')"), {"o": w.org.id, "t": task_id})
            insert_step(db, w.org.id, task_id, 1)
            db.execute(text("INSERT INTO agent_settings (id, organization_id, agent_key) VALUES ('s1', :o, 'product_analyst')"), {"o": w.org.id})
            db.commit()
        finally:
            db.close()
        return task_id

    def test_each_table_is_visible_only_inside_its_own_organization(self, w, db_session):
        self.seed(w, db_session)
        for org_id, expected in ((w.org.id, 1), (w.other.id, 0), (None, 0)):
            db = session_for(org_id)
            try:
                assert [db.execute(text(f"SELECT count(*) FROM {t}")).scalar() for t in TABLES] == [expected] * 4, org_id
            finally:
                db.close()

    def test_writing_into_another_organization_is_refused(self, w, db_session):
        task_id = self.seed(w, db_session)
        db = session_for(w.other.id)
        try:
            with pytest.raises(DBAPIError, match="row-level security"):
                insert_step(db, w.org.id, task_id, 99)
        finally:
            db.rollback()
            db.close()

    def test_a_run_cannot_attach_to_another_organizations_task(self, w, db_session):
        task_id = self.seed(w, db_session)
        db = session_for(w.other.id)
        try:
            with pytest.raises(DBAPIError):          # composite FK (task_id, organization_id) AND RLS: both say no
                db.execute(text("INSERT INTO agent_runs (id, organization_id, task_id, sequence, agent_key, agent_version, instructions_hash, objective) "
                                "VALUES ('r2', :o, :t, 1, 'product_analyst', '1.0.0', 'h', 'obj')"), {"o": w.other.id, "t": task_id})
        finally:
            db.rollback()
            db.close()

    def test_only_the_signed_platform_context_can_see_tasks_across_organizations(self, w, db_session):
        create_task_row(w.org, w.users["manager"], agent_key="product_analyst")
        create_task_row(w.other, w.other_owner, agent_key="product_analyst")
        platform = session_for(None, platform=True)
        try:
            assert platform.execute(text("SELECT count(*) FROM agent_tasks")).scalar() == 2
            # the platform policy exists ONLY on agent_tasks: it grants no access to content tables
            assert platform.execute(text("SELECT count(*) FROM agent_steps")).scalar() == 0
            assert platform.execute(text("SELECT count(*) FROM agent_runs")).scalar() == 0
        finally:
            platform.close()
        ordinary = session_for(w.org.id)
        try:
            assert ordinary.execute(text("SELECT count(*) FROM agent_tasks")).scalar() == 1
        finally:
            ordinary.close()

    def test_a_forged_platform_flag_without_the_signature_grants_nothing(self, w, db_session):
        create_task_row(w.org, w.users["manager"], agent_key="product_analyst")
        db = session_for(w.other.id)
        try:
            db.execute(text("SELECT set_config('app.platform_admin', 'on', true)"))      # raw setting, no valid HMAC
            assert db.execute(text("SELECT count(*) FROM agent_tasks")).scalar() == 0
        finally:
            db.close()

    def test_exactly_two_policies_on_tasks_and_one_on_the_other_tables(self, db_session):
        rows = db_session.execute(text("SELECT tablename, count(*) FROM pg_policies WHERE tablename LIKE 'agent_%' GROUP BY 1")).all()
        assert dict(rows) == {"agent_tasks": 2, "agent_runs": 1, "agent_steps": 1, "agent_settings": 1}


class TestHistoryIsAppendOnly:
    def test_the_application_role_cannot_update_delete_or_truncate_steps(self, w, db_session):
        task_id = create_task_row(w.org, w.users["manager"], agent_key="product_analyst")
        db = session_for(w.org.id)
        try:
            insert_step(db, w.org.id, task_id, 1)
            db.commit()
            for statement in ("UPDATE agent_steps SET status = 'error'", "DELETE FROM agent_steps", "TRUNCATE agent_steps"):
                with pytest.raises(DBAPIError, match="permission denied"):
                    db.execute(text(statement))
                db.rollback()
        finally:
            db.close()


class TestConstraints:
    def one_task(self, w):
        return create_task_row(w.org, w.users["manager"], agent_key="product_analyst")

    def test_status_values_are_checked(self, w, db_session):
        task_id = self.one_task(w)
        with pytest.raises(IntegrityError):
            db_session.execute(text("UPDATE agent_tasks SET status = 'zombie' WHERE id = :i"), {"i": task_id})
        db_session.rollback()

    def test_step_and_run_sequences_are_unique_per_task(self, w, db_session):
        task_id = self.one_task(w)
        insert_step(db_session, w.org.id, task_id, 1)
        db_session.commit()
        with pytest.raises(IntegrityError):
            insert_step(db_session, w.org.id, task_id, 1)
        db_session.rollback()

    def test_idempotency_key_is_unique_per_organization_only(self, w, db_session):
        create_task_row(w.org, w.users["manager"], idempotency_key="k")
        create_task_row(w.other, w.other_owner, idempotency_key="k")          # same key, other organization: fine
        with pytest.raises(IntegrityError):
            db_session.execute(text("INSERT INTO agent_tasks (id, organization_id, objective, status, idempotency_key) VALUES ('dup', :o, 'x', 'pending', 'k')"), {"o": w.org.id})
        db_session.rollback()

    def test_settings_overrides_must_be_positive_and_unique_per_agent(self, w, db_session):
        db_session.execute(text("INSERT INTO agent_settings (id, organization_id, agent_key) VALUES ('a', :o, 'x')"), {"o": w.org.id})
        db_session.commit()
        for sql in ("INSERT INTO agent_settings (id, organization_id, agent_key) VALUES ('b', :o, 'x')",
                    "INSERT INTO agent_settings (id, organization_id, agent_key, max_steps) VALUES ('c', :o, 'y', 0)"):
            with pytest.raises(IntegrityError):
                db_session.execute(text(sql), {"o": w.org.id})
            db_session.rollback()

    def test_deleting_a_task_removes_its_runs_and_steps(self, w, db_session):
        task_id = self.one_task(w)
        insert_step(db_session, w.org.id, task_id, 1)
        db_session.commit()
        db_session.execute(text("DELETE FROM agent_tasks WHERE id = :i"), {"i": task_id})
        db_session.commit()
        assert db_session.execute(text("SELECT count(*) FROM agent_steps")).scalar() == 0
