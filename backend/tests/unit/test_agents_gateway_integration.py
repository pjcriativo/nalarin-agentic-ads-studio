"""Agents on the REAL AI Gateway: organization credentials, routing per operation, fallback, budgets and accounting.

The providers' HTTP endpoints are scripted with ``provider_mock`` (the providers' real wire formats): nothing reaches a
paid API, so this proves the INTEGRATION, not that Anthropic/Gemini/OpenAI accept these requests in production."""
import asyncio
import json
import re
import threading
import time
from pathlib import Path

import pytest

from app.agents import executor
from app.ai.budget import window_start
from app.models import AiBudget, AiRouting, AiUsageLog
from tests.unit.agents_support import COPY, PLAN, PRODUCT, REPORT, create_task_row, snapshot, world_of
from tests.unit.ai_support import KEYS, anthropic_ok, jr, openai_ok, seed_credential, seed_model

PRICED = dict(input_price_micros_per_mtok=3_000_000, output_price_micros_per_mtok=15_000_000, supports_tools=True)
RATE_LIMIT = {"error": {"message": "slow down", "type": "rate_limit_error", "code": "rate_limit_exceeded"}}


@pytest.fixture
def w(make_user, make_org, add_member, login_headers):
    return world_of(make_user, make_org, add_member, login_headers)


def configure(db_session, org, providers=("openai",), **model_kw):
    models = {}
    for provider in providers:
        seed_credential(db_session, org.id, provider)
        models[provider] = seed_model(db_session, org.id, provider, f"model-{provider}", **{**PRICED, **model_kw})
    return models


def say(payload) -> dict:
    return openai_ok(json.dumps(payload))


def execute(task_id, org):
    return asyncio.run(executor.execute_task(task_id, org.id))


def usage_rows(db_session, org):
    db_session.expire_all()
    return db_session.query(AiUsageLog).filter(AiUsageLog.organization_id == org.id).order_by(AiUsageLog.created_at, AiUsageLog.attempt).all()


