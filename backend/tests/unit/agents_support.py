"""Test doubles for the agent system. NOTHING here reaches a real AI provider or a real web page.

* ``ScriptedGateway`` stands in for the AI Gateway in logic tests (loops, limits, cancellation...): it replays a script
  of model answers and records every request. Tests that must prove the REAL gateway integration (routing, budget,
  fallback, accounting) use ``provider_mock`` + the real ``AIGateway`` instead (test_agents_gateway_integration.py).
* Valid final answers for every output schema, so a test can finish a run without caring about content.
"""
import asyncio
import json
import uuid
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable, List, Optional

from app.ai import errors as AIE
from app.ai.gateway import GatewayResult
from app.ai.types import ChatResponse, ToolCall, Usage

PRODUCT = {"summary": "A concise analysis."}
MARKET = {"summary": "Market notes.", "keyword_candidates": [
    {"keyword": "buy widgets", "intent": "transactional", "match_type_suggestion": "phrase", "rationale": "clear purchase intent"}]}
STRATEGY = {"objective": "Sell widgets", "campaigns": [{"name": "Brand", "goal": "Capture demand", "ad_groups": [{"name": "Core"}]}],
            "bidding_recommendation": "Start with manual CPC while learning."}
COPY = {"ad_groups": [{"ad_group": "Core", "headlines": ["Widgets that last", "Free delivery in 24h", "Official store"],
                       "descriptions": ["Durable widgets with a two year warranty.", "Order today and receive tomorrow."]}]}
POLICY = {"verdict": "approved", "summary": "No blocking issue found.", "findings": []}
PERFORMANCE = {"summary": "Supplied data analyzed.", "data_limitations": ["Only 7 days were supplied."]}
PLAN = {"summary": "Analyze, then write.", "steps": [
    {"id": "s1", "agent": "product_analyst", "objective": "Analyze the product and page"},
    {"id": "s2", "agent": "ad_copywriter", "objective": "Write the ads", "depends_on": ["s1"]}]}
REPORT = {"summary": "Consolidated.", "key_results": ["analysis done", "ads written"]}

OUTPUT_FOR = {"product_analyst": PRODUCT, "market_researcher": MARKET, "search_ads_strategist": STRATEGY,
              "ad_copywriter": COPY, "policy_reviewer": POLICY, "performance_analyst": PERFORMANCE}


@dataclass
class Say:
    text: str
    cost: Optional[int] = 1000


def say_json(value: Any, cost: Optional[int] = 1000) -> Say:
    return Say(json.dumps(value), cost)


@dataclass
class UseTools:
    calls: List[tuple]          # (tool name, arguments)
    text: str = ""
    cost: Optional[int] = 1000


@dataclass
class Delay:
    seconds: float
    then: Any


class ScriptedGateway:
    def __init__(self, *script: Any):
        self.script = list(script)
        self.requests: List[tuple] = []     # (operation, ChatRequest)

    @property
    def operations(self) -> List[str]:
        return [op for op, _ in self.requests]

    async def chat(self, operation, request):
        self.requests.append((operation, request))
        if not self.script:
            raise AssertionError(f"no scripted answer left for {operation}")
        item = self.script.pop(0)
        if callable(item):
            item = item(request)
        if isinstance(item, Delay):
            await asyncio.sleep(item.seconds)
            item = item.then
        if isinstance(item, Exception):
            raise item
        if isinstance(item, UseTools):
            tool_calls = [ToolCall(id=f"call-{uuid.uuid4().hex[:8]}", name=n, arguments=a) for n, a in item.calls]
            response = ChatResponse(item.text, tool_calls, "tool_calls", Usage(120, 40, 0, 0), "m-scripted", "scripted")
        else:
            response = ChatResponse(item.text, [], "stop", Usage(100, 50, 0, 0), "m-scripted", "scripted")
        return GatewayResult(response=response, model_row_id="model-row", provider="scripted", model_id="m-scripted", attempts=1,
                             request_id=str(uuid.uuid4()), cost_micros=item.cost)


def ai_error(kind: str) -> AIE.AIError:
    return AIE.AIError(kind, "scripted failure", provider="scripted")


def world_of(make_user, make_org, add_member, login_headers):
    """An organization with one user per role; `a` is the owner of a second, unrelated organization `b`."""
    owner = make_user("owner-ag@example.com")
    org = make_org("Agents Org", owner=owner)
    users = {"owner": owner}
    headers = {"owner": login_headers(owner, org)}
    for role in ("admin", "manager", "analyst", "viewer"):
        user = make_user(f"{role}-ag@example.com")
        add_member(org, user, role=role)
        users[role] = user
        headers[role] = login_headers(user, org)
    other_owner = make_user("other-ag@example.com")
    other = make_org("Other Agents Org", owner=other_owner)
    return SimpleNamespace(org=org, users=users, h=headers, other=other, other_owner=other_owner,
                           other_h=login_headers(other_owner, other))


# ---- driving the real executor against the real (disposable) database ---------------------------------------
def create_task_row(org, user, objective="Analyze this product for search ads", agent_key=None, context=None, **kwargs) -> str:
    """Insert a PENDING task through the same function the API uses (runtime role, RLS in force). Returns its id."""
    from app.agents import executor
    from app.database import SessionLocal, bind_context
    db = SessionLocal()
    bind_context(db, org.id)
    try:
        task, _created = executor.create_task(db, org_id=org.id, user_id=user.id, objective=objective, agent_key=agent_key, context=context,
                                              idempotency_key=kwargs.get("idempotency_key"), max_cost_usd=kwargs.get("max_cost_usd"))
        return task.id
    finally:
        db.close()


def run_task(monkeypatch, gateway, task_id, org, fetch=None) -> str:
    from app.agents import executor
    monkeypatch.setattr(executor, "GATEWAY_FACTORY", lambda db, org_id, user_id: gateway)
    if fetch is not None:
        monkeypatch.setattr(executor, "FETCH_PAGE", fetch)
    return asyncio.run(executor.execute_task(task_id, org.id))


def snapshot(db_session, task_id):
    from app.models import AgentRun, AgentStep, AgentTask
    db_session.expire_all()
    task = db_session.query(AgentTask).filter(AgentTask.id == task_id).one()
    runs = db_session.query(AgentRun).filter(AgentRun.task_id == task_id).order_by(AgentRun.sequence).all()
    steps = db_session.query(AgentStep).filter(AgentStep.task_id == task_id).order_by(AgentStep.sequence).all()
    return task, runs, steps


def set_limits(monkeypatch, key, **changes):
    """Tighten one agent's built-in limits for a test."""
    import dataclasses
    from app.agents import registry
    defn = registry.AGENTS[key]
    monkeypatch.setitem(registry.AGENTS, key, dataclasses.replace(defn, limits=dataclasses.replace(defn.limits, **changes)))


def drain(**kwargs):
    """Run the REAL worker loop in-process until the queue has nothing claimable (``--once``). The API never executes a task,
    so tests that want one finished enqueue it (API or ``create_task_row``) and then drain. Returns {status: count}."""
    from app.worker.worker import AgentWorker
    options = {"once": True, "health_port": 0, "handle_signals": False, "poll_interval": 0.05, "recovery_interval": 3600}
    options.update(kwargs)
    return asyncio.run(AgentWorker(**options).run())
