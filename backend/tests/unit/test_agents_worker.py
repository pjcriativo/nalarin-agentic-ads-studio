"""Agent worker (etapa 02.4): queue claim, limits, retries, graceful shutdown, fencing, isolation, logs and health.
In-process tests drive the REAL worker loop with a scripted gateway; the process tests (test_agents_worker_processes.py)
run the API and several workers as separate operating-system processes. No provider is contacted."""
import asyncio
import io
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.agents import errors as AE
from app.agents import executor, queue
from app.agents.errors import LostLease
from app.agents.store import TaskStore, utcnow
from app.core.config import settings
from app.models import AgentRun, AgentTask, AuditLog, OrganizationMember
from app.worker.logs import JsonFormatter, log_event
from app.worker.worker import AgentWorker, healthcheck
from tests.unit.agents_support import (COPY, PLAN, PRODUCT, REPORT, Delay, Say, ScriptedGateway, UseTools, ai_error,
                                       create_task_row, drain, say_json, snapshot, world_of)


@pytest.fixture
def w(make_user, make_org, add_member, login_headers):
    return world_of(make_user, make_org, add_member, login_headers)


@pytest.fixture(autouse=True)
def roomy_caps(monkeypatch):
    """Tests enqueue many tasks per organization; the creation cap is exercised in test_agents_api.py."""
    monkeypatch.setattr(settings, "AGENT_MAX_ACTIVE_TASKS_PER_ORG", 100)
    monkeypatch.setattr(settings, "AGENT_RETRY_BASE_SECONDS", 30.0)


def gateway_for(monkeypatch, make):
    """``make(db, org_id, user_id)`` -> gateway (a fresh one per task, so tasks never share a script)."""
    monkeypatch.setattr(executor, "GATEWAY_FACTORY", make)


def enqueue(w, n=1, agent="product_analyst", org=None, user=None, **kw):
    org, user = org or w.org, user or w.users["manager"]
    return [create_task_row(org, user, agent_key=agent, objective=f"Analyze widget product number {i} for ads", **kw) for i in range(n)]


def row(db_session, task_id):
    return snapshot(db_session, task_id)[0]


class Inflight:
    """Counts model calls in flight across ALL tasks of the process (a proxy for tasks running at the same time)."""
    def __init__(self, seconds=0.3, answer=PRODUCT):
        self.now = self.peak = 0
        self.seconds, self.answer = seconds, answer
        self.by_org = {}
        self.peak_by_org = {}

    def factory(self, db, org_id, user_id):
        outer = self

        class Gate(ScriptedGateway):
            async def chat(self, operation, request):
                outer.now += 1
                outer.by_org[org_id] = outer.by_org.get(org_id, 0) + 1
                outer.peak = max(outer.peak, outer.now)
                outer.peak_by_org[org_id] = max(outer.peak_by_org.get(org_id, 0), outer.by_org[org_id])
                try:
                    await asyncio.sleep(outer.seconds)
                    return await ScriptedGateway.chat(self, operation, request)
                finally:
                    outer.now -= 1
                    outer.by_org[org_id] -= 1
        return Gate(say_json(self.answer))


