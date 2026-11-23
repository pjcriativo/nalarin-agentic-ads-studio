"""Interrupted AI calls (etapa 02.5): a model call that is cancelled or times out while the provider may already be
processing it must keep its COST accounted (budget, usage log, task totals) and must never be silently released.

Real AI Gateway + real budgets + a slow fake adapter (no network); the agent-level cases use the real worker loop."""
import asyncio
import json

import pytest
from sqlalchemy import text

from app.ai import errors as E
from app.ai import types as T
from app.ai.adapters.base import ProviderAdapter
from app.ai.gateway import AIGateway
from app.core.config import settings
from app.models import AiBudget, AiUsageLog, AuditLog
from tests.unit.agents_support import (PRODUCT, Delay, ScriptedGateway, create_task_row, drain, say_json, set_limits, snapshot, world_of)
from tests.unit.ai_support import gateway_for, seed_credential, seed_model

PRICED = dict(input_price_micros_per_mtok=3_000_000, output_price_micros_per_mtok=15_000_000)


class SlowAdapter(ProviderAdapter):
    """A provider call that never returns on its own (the awaiting task is cancelled or times out)."""
    name = "openai"

    def __init__(self):
        self.started = asyncio.Event()

    async def chat(self, request, model_id, api_key, timeout):
        self.started.set()
        await asyncio.sleep(20)                       # never reached by a passing test (cancelled first)

    async def list_models(self, api_key, timeout):
        return []

    def _headers(self, api_key):
        return {}

    def _error_kind(self, status, body):
        return E.UNKNOWN


@pytest.fixture
def org(make_user, make_org):
    return make_org("Interrupted AI", owner=make_user("interrupted-ai@example.com"))


def setup(db_session, org, daily=10_000_000, priced=True):
    seed_credential(db_session, org.id, "openai")
    seed_model(db_session, org.id, "openai", "m", **(PRICED if priced else {}))
    if daily is not None:
        from app.ai.budget import window_start
        db_session.add(AiBudget(organization_id=org.id, period="daily", limit_micros=daily, window_start=window_start("daily")))
        db_session.commit()


def budget(db_session, org):
    db_session.expire_all()
    return {b.period: (b.spent_micros, b.reserved_micros) for b in db_session.query(AiBudget).filter(AiBudget.organization_id == org.id)}


def request():
    return T.ChatRequest(messages=[T.Message.user("Hello there")], max_tokens=100)


def reservations(db_session, org):
    return db_session.execute(text("SELECT count(*) FROM ai_budget_reservations WHERE organization_id = :o"), {"o": org.id}).scalar()