class TestRealGateway:
    def test_single_agent_with_tool_calling_through_the_openai_wire_format(self, w, db_session, provider_mock):
        configure(db_session, w.org)
        provider_mock.queue("openai", jr(200, openai_ok(None, tool_calls=[{"id": "c1", "name": "plan.note", "args": {"note": "check"}}])),
                            jr(200, say(PRODUCT)))
        task_id = create_task_row(w.org, w.users["manager"], agent_key="product_analyst")
        assert execute(task_id, w.org) == "completed"
        first, second = (provider_mock.body(r) for r in provider_mock.of("openai"))
        offered = {t["function"]["name"] for t in first["tools"]}
        assert "plan.note" in offered and not any(n.startswith("google_ads.") for n in offered)
        assert first["messages"][0]["role"] == "system" and "SECURITY RULES" in first["messages"][0]["content"]
        tool_message = next(m for m in second["messages"] if m["role"] == "tool")
        assert tool_message["tool_call_id"] == "c1" and tool_message["content"].startswith("<untrusted_tool_output")
        task, runs, _steps = snapshot(db_session, task_id)
        logs = usage_rows(db_session, w.org)
        assert [(l.operation, l.status, l.user_id) for l in logs] == [("agent.product_analyst", "success", w.users["manager"].id)] * 2
        assert task.total_cost_micros == sum(l.cost_micros for l in logs) > 0 and task.total_input_tokens == sum(l.input_tokens for l in logs)

    def test_supervised_flow_runs_on_the_organizations_credentials_and_accounts_each_operation(self, w, db_session, provider_mock):
        configure(db_session, w.org)
        provider_mock.queue("openai", jr(200, say(PLAN)), jr(200, say(PRODUCT)), jr(200, say(COPY)), jr(200, say(REPORT)))
        task_id = create_task_row(w.org, w.users["manager"], objective="Launch widgets on Google Search")
        assert execute(task_id, w.org) == "completed"
        bodies = [provider_mock.body(r) for r in provider_mock.of("openai")]
        assert "tools" not in bodies[0] and "tools" not in bodies[3]                  # planning and consolidation use no tools
        assert "tools" in bodies[1] and "tools" in bodies[2]
        assert [l.operation for l in usage_rows(db_session, w.org)] == ["agent.supervisor", "agent.product_analyst", "agent.ad_copywriter", "agent.supervisor"]
        assert {r.headers["authorization"] for r in provider_mock.of("openai")} == {f"Bearer {KEYS['openai']}"}
        assert snapshot(db_session, task_id)[0].result["report"]["summary"] == "Consolidated."

    def test_the_provider_and_model_of_each_agent_come_from_the_organizations_routing(self, w, db_session, provider_mock):
        models = configure(db_session, w.org, providers=("openai", "anthropic"))
        db_session.add(AiRouting(organization_id=w.org.id, operation="agent.product_analyst", primary_model_id=models["anthropic"].id, fallback_model_ids=[]))
        db_session.add(AiRouting(organization_id=w.org.id, operation="default", primary_model_id=models["openai"].id, fallback_model_ids=[]))
        db_session.commit()
        provider_mock.queue("anthropic", jr(200, anthropic_ok(json.dumps(PRODUCT))))
        provider_mock.queue("openai", jr(200, say(PLAN)), jr(200, say(COPY)), jr(200, say(REPORT)))
        task_id = create_task_row(w.org, w.users["manager"], objective="Launch widgets on Google Search")
        assert execute(task_id, w.org) == "completed"
        assert len(provider_mock.of("anthropic")) == 1 and len(provider_mock.of("openai")) == 3          # the analyst ran on a different provider
        assert provider_mock.body(provider_mock.of("anthropic")[0])["model"] == "model-anthropic"
        assert {l.provider for l in usage_rows(db_session, w.org)} == {"openai", "anthropic"}

    def test_gateway_fallback_keeps_an_agent_alive_when_its_primary_provider_is_rate_limited(self, w, db_session, provider_mock):
        models = configure(db_session, w.org, providers=("openai", "anthropic"))
        db_session.add(AiRouting(organization_id=w.org.id, operation="agent.product_analyst", primary_model_id=models["openai"].id,
                                 fallback_model_ids=[models["anthropic"].id]))
        db_session.commit()
        provider_mock.queue("openai", jr(429, RATE_LIMIT))
        provider_mock.queue("anthropic", jr(200, anthropic_ok(json.dumps(PRODUCT))))
        task_id = create_task_row(w.org, w.users["manager"], agent_key="product_analyst")
        assert execute(task_id, w.org) == "completed"
        logs = usage_rows(db_session, w.org)
        assert [(l.provider, l.status, l.attempt) for l in logs] == [("openai", "error", 1), ("anthropic", "success", 2)]
        assert snapshot(db_session, task_id)[0].status == "completed"

    def test_an_organization_without_credentials_gets_a_clear_failure_and_no_provider_call(self, w, db_session, provider_mock):
        seed_model(db_session, w.org.id, "openai", "model-openai", **PRICED)             # model but NO credential
        task_id = create_task_row(w.org, w.users["manager"], agent_key="product_analyst")
        assert execute(task_id, w.org) == "failed"
        task, runs, _ = snapshot(db_session, task_id)
        assert task.error_kind == "ai_not_configured" and provider_mock.requests == [] and runs[0].error_kind == "ai_not_configured"

    def test_a_model_without_tool_support_cannot_serve_a_tool_using_agent(self, w, db_session, provider_mock):
        configure(db_session, w.org, supports_tools=False)
        provider_mock.queue("openai", jr(200, say(PLAN)))                              # planning needs no tools: it runs
        task_id = create_task_row(w.org, w.users["manager"], objective="Launch widgets on Google Search")
        assert execute(task_id, w.org) == "failed"
        task, *_ = snapshot(db_session, task_id)
        assert task.error_kind == "ai_not_configured" and len(provider_mock.requests) == 1

    def test_each_organization_uses_its_own_key_for_its_agents(self, w, db_session, provider_mock):
        seed_credential(db_session, w.org.id, "openai", key="sk-org-A-key-AAAAAAAAAA")
        seed_credential(db_session, w.other.id, "openai", key="sk-org-B-key-BBBBBBBBBB")
        seed_model(db_session, w.org.id, "openai", "m", **PRICED)
        seed_model(db_session, w.other.id, "openai", "m", **PRICED)
        provider_mock.always("openai", lambda r: jr(200, say(PRODUCT)))
        a = create_task_row(w.org, w.users["manager"], agent_key="product_analyst")
        b = create_task_row(w.other, w.other_owner, agent_key="product_analyst")
        assert (execute(a, w.org), execute(b, w.other)) == ("completed", "completed")
        assert [r.headers["authorization"] for r in provider_mock.of("openai")] == ["Bearer sk-org-A-key-AAAAAAAAAA", "Bearer sk-org-B-key-BBBBBBBBBB"]
        assert {l.organization_id for l in usage_rows(db_session, w.org)} == {w.org.id}

    def test_usage_records_hold_no_prompt_or_answer_text(self, w, db_session, provider_mock):
        configure(db_session, w.org)
        provider_mock.queue("openai", jr(200, say(PRODUCT)))
        create_and_run = create_task_row(w.org, w.users["manager"], objective="Top secret launch of the ZEPHYR widget", agent_key="product_analyst")
        execute(create_and_run, w.org)
        dump = json.dumps([{c.name: str(getattr(l, c.name)) for c in AiUsageLog.__table__.columns} for l in usage_rows(db_session, w.org)])
        assert "ZEPHYR" not in dump and "SECURITY RULES" not in dump and "concise analysis" not in dump


