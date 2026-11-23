"""The AI Agents dashboard reads a fixed set of fields from /api/v1/agents. The list lives in
frontend/src/__tests__/agentsContract.json and is checked here against the REAL API (and, in the frontend, against the fake the
UI tests run on), so the UI and the backend cannot drift apart silently. Scripted gateway: no provider, no Google, no network."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agents import executor
from tests.unit.agents_support import PLAN, PRODUCT, REPORT, COPY, ScriptedGateway, UseTools, create_task_row, drain, say_json, world_of

CONTRACT = json.loads((Path(__file__).resolve().parents[3] / "frontend" / "src" / "__tests__" / "agentsContract.json").read_text(encoding="utf-8"))
P = "/api/v1/agents"


def has(item: dict, name: str):
    missing = [f for f in CONTRACT[name] if f not in item]
    assert not missing, f"{name}: the API no longer returns {missing} (the dashboard reads them)"


def keys_of(payload):
    found = set()

    def walk(value):
        if isinstance(value, dict):
            found.update(value.keys())
            [walk(v) for v in value.values()]
        elif isinstance(value, list):
            [walk(v) for v in value]
    walk(payload)
    return found


@pytest.fixture
def w(make_user, make_org, add_member, login_headers):
    return world_of(make_user, make_org, add_member, login_headers)


def test_every_endpoint_returns_the_fields_the_dashboard_reads(client, w, monkeypatch, db_session):
    h = w.h["manager"]
    answers = []

    agents = client.get(P, headers=h).json()
    answers.append(agents)
    assert len(agents) == 6
    for agent in agents:
        has(agent, "agent")
        has(agent["limits"]["effective"], "agentLimits")
        has(agent["limits"]["defaults"], "agentLimits")
        has(agent["routing"], "agentRouting")
    supervisor = client.get(f"{P}/supervisor", headers=h).json()
    answers.append(supervisor)
    has(supervisor, "agent")
    has(supervisor["routing"], "agentRouting")
    assert supervisor["allowed_tools"] == [] and supervisor["role"] == "supervisor" and supervisor["limitations"]

    tools = client.get(f"{P}/tools", headers=h).json()
    answers.append(tools)
    [has(t, "tool") for t in tools["available"]]
    [has(t, "plannedTool") for t in tools["planned"]]

    # a real task executed by the real worker loop with a scripted model
    gw = ScriptedGateway(say_json(PLAN), UseTools([("plan.note", {"note": "x"})]), say_json(PRODUCT), say_json(COPY), say_json(REPORT))
    monkeypatch.setattr(executor, "GATEWAY_FACTORY", lambda db, org_id, user_id: gw)
    created = client.post(f"{P}/tasks", headers=h, json={"objective": "Plan search ads for the widget product"})
    assert created.status_code == 201
    has(created.json(), "task")
    has(created.json()["usage"], "taskUsage")
    task_id = created.json()["id"]
    assert drain() == {"completed": 1}
    listed = client.get(f"{P}/tasks", headers=h).json()
    answers.append(listed)
    for item in listed:
        has(item, "task")
        has(item["usage"], "taskUsage")
    detail = client.get(f"{P}/tasks/{task_id}", headers=h).json()
    answers.append(detail)
    has(detail, "task")
    has(detail, "taskDetail")
    has(detail["progress"], "progress")
    [has(run, "run") for run in detail["runs"]]
    steps = client.get(f"{P}/tasks/{task_id}/steps", headers=h).json()
    answers.append(steps)
    assert steps
    [has(step, "step") for step in steps]
    result = client.get(f"{P}/tasks/{task_id}/result", headers=h).json()
    answers.append(result)
    has(result, "result")

    status = client.get(f"{P}/worker/status", headers=h).json()
    answers.append(status)
    has(status, "workerStatus")
    has(status["queue"], "workerQueue")

    forbidden = keys_of(answers) & set(CONTRACT["forbidden"])
    assert not forbidden, f"never exposed to the browser: {forbidden}"
    assert "SECURITY RULES" not in json.dumps(answers)


CATALOG = json.loads((Path(__file__).resolve().parents[3] / "frontend" / "src" / "__tests__" / "agentsCatalog.json").read_text(encoding="utf-8"))


def test_the_catalog_snapshot_used_by_the_ui_tests_matches_the_real_registry_and_api(client, w):
    """The fake backend (and the pt-BR translations, which are only used while the backend text equals the en-US text) rely on
    agentsCatalog.json. If an agent, its texts or its tools change, this fails until the snapshot is regenerated."""
    h = w.h["manager"]
    real = {a["key"]: a for a in client.get(P, headers=h).json()}
    assert [a["key"] for a in CATALOG["agents"]] == list(real)
    for snap in CATALOG["agents"] + [CATALOG["supervisor"]]:
        api = client.get(f"{P}/supervisor", headers=h).json() if snap["key"] == "supervisor" else real[snap["key"]]
        for field in ("name", "description", "version", "instructions_hash", "allowed_tools", "readiness", "limitations"):
            assert snap[field] == api[field], f"{snap['key']}.{field} changed: regenerate frontend/src/__tests__/agentsCatalog.json and the agents i18n catalogue"
        assert snap["limits_defaults"] == api["limits"]["defaults"]
    tools = client.get(f"{P}/tools", headers=h).json()
    assert CATALOG["tools"] == [{k: t[k] for k in ("name", "description", "side_effect", "required_permission", "external_system", "requires", "status")} for t in tools["available"]]
    assert CATALOG["planned"] == [{k: t[k] for k in ("name", "description", "status", "side_effect", "requires_human_approval", "agent_may_hold")} for t in tools["planned"]]