# ================================================================================================== the queue
class TestQueueClaim:
    def test_a_claim_turns_the_oldest_pending_task_into_a_leased_running_one(self, w, db_session):
        first, second = enqueue(w, 2)
        claim = queue.claim_next("worker-1")
        assert claim.task_id == first and claim.org_id == w.org.id and claim.attempt == 1
        task = row(db_session, first)
        assert task.status == "running" and task.lease_owner == claim.owner and task.worker_id == "worker-1"
        assert task.lease_expires_at > utcnow() and row(db_session, second).status == "pending"

    def test_nothing_to_claim_returns_none(self, w):
        assert queue.claim_next("worker-1") is None

    def test_concurrent_workers_never_claim_the_same_task(self, w, db_session):
        ids = set(enqueue(w, 6))
        won, lock = [], threading.Lock()

        def grab():
            for _ in range(3):
                claim = queue.claim_next("w", global_max=100, org_max=100)
                if claim:
                    with lock:
                        won.append(claim)
        threads = [threading.Thread(target=grab) for _ in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert sorted(c.task_id for c in won) == sorted(ids)                     # every task exactly once
        assert len({c.owner for c in won}) == 6 and all(c.attempt == 1 for c in won)

    def test_the_global_limit_holds_across_concurrent_claimers(self, w, db_session):
        enqueue(w, 4)
        enqueue(w, 4, org=w.other, user=w.other_owner)
        won, lock = [], threading.Lock()

        def grab():
            claim = queue.claim_next("w", global_max=3, org_max=100)
            if claim:
                with lock:
                    won.append(claim)
        threads = [threading.Thread(target=grab) for _ in range(12)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert len(won) == 3
        assert db_session.execute(text("SELECT count(*) FROM agent_tasks WHERE status = 'running'")).scalar() == 3

    def test_the_organization_limit_does_not_starve_the_other_organization(self, w, db_session):
        mine = enqueue(w, 4)
        theirs = enqueue(w, 1, org=w.other, user=w.other_owner)
        got = [queue.claim_next("w", global_max=10, org_max=2) for _ in range(5)]
        claimed = [c.task_id for c in got if c]
        assert claimed == [mine[0], mine[1], theirs[0]]                          # 2 of mine, then the others' task; my 3rd waits
        assert got[3] is None and got[4] is None

    def test_a_finished_task_frees_its_slot(self, w, db_session):
        enqueue(w, 2)
        first = queue.claim_next("w", org_max=1)
        assert queue.claim_next("w", org_max=1) is None
        db_session.execute(text("UPDATE agent_tasks SET status = 'completed' WHERE id = :i"), {"i": first.task_id})
        db_session.commit()
        assert queue.claim_next("w", org_max=1) is not None

    def test_a_running_task_with_an_expired_lease_does_not_hold_a_slot(self, w, db_session):
        a, _b = enqueue(w, 2)
        queue.claim_next("w", org_max=1)
        db_session.execute(text("UPDATE agent_tasks SET lease_expires_at = now() - interval '1 minute' WHERE id = :i"), {"i": a})
        db_session.commit()
        assert queue.claim_next("w", global_max=1, org_max=1) is not None       # the dead one is not counted

    def test_a_task_in_backoff_or_flagged_for_cancellation_is_not_claimable(self, w, db_session):
        later, cancelled, ready = enqueue(w, 3)
        db_session.execute(text("UPDATE agent_tasks SET available_at = now() + interval '1 hour' WHERE id = :i"), {"i": later})
        db_session.execute(text("UPDATE agent_tasks SET cancel_requested = true WHERE id = :i"), {"i": cancelled})
        db_session.commit()
        assert queue.claim_next("w", global_max=10, org_max=10).task_id == ready
        assert queue.claim_next("w", global_max=10, org_max=10) is None
        db_session.execute(text("UPDATE agent_tasks SET available_at = now() - interval '1 second' WHERE id = :i"), {"i": later})
        db_session.commit()
        assert queue.claim_next("w", global_max=10, org_max=10).task_id == later

    def test_the_claim_returns_identifiers_only_and_runs_in_the_platform_context(self, w):
        enqueue(w, 1)
        claim = queue.claim_next("w")
        assert set(vars(claim)) == {"task_id", "org_id", "owner", "attempt"}


# ================================================================================================== fencing & leases
class TestFencingAndLeases:
    def age(self, db_session, task_id):
        db_session.execute(text("UPDATE agent_tasks SET lease_expires_at = now() - interval '5 minutes' WHERE id = :i"), {"i": task_id})
        db_session.commit()

    def test_a_recovered_and_reclaimed_task_cannot_be_advanced_by_the_old_owner(self, w, db_session):
        (task_id,) = enqueue(w, 1)
        old = queue.claim_next("worker-a")
        self.age(db_session, task_id)
        assert executor.recover_interrupted_tasks()["recovered"] == [(task_id, w.org.id, "pending")]
        new = queue.claim_next("worker-b")
        assert new.task_id == task_id and new.owner != old.owner and new.attempt == 2
        stale = TaskStore(executor._session(w.org.id), w.org.id, task_id, old.owner)
        with pytest.raises(LostLease):
            stale.checkpoint()
        with pytest.raises(LostLease):
            stale.add_step(kind="llm")
        with pytest.raises(LostLease):
            stale.finish_task(status="completed", result={"x": 1})
        with pytest.raises(LostLease):
            stale.fail_or_retry(error_kind="internal", error_message="x", retry=True)
        assert queue.release(old) is False                                      # the stale owner cannot hand it back either
        task = row(db_session, task_id)
        assert task.status == "running" and task.lease_owner == new.owner and task.worker_id == "worker-b"

    def test_a_stale_executor_that_starts_late_stops_without_touching_the_current_owners_runs(self, w, monkeypatch, db_session):
        (task_id,) = enqueue(w, 1)
        old = queue.claim_next("worker-a")
        self.age(db_session, task_id)
        executor.recover_interrupted_tasks()
        new = queue.claim_next("worker-b")
        db_session.execute(text(
            "INSERT INTO agent_runs (id, organization_id, task_id, sequence, agent_key, agent_version, instructions_hash, objective, status) "
            "VALUES ('run-live', :o, :t, 1, 'product_analyst', 'v', 'h', 'x', 'running')"), {"o": w.org.id, "t": task_id})
        db_session.commit()
        gw = ScriptedGateway(say_json(PRODUCT))
        gateway_for(monkeypatch, lambda db, o, u: gw)
        assert asyncio.run(executor.execute_task(task_id, w.org.id, owner=old.owner)) == "lost_lease"
        assert gw.requests == []
        db_session.expire_all()
        assert db_session.query(AgentRun).filter(AgentRun.id == "run-live").one().status == "running"        # untouched
        assert row(db_session, task_id).lease_owner == new.owner

    def test_a_live_lease_is_never_stolen_by_recovery(self, w, db_session):
        (task_id,) = enqueue(w, 1)
        queue.claim_next("w")
        assert executor.recover_interrupted_tasks()["recovered"] == []
        assert row(db_session, task_id).status == "running"

    def test_the_lease_is_renewed_by_the_executor_heartbeat(self, w, monkeypatch, db_session):
        monkeypatch.setattr(settings, "AGENT_HEARTBEAT_SECONDS", 0.05)
        (task_id,) = enqueue(w, 1)
        seen = []

        class Spy(ScriptedGateway):
            async def chat(self, operation, request):
                before = executor_beat(db_session, task_id)
                await asyncio.sleep(0.4)                                          # a long model call: only the heartbeat renews the lease
                seen.append((before, executor_beat(db_session, task_id)))
                return await super().chat(operation, request)
        gateway_for(monkeypatch, lambda db, o, u: Spy(say_json(PRODUCT)))
        assert drain() == {"completed": 1}
        assert seen and seen[0][1] > seen[0][0]


def executor_beat(db_session, task_id):
    db_session.expire_all()
    return db_session.execute(text("SELECT heartbeat_at FROM agent_tasks WHERE id = :i"), {"i": task_id}).scalar()


# ================================================================================================== the worker loop
class TestWorkerLoop:
    def test_the_worker_executes_what_the_queue_holds_and_then_exits_in_once_mode(self, w, monkeypatch, db_session):
        ids = enqueue(w, 3)
        gateway_for(monkeypatch, lambda db, o, u: ScriptedGateway(say_json(PRODUCT)))
        assert drain() == {"completed": 3}
        assert all(row(db_session, i).status == "completed" and row(db_session, i).attempts == 1 for i in ids)
        assert drain() == {}                                                     # nothing left: idempotent

    def test_tasks_run_in_parallel_up_to_the_worker_concurrency(self, w, monkeypatch, db_session):
        monkeypatch.setattr(settings, "AGENT_ORG_MAX_RUNNING", 10)
        monkeypatch.setattr(settings, "AGENT_GLOBAL_MAX_RUNNING", 10)
        probe = Inflight()
        gateway_for(monkeypatch, probe.factory)
        enqueue(w, 4)
        assert drain(concurrency=3) == {"completed": 4} and probe.peak == 3
        probe = Inflight()
        gateway_for(monkeypatch, probe.factory)
        enqueue(w, 2)
        assert drain(concurrency=1) == {"completed": 2} and probe.peak == 1

    def test_the_organization_limit_caps_parallelism_but_everything_still_completes(self, w, monkeypatch, db_session):
        monkeypatch.setattr(settings, "AGENT_ORG_MAX_RUNNING", 2)
        monkeypatch.setattr(settings, "AGENT_GLOBAL_MAX_RUNNING", 10)
        probe = Inflight()
        gateway_for(monkeypatch, probe.factory)
        enqueue(w, 5)
        assert drain(concurrency=5) == {"completed": 5}
        assert probe.peak_by_org[w.org.id] == 2

    def test_the_global_limit_caps_parallelism_across_organizations(self, w, monkeypatch, db_session):
        monkeypatch.setattr(settings, "AGENT_ORG_MAX_RUNNING", 5)
        monkeypatch.setattr(settings, "AGENT_GLOBAL_MAX_RUNNING", 3)
        probe = Inflight()
        gateway_for(monkeypatch, probe.factory)
        enqueue(w, 3)
        enqueue(w, 3, org=w.other, user=w.other_owner)
        assert drain(concurrency=6) == {"completed": 6} and probe.peak == 3

    def test_a_dead_workers_task_is_recovered_and_resumed_by_a_live_one(self, w, monkeypatch, db_session):
        (task_id,) = enqueue(w, 1)
        queue.claim_next("dead-worker")                                          # claimed, then the worker vanished
        gateway_for(monkeypatch, lambda db, o, u: ScriptedGateway(say_json(PRODUCT)))
        assert drain(recovery_interval=0) == {}                                  # lease still valid: left alone
        assert row(db_session, task_id).status == "running"
        db_session.execute(text("UPDATE agent_tasks SET lease_expires_at = now() - interval '1 minute' WHERE id = :i"), {"i": task_id})
        db_session.commit()
        assert drain(recovery_interval=0) == {"completed": 1}
        task = row(db_session, task_id)
        assert task.status == "completed" and task.attempts == 2

    def test_a_pending_task_that_was_cancelled_through_the_api_is_never_started(self, client, w, monkeypatch, db_session):
        gw = ScriptedGateway(say_json(PRODUCT))
        gateway_for(monkeypatch, lambda db, o, u: gw)
        task_id = client.post("/api/v1/agents/tasks", headers=w.h["manager"],
                              json={"objective": "Analyze the widget product", "agent_key": "product_analyst"}).json()["id"]
        assert client.post(f"/api/v1/agents/tasks/{task_id}/cancel", headers=w.h["manager"]).json()["status"] == "cancelled"
        assert drain() == {} and gw.requests == [] and row(db_session, task_id).status == "cancelled"

    def test_a_running_task_cancelled_mid_flight_ends_cancelled_before_the_next_model_call(self, w, monkeypatch, db_session):
        (task_id,) = enqueue(w, 1)

        def flag(_request):
            db_session.execute(text("UPDATE agent_tasks SET cancel_requested = true WHERE id = :i"), {"i": task_id})
            db_session.commit()
            return UseTools([("plan.note", {"note": "x"})])
        gw = ScriptedGateway(flag, say_json(PRODUCT))
        gateway_for(monkeypatch, lambda db, o, u: gw)
        assert drain() == {"cancelled": 1} and len(gw.requests) == 1
        task = row(db_session, task_id)
        assert task.status == "cancelled" and task.lease_owner is None

    def test_a_user_demoted_before_execution_fails_the_task_for_authorization(self, w, monkeypatch, db_session):
        (task_id,) = enqueue(w, 1)
        db_session.query(OrganizationMember).filter(OrganizationMember.user_id == w.users["manager"].id).update({"role": "viewer"})
        db_session.commit()
        gw = ScriptedGateway(say_json(PRODUCT))
        gateway_for(monkeypatch, lambda db, o, u: gw)
        assert drain() == {"failed": 1} and gw.requests == []
        assert row(db_session, task_id).error_kind == "authorization"

    def test_a_database_outage_does_not_kill_the_worker_and_is_reported_unhealthy(self, w, monkeypatch):
        calls = {"n": 0}

        def flaky(worker_id, *a, **k):
            calls["n"] += 1
            raise RuntimeError("connection refused password=hunter2hunter2")
        monkeypatch.setattr(queue, "claim_next", flaky)

        async def go():
            worker = AgentWorker(once=False, health_port=0, handle_signals=False, poll_interval=0.02)
            run = asyncio.get_running_loop().create_task(worker.run())
            await asyncio.sleep(0.2)
            report = worker.health()
            worker.stop()
            await run
            return report
        report = asyncio.run(go())
        assert calls["n"] >= 3 and report["database"] == "error" and report["status"] == "unhealthy"


# ================================================================================================== retries
class TestRetries:
    def test_a_transient_failure_goes_back_to_the_queue_with_backoff_and_then_completes(self, w, monkeypatch, db_session):
        (task_id,) = enqueue(w, 1)
        gateway_for(monkeypatch, lambda db, o, u: ScriptedGateway(ai_error("rate_limit"), say_json(PRODUCT)))
        assert drain() == {"retry": 1}
        task = row(db_session, task_id)
        assert task.status == "pending" and task.error_kind == "ai_rate_limit" and task.attempts == 1 and task.finished_at is None
        wait = (task.available_at - utcnow()).total_seconds()
        assert 20 < wait <= 30                                                   # AGENT_RETRY_BASE_SECONDS * 2^0
        assert drain() == {}                                                     # not claimable during the back-off
        db_session.execute(text("UPDATE agent_tasks SET available_at = now() - interval '1 second' WHERE id = :i"), {"i": task_id})
        db_session.commit()
        gateway_for(monkeypatch, lambda db, o, u: ScriptedGateway(say_json(PRODUCT)))
        assert drain() == {"completed": 1}
        task = row(db_session, task_id)
        assert task.status == "completed" and task.attempts == 2 and task.error_kind is None and task.available_at is None
        actions = [a.action for a in db_session.query(AuditLog).filter(AuditLog.target == task_id).order_by(AuditLog.created_at).all()]
        assert "agent.task.retry_scheduled" in actions and actions[-1] == "agent.task.completed"

    def test_the_backoff_doubles_with_each_attempt_and_is_capped(self, w, monkeypatch, db_session):
        monkeypatch.setattr(settings, "AGENT_RETRY_MAX_SECONDS", 50.0)
        (task_id,) = enqueue(w, 1)
        waits = []
        for _ in range(2):
            gateway_for(monkeypatch, lambda db, o, u: ScriptedGateway(ai_error("unavailable")))
            drain()
            task = row(db_session, task_id)
            waits.append((task.available_at - utcnow()).total_seconds())
            db_session.execute(text("UPDATE agent_tasks SET available_at = now() WHERE id = :i"), {"i": task_id})
            db_session.commit()
        assert 25 < waits[0] <= 30 and 45 < waits[1] <= 50                       # 30, then min(60, cap 50)

    def test_retries_stop_at_max_attempts_and_the_task_fails_with_its_kind(self, w, monkeypatch, db_session):
        monkeypatch.setattr(settings, "AGENT_MAX_ATTEMPTS", 2)
        monkeypatch.setattr(settings, "AGENT_RETRY_BASE_SECONDS", 0.0)
        (task_id,) = enqueue(w, 1)
        gateway_for(monkeypatch, lambda db, o, u: ScriptedGateway(ai_error("timeout")))
        assert drain() == {"retry": 1, "failed": 1}                              # no back-off: the 2nd attempt follows at once
        task = row(db_session, task_id)
        assert task.status == "failed" and task.error_kind == "ai_timeout" and task.attempts == 2 and task.finished_at is not None

    @pytest.mark.parametrize("kind", ["authentication", "quota", "budget_exceeded", "not_configured", "invalid_response", "permission"])
    def test_non_transient_errors_are_never_retried(self, w, monkeypatch, db_session, kind):
        (task_id,) = enqueue(w, 1)
        gateway_for(monkeypatch, lambda db, o, u: ScriptedGateway(ai_error(kind)))
        assert drain() == {"failed": 1}
        task = row(db_session, task_id)
        assert task.status == "failed" and task.attempts == 1 and task.available_at is None
        assert AE.AI_PREFIX + kind not in AE.RETRYABLE_KINDS

    def test_a_retry_resumes_without_repeating_the_steps_that_already_finished(self, w, monkeypatch, db_session):
        task_id = create_task_row(w.org, w.users["manager"], objective="Launch widgets on Google Search")
        first = ScriptedGateway(say_json(PLAN), say_json(PRODUCT), ai_error("unavailable"))
        gateway_for(monkeypatch, lambda db, o, u: first)
        assert drain() == {"retry": 1}
        db_session.execute(text("UPDATE agent_tasks SET available_at = now() WHERE id = :i"), {"i": task_id})
        db_session.commit()
        second = ScriptedGateway(say_json(COPY), say_json(REPORT))                # ONLY the unfinished work is asked again
        gateway_for(monkeypatch, lambda db, o, u: second)
        assert drain() == {"completed": 1}
        assert second.operations == ["agent.ad_copywriter", "agent.supervisor"] and second.script == []
        task, runs, _steps = snapshot(db_session, task_id)
        assert [(r.plan_step_id, r.status) for r in runs] == [("s1", "completed"), ("s2", "failed"), ("s2", "completed")]
        assert task.attempts == 2 and task.result["steps"][0]["output"]["summary"] == "A concise analysis."
        assert sum(1 for r in runs if r.plan_step_id == "s1") == 1                # the finished specialist ran exactly once

    def test_the_cost_cap_is_not_reset_by_a_retry(self, w, monkeypatch, db_session):
        (task_id,) = enqueue(w, 1, max_cost_usd=0.0015)
        gateway_for(monkeypatch, lambda db, o, u: ScriptedGateway(UseTools([("plan.note", {"note": "a"})], cost=1000), ai_error("unavailable")))
        assert drain() == {"retry": 1} and row(db_session, task_id).total_cost_micros == 1000
        db_session.execute(text("UPDATE agent_tasks SET available_at = now() WHERE id = :i"), {"i": task_id})
        db_session.commit()
        second = ScriptedGateway(UseTools([("plan.note", {"note": "b"})], cost=1000), say_json(PRODUCT))
        gateway_for(monkeypatch, lambda db, o, u: second)
        assert drain() == {"failed": 1}
        task = row(db_session, task_id)
        # 1000 from the first attempt + 1000 from the second > 1500: stopped before the 3rd paid call (a reset would have allowed it)
        assert task.error_kind == "cost_limit" and task.total_cost_micros == 2000 and len(second.requests) == 1

    def test_a_task_already_at_its_cap_makes_no_paid_call_when_it_is_claimed_again(self, w, monkeypatch, db_session):
        (task_id,) = enqueue(w, 1, max_cost_usd=0.001)
        db_session.execute(text("UPDATE agent_tasks SET total_cost_micros = 1000 WHERE id = :i"), {"i": task_id})
        db_session.commit()
        gw = ScriptedGateway(say_json(PRODUCT))
        gateway_for(monkeypatch, lambda db, o, u: gw)
        assert drain() == {"failed": 1} and gw.requests == []
        assert row(db_session, task_id).error_kind == "cost_limit"

    def test_an_unexpected_crash_is_retried_a_bounded_number_of_times_and_never_leaks_the_message(self, w, monkeypatch, db_session):
        monkeypatch.setattr(settings, "AGENT_RETRY_BASE_SECONDS", 0.0)
        monkeypatch.setattr(settings, "AGENT_MAX_ATTEMPTS", 2)
        (task_id,) = enqueue(w, 1)

        def explode(_r):
            raise RuntimeError("detail with password=hunter2hunter2")
        gateway_for(monkeypatch, lambda db, o, u: ScriptedGateway(explode, explode))
        drain()
        drain()
        task = row(db_session, task_id)
        assert task.status == "failed" and task.error_kind == "internal" and task.attempts == 2
        assert "hunter2" not in (task.error_message or "")


# ================================================================================================== graceful shutdown
class TestGracefulShutdown:
    def run_and_stop(self, monkeypatch, db_session, task_id, *, grace, stop_after_claim=True):
        async def go():
            worker = AgentWorker(once=False, health_port=0, handle_signals=False, poll_interval=0.02, shutdown_grace=grace, concurrency=1)
            run = asyncio.get_running_loop().create_task(worker.run())
            for _ in range(200):
                await asyncio.sleep(0.02)
                if worker.active:
                    await asyncio.sleep(0.15)                                   # let the model call start
                    break
            worker.stop()
            await run
            return worker
        return asyncio.run(go())

    def test_running_tasks_finish_inside_the_grace_period(self, w, monkeypatch, db_session):
        (task_id,) = enqueue(w, 1)
        gateway_for(monkeypatch, lambda db, o, u: ScriptedGateway(Delay(0.4, say_json(PRODUCT))))
        worker = self.run_and_stop(monkeypatch, db_session, task_id, grace=10)
        assert worker.processed == {"completed": 1} and row(db_session, task_id).status == "completed"

    def test_tasks_that_outlive_the_grace_are_handed_back_without_burning_an_attempt(self, w, monkeypatch, db_session):
        (task_id,) = enqueue(w, 1)
        gateway_for(monkeypatch, lambda db, o, u: ScriptedGateway(Delay(30, say_json(PRODUCT))))
        worker = self.run_and_stop(monkeypatch, db_session, task_id, grace=0.2)
        assert worker.processed == {"released": 1}
        task = row(db_session, task_id)
        assert task.status == "pending" and task.attempts == 0 and task.lease_owner is None and task.available_at is None
        assert "agent.task.released" in [a.action for a in db_session.query(AuditLog).filter(AuditLog.target == task_id).all()]
        gateway_for(monkeypatch, lambda db, o, u: ScriptedGateway(say_json(PRODUCT)))      # another worker resumes it immediately
        assert drain() == {"completed": 1}
        assert row(db_session, task_id).attempts == 1


# ================================================================================================== tenant isolation
class TestTenantIsolation:
    def test_concurrent_tasks_of_two_organizations_run_in_their_own_context_and_never_mix(self, w, monkeypatch, db_session):
        from app.models import Brand
        for org, name in ((w.org, "AlphaBrand"), (w.other, "BetaBrand")):
            db_session.add(Brand(name=name, organization_id=org.id))
        db_session.commit()
        a, = enqueue(w, 1)
        b, = enqueue(w, 1, org=w.other, user=w.other_owner)
        seen = {}

        def factory(db, org_id, user_id):
            seen[org_id] = (db.info.get("org_id"), user_id)

            def ask(request):
                return UseTools([("catalog.list_brands", {})])
            return ScriptedGateway(ask, say_json(PRODUCT))
        gateway_for(monkeypatch, factory)
        assert drain(concurrency=2) == {"completed": 2}
        assert seen[w.org.id] == (w.org.id, w.users["manager"].id) and seen[w.other.id] == (w.other.id, w.other_owner.id)
        for task_id, mine, theirs, org in ((a, "AlphaBrand", "BetaBrand", w.org), (b, "BetaBrand", "AlphaBrand", w.other)):
            task, runs, steps = snapshot(db_session, task_id)
            blob = json.dumps([s.output for s in steps], default=str)
            assert mine in blob and theirs not in blob
            assert task.organization_id == org.id and all(r.organization_id == org.id for r in runs) and all(s.organization_id == org.id for s in steps)
            assert all(x.organization_id == org.id for x in db_session.query(AuditLog).filter(AuditLog.target == task_id).all())

    def test_a_task_of_one_organization_cannot_be_released_recovered_or_cancelled_from_the_other(self, w, db_session):
        (mine,) = enqueue(w, 1)
        (theirs,) = enqueue(w, 1, org=w.other, user=w.other_owner)
        queue.claim_next("w")
        queue.claim_next("w")
        db_session.execute(text("UPDATE agent_tasks SET lease_expires_at = now() - interval '1 minute'"))
        db_session.commit()
        outcome = executor.recover_interrupted_tasks(org_id=w.other.id)
        assert [r[0] for r in outcome["recovered"]] == [theirs] and row(db_session, mine).status == "running"
        assert executor.request_cancel(executor._session(w.other.id), w.other.id, mine) is None        # RLS + filter: not found for the other org

    def test_the_worker_database_role_is_the_same_non_bypass_role_as_the_api(self, w):
        from sqlalchemy import text as t
        from app.database import SessionLocal, bind_context
        db = SessionLocal()
        bind_context(db, w.org.id)
        try:
            assert db.execute(t("SELECT count(*) FROM agent_tasks")).scalar() == 0
            enqueue(w, 1, org=w.other, user=w.other_owner)
            assert db.execute(t("SELECT count(*) FROM agent_tasks")).scalar() == 0           # RLS still hides the other tenant
            assert db.execute(t("SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user")).scalar() is False
        finally:
            db.close()


# ================================================================================================== logs, health, architecture
class TestObservability:
    def capture(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonFormatter())
        root = logging.getLogger()
        root.addHandler(handler)
        previous = root.level
        root.setLevel(logging.INFO)
        for name in ("agent_worker", "app.agents.executor"):
            logging.getLogger(name).disabled = False      # alembic's fileConfig (run by the suite's migrations) disables pre-existing loggers
        return stream, lambda: (root.removeHandler(handler), root.setLevel(previous))

    def test_logs_are_json_lines_without_prompts_objectives_or_secrets(self, w, monkeypatch, db_session):
        secret_objective = "Analyze CONFIDENTIAL-LAUNCH-PLAN with key sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789"
        task_id = create_task_row(w.org, w.users["manager"], agent_key="product_analyst", objective=secret_objective)
        stream, undo = self.capture()

        def explode(_r):
            raise RuntimeError("provider said: password=hunter2hunter2 CONFIDENTIAL-LAUNCH-PLAN")
        gateway_for(monkeypatch, lambda db, o, u: ScriptedGateway(explode))
        try:
            drain()
        finally:
            undo()
        text_out = stream.getvalue()
        lines = [json.loads(line) for line in text_out.splitlines() if line.strip()]
        events = [line.get("event") for line in lines]
        assert "worker.started" in events and "task.claimed" in events and "task.finished" in events and "worker.stopped" in events
        for forbidden in ("CONFIDENTIAL-LAUNCH-PLAN", "sk-ant-api03", "hunter2", "Traceback", "password="):
            assert forbidden not in text_out, forbidden
        finished = next(line for line in lines if line.get("event") == "task.finished")
        assert finished["task"] == task_id and finished["org"] == w.org.id and finished["status"] == "retry"

    def test_the_formatter_logs_an_exception_by_type_only(self):
        record = logging.LogRecord("x", logging.ERROR, __file__, 1, "boom password=hunter2hunter2", None, None)
        try:
            raise ValueError("secret detail")
        except ValueError:
            import sys
            record.exc_info = sys.exc_info()
        out = json.loads(JsonFormatter().format(record))
        assert out["exception_type"] == "ValueError" and "secret detail" not in json.dumps(out) and "hunter2" not in json.dumps(out)

    def test_extra_fields_are_redacted_and_cannot_overwrite_reserved_keys(self):
        stream, undo = self.capture()
        try:
            log_event(logging.getLogger("agent_worker"), "probe", note="Bearer abcdefghijklmnopqrstuvwxyz0123456789", ts="forged", n=3)
        finally:
            undo()
        line = json.loads(stream.getvalue().splitlines()[-1])
        assert line["event"] == "probe" and line["level"] == "info" and line["ts"] != "forged" and line["n"] == 3 and "abcdefghijklmnopqrstuvwxyz0123456789" not in json.dumps(line)

    def test_health_endpoint_reports_ok_then_stopping_and_exposes_no_tenant_data(self, w, monkeypatch):
        import socket
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        monkeypatch.setattr(settings, "AGENT_WORKER_HEALTH_PORT", port)
        enqueue(w, 1)
        gateway_for(monkeypatch, lambda db, o, u: ScriptedGateway(Delay(0.6, say_json(PRODUCT))))
        result = {}

        async def go():
            worker = AgentWorker(once=False, health_port=port, handle_signals=False, poll_interval=0.02, shutdown_grace=5, concurrency=1)
            run = asyncio.get_running_loop().create_task(worker.run())
            for _ in range(100):
                await asyncio.sleep(0.05)
                if worker.active:
                    break

            def fetch(path):
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=3) as resp:
                        return resp.status, json.loads(resp.read())
                except urllib.error.HTTPError as exc:
                    return exc.code, json.loads(exc.read())
            result["ok"] = await asyncio.to_thread(fetch, "/healthz")
            result["missing"] = await asyncio.to_thread(fetch, "/nope")
            result["cli"] = await asyncio.to_thread(healthcheck)
            monkeypatch.setattr(settings, "AGENT_WORKER_STALE_SECONDS", -1.0)      # a loop that did not tick "recently" is unhealthy
            await asyncio.sleep(0.05)
            result["stale"] = await asyncio.to_thread(fetch, "/healthz")
            monkeypatch.setattr(settings, "AGENT_WORKER_STALE_SECONDS", 60.0)
            worker.stop()
            await run
        asyncio.run(go())
        code, body = result["ok"]
        assert code == 200 and body["status"] == "ok" and body["active_tasks"] == 1 and body["database"] == "ok"
        assert set(body) == {"status", "worker", "active_tasks", "concurrency", "database", "last_poll_seconds_ago", "uptime_seconds"}
        assert result["missing"][0] == 404 and result["cli"] == 0 and result["stale"][0] == 503 and result["stale"][1]["status"] == "unhealthy"

    def test_the_healthcheck_command_fails_when_no_worker_is_listening(self, monkeypatch):
        import socket
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        monkeypatch.setattr(settings, "AGENT_WORKER_HEALTH_PORT", port)
        assert healthcheck() == 1


class TestArchitecture:
    def source(self, path):
        from pathlib import Path
        return (Path(__file__).resolve().parents[2] / path).read_text(encoding="utf-8")

    def test_the_api_only_enqueues_it_never_executes_tasks(self):
        api = self.source("app/api/v1/agents.py")
        assert "BackgroundTasks" not in api and "execute_task" not in api and "schedule(" not in api and "asyncio.create_task" not in api
        main = self.source("app/main.py")
        assert "recover_and_resume" not in main and "AGENT_RECOVER_ON_STARTUP" not in main and "execute_task" not in main
        assert not hasattr(executor, "schedule") and not hasattr(executor, "recover_and_resume")

    def test_the_worker_reuses_the_executor_and_has_no_agent_logic_of_its_own(self):
        worker = self.source("app/worker/worker.py")
        assert "executor.execute_task" in worker and "queue.claim_next" in worker
        for forbidden in ("run_supervised", "run_single", "AGENTS[", "httpx", "anthropic", "openai", "gemini", "gpt-", "claude-"):
            assert forbidden not in worker, forbidden

    def test_only_the_worker_module_claims_from_the_queue_and_it_is_not_importable_by_http_handlers(self):
        import ast
        from pathlib import Path
        root = Path(__file__).resolve().parents[2] / "app"
        for path in root.rglob("*.py"):
            if "worker" in path.parts or path.name == "queue.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module in ("app.agents.queue", "app.worker.worker"):
                    pytest.fail(f"{path} imports {node.module}")
                if isinstance(node, ast.ImportFrom) and node.module == "app.agents" and any(a.name == "queue" for a in node.names):
                    pytest.fail(f"{path} imports app.agents.queue")

    def test_the_new_migration_is_the_single_head_and_adds_only_queue_columns(self):
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        from pathlib import Path
        backend = Path(__file__).resolve().parents[2]
        cfg = Config(str(backend / "alembic.ini"))
        cfg.set_main_option("script_location", str(backend / "alembic"))
        script = ScriptDirectory.from_config(cfg)
        assert script.get_heads() == ["a3b4c5d6e7f8"]
        assert script.get_revision("f2a3b4c5d6e7").down_revision == "e1f2a3b4c5d6"
        assert script.get_revision("a3b4c5d6e7f8").down_revision == "f2a3b4c5d6e7"

    def test_agent_tasks_has_the_queue_columns_and_the_claim_index(self, db_session):
        cols = {r[0] for r in db_session.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'agent_tasks'"))}
        assert {"available_at", "worker_id"} <= cols
        idx = db_session.execute(text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_agent_tasks_claimable'")).scalar()
        assert idx and "pending" in idx