class TestBudgets:
    def test_an_exhausted_budget_stops_the_task_before_any_provider_call(self, w, db_session, provider_mock):
        configure(db_session, w.org)
        db_session.add(AiBudget(organization_id=w.org.id, period="daily", limit_micros=1, window_start=window_start("daily")))
        db_session.commit()
        task_id = create_task_row(w.org, w.users["manager"], agent_key="product_analyst")
        assert execute(task_id, w.org) == "failed"
        task, *_ = snapshot(db_session, task_id)
        assert task.error_kind == "ai_budget_exceeded" and provider_mock.requests == []

    def test_spend_is_charged_to_the_budget_and_no_reservation_is_left_behind(self, w, db_session, provider_mock):
        configure(db_session, w.org)
        db_session.add(AiBudget(organization_id=w.org.id, period="daily", limit_micros=50_000_000, window_start=window_start("daily")))
        db_session.commit()
        provider_mock.queue("openai", jr(200, openai_ok(json.dumps(PRODUCT), inp=1000, out=500)))
        task_id = create_task_row(w.org, w.users["manager"], agent_key="product_analyst")
        assert execute(task_id, w.org) == "completed"
        db_session.expire_all()
        budget = db_session.query(AiBudget).one()
        assert (budget.spent_micros, budget.reserved_micros) == (10_500, 0)                # 1000 in * $3/M + 500 out * $15/M
        assert snapshot(db_session, task_id)[0].total_cost_micros == 10_500

    def test_concurrent_agent_tasks_can_never_exceed_the_organizations_budget(self, w, db_session, provider_mock):
        """Two tasks start together; the budget has room for ONE worst-case reservation. Exactly one may proceed."""
        configure(db_session, w.org, input_price_micros_per_mtok=0, output_price_micros_per_mtok=1_000_000_000)
        db_session.add(AiBudget(organization_id=w.org.id, period="daily", limit_micros=3_000_000, window_start=window_start("daily")))
        db_session.commit()

        def slow(_request):                         # the first call holds its reservation while the second one tries to reserve
            time.sleep(0.8)
            return jr(200, openai_ok(json.dumps(PRODUCT), inp=0, out=5))
        provider_mock.always("openai", slow)
        ids = [create_task_row(w.org, w.users["manager"], agent_key="product_analyst", objective=f"Analyze product number {i}") for i in range(2)]
        outcomes, barrier = [], threading.Barrier(2)

        def attempt(task_id):
            barrier.wait()
            outcomes.append(asyncio.run(executor.execute_task(task_id, w.org.id)))
        threads = [threading.Thread(target=attempt, args=(i,)) for i in ids]
        [t.start() for t in threads]
        [t.join(120) for t in threads]
        assert sorted(outcomes) == ["completed", "failed"] and len(provider_mock.of("openai")) == 1
        kinds = sorted(snapshot(db_session, i)[0].error_kind or "" for i in ids)
        assert kinds == ["", "ai_budget_exceeded"]
        db_session.expire_all()
        assert db_session.query(AiBudget).one().reserved_micros == 0


class TestNoDirectProviderAccess:
    ROOT = Path(__file__).resolve().parents[2] / "app" / "agents"

    def test_agent_code_never_imports_an_http_client_or_a_provider_adapter(self):
        offenders = [f.name for f in self.ROOT.glob("*.py")
                     if re.search(r"^\s*(?:import|from)\s+(?:httpx|requests|aiohttp|openai|anthropic|google\.generativeai|app\.ai\.adapters)", f.read_text(encoding="utf-8"), re.M)]
        assert offenders == []

    def test_agent_code_has_no_model_names_or_provider_keys(self):
        pattern = re.compile(r"(claude-(?:opus|sonnet|haiku|fable)|gpt-[0-9o]|\bo[1-9]-|gemini-[0-9]|sk-ant-|AIza[0-9A-Za-z_\-]{20})", re.I)
        hits = [f"{f.name}: {m.group(0)}" for f in self.ROOT.glob("*.py") for m in pattern.finditer(f.read_text(encoding="utf-8"))]
        assert hits == []

    def test_agents_use_gateway_operations_not_model_ids(self):
        from app.agents.registry import AGENTS, SUPERVISOR
        assert {a.operation for a in AGENTS.values()} | {SUPERVISOR.operation} == {f"agent.{k}" for k in (*AGENTS, "supervisor")}