async def interrupt(gateway, adapter):
    """Start a call, wait until the provider call is in flight, then cancel the awaiting task."""
    task = asyncio.get_running_loop().create_task(gateway.chat("op.test", request()))
    await asyncio.wait_for(adapter.started.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


class TestGatewayCharge:
    @pytest.mark.parametrize("how", ["cancel", "wait_for_timeout"])
    def test_an_interrupted_call_is_charged_at_its_estimate_not_released(self, org, db_session, how):
        setup(db_session, org)
        adapter = SlowAdapter()
        gateway, db = gateway_for(org.id, None, adapters={"openai": adapter})

        async def go():
            if how == "cancel":
                await interrupt(gateway, adapter)
            else:
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(gateway.chat("op.test", request()), timeout=0.3)
        try:
            asyncio.run(go())
        finally:
            db.close()
        spent, reserved = budget(db_session, org)["daily"]
        assert spent > 0 and reserved == 0                              # CHARGED (the provider may have billed), nothing left reserved
        assert reservations(db_session, org) == 0                       # no dangling reservation waiting for the TTL
        assert gateway.interrupted_cost_micros == spent
        log = db_session.query(AiUsageLog).filter(AiUsageLog.organization_id == org.id).one()
        assert (log.status, log.error_kind, log.cost_micros, log.estimated_cost_micros) == ("error", "interrupted", spent, spent)

    def test_the_charge_counts_against_the_limit_for_the_next_call(self, org, db_session):
        setup(db_session, org, daily=40_000)
        adapter = SlowAdapter()
        gateway, db = gateway_for(org.id, None, adapters={"openai": adapter})
        try:
            asyncio.run(interrupt(gateway, adapter))
            spent = budget(db_session, org)["daily"][0]
            assert 0 < spent <= 40_000
            second = SlowAdapter()
            again, db2 = gateway_for(org.id, None, adapters={"openai": second})
            try:
                with pytest.raises(E.AIError) as caught:
                    asyncio.run(again.chat("op.test", T.ChatRequest(messages=[T.Message.user("x" * 4000)], max_tokens=4000)))
            finally:
                db2.close()
            assert caught.value.kind == E.BUDGET_EXCEEDED and not second.started.is_set()      # blocked BEFORE any provider call
        finally:
            db.close()

    def test_without_a_price_nothing_is_invented_but_the_interruption_is_still_logged(self, org, db_session):
        setup(db_session, org, daily=None, priced=False)
        adapter = SlowAdapter()
        gateway, db = gateway_for(org.id, None, adapters={"openai": adapter})
        try:
            asyncio.run(interrupt(gateway, adapter))
        finally:
            db.close()
        assert gateway.interrupted_cost_micros is None
        log = db_session.query(AiUsageLog).filter(AiUsageLog.organization_id == org.id).one()
        assert (log.status, log.error_kind, log.cost_micros) == ("error", "interrupted", None)

    def test_a_later_successful_call_clears_the_interruption_marker(self, org, db_session, provider_mock):
        from tests.unit.ai_support import jr, openai_ok
        setup(db_session, org, daily=None)
        adapter = SlowAdapter()
        gateway, db = gateway_for(org.id, None, adapters={"openai": adapter})
        try:
            asyncio.run(interrupt(gateway, adapter))
            assert gateway.interrupted_cost_micros is not None
        finally:
            db.close()
        real, db2 = gateway_for(org.id, None)
        provider_mock.queue("openai", jr(200, openai_ok("fine")))
        try:
            real.interrupted_cost_micros = 123
            assert asyncio.run(real.chat("op.test", request())).response.text == "fine" and real.interrupted_cost_micros is None
        finally:
            db2.close()


class Interruptible(ScriptedGateway):
    """Scripted gateway that, like the real one, records what an interrupted call was charged."""
    CHARGE = 4200

    def __init__(self, *script):
        super().__init__(*script)
        self.interrupted_cost_micros = None

    async def chat(self, operation, request):
        self.interrupted_cost_micros = None
        try:
            return await super().chat(operation, request)
        except asyncio.CancelledError:
            self.interrupted_cost_micros = self.CHARGE
            raise


@pytest.fixture
def w(make_user, make_org, add_member, login_headers):
    return world_of(make_user, make_org, add_member, login_headers)


@pytest.fixture(autouse=True)
def roomy(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_MAX_ACTIVE_TASKS_PER_ORG", 100)
    monkeypatch.setattr(settings, "AGENT_RETRY_BASE_SECONDS", 30.0)


def use(monkeypatch, factory):
    from app.agents import executor
    monkeypatch.setattr(executor, "GATEWAY_FACTORY", factory)


class TestTaskAccounting:
    def test_a_model_call_that_times_out_keeps_its_charge_on_the_step_and_the_task(self, w, monkeypatch, db_session):
        set_limits(monkeypatch, "product_analyst", timeout_seconds=1)
        task_id = create_task_row(w.org, w.users["manager"], agent_key="product_analyst", objective="Analyze the widget product for ads")
        use(monkeypatch, lambda db, o, u: Interruptible(Delay(30, say_json(PRODUCT))))
        assert drain() == {"failed": 1}
        task, runs, steps = snapshot(db_session, task_id)
        assert task.error_kind == "timeout" and task.total_cost_micros == 4200
        llm = [s for s in steps if s.kind == "llm"]
        assert [(s.status, s.error_kind, s.cost_micros) for s in llm] == [("error", "timeout", 4200)]
        assert runs[0].total_cost_micros == 4200                                  # also on the run, not just the task

    def test_a_worker_shutdown_mid_call_keeps_the_charge_and_the_resumed_task_still_obeys_the_cap(self, w, monkeypatch, db_session):
        task_id = create_task_row(w.org, w.users["manager"], agent_key="product_analyst",
                                  objective="Analyze the widget product for ads", max_cost_usd=0.0045)
        use(monkeypatch, lambda db, o, u: Interruptible(Delay(30, say_json(PRODUCT))))
        from app.worker.worker import AgentWorker

        async def go():
            worker = AgentWorker(once=False, health_port=0, handle_signals=False, poll_interval=0.02, shutdown_grace=0.2, concurrency=1)
            run = asyncio.get_running_loop().create_task(worker.run())
            for _ in range(200):
                await asyncio.sleep(0.02)
                if worker.active:
                    await asyncio.sleep(0.2)
                    break
            worker.stop()
            await run
            return worker
        worker = asyncio.run(go())
        assert worker.processed == {"released": 1}
        task, _runs, steps = snapshot(db_session, task_id)
        assert task.status == "pending" and task.attempts == 0 and task.total_cost_micros == 4200    # released, but the charge stays
        assert [(s.error_kind, s.cost_micros) for s in steps if s.kind == "llm"] == [("interrupted", 4200)]
        # another worker resumes it: 4200 spent + the next call (1000) would pass the 4500 cap -> stops BEFORE a second paid call
        again = Interruptible(UseTools_note(), say_json(PRODUCT))
        use(monkeypatch, lambda db, o, u: again)
        assert drain() == {"failed": 1}
        task, *_ = snapshot(db_session, task_id)
        assert task.error_kind == "cost_limit" and task.total_cost_micros == 5200 and len(again.requests) == 1

    def test_a_task_whose_interrupted_call_already_hit_the_cap_makes_no_further_paid_call(self, w, monkeypatch, db_session):
        task_id = create_task_row(w.org, w.users["manager"], agent_key="product_analyst",
                                  objective="Analyze the widget product for ads", max_cost_usd=0.004)
        db_session.execute(text("UPDATE agent_tasks SET total_cost_micros = 4200 WHERE id = :i"), {"i": task_id})
        db_session.commit()
        gw = Interruptible(say_json(PRODUCT))
        use(monkeypatch, lambda db, o, u: gw)
        assert drain() == {"failed": 1} and gw.requests == []
        assert snapshot(db_session, task_id)[0].error_kind == "cost_limit"


def UseTools_note():
    from tests.unit.agents_support import UseTools
    return UseTools([("plan.note", {"note": "x"})], cost=1000)
