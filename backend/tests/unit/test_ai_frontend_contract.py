"""The AI Control Center reads a fixed set of fields from /api/v1/ai. The list lives in
frontend/src/__tests__/aiContract.json and is checked here against the REAL API (and, in the frontend,
against the fake the UI tests run on), so the UI and the backend cannot drift apart silently."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models import AiUsageLog
from tests.unit.ai_support import jr

CONTRACT = json.loads((Path(__file__).resolve().parents[3] / "frontend" / "src" / "__tests__" / "aiContract.json").read_text(encoding="utf-8"))
P = "/api/v1/ai"
KEY = "sk-contract-KEYKEYKEY-0001"


def has(item: dict, name: str):
    missing = [f for f in CONTRACT[name] if f not in item]
    assert not missing, f"{name}: the API no longer returns {missing} (the UI reads them)"


def no_secret(payload):
    dump = json.dumps(payload)
    assert KEY not in dump and KEY[-8:] not in dump
    keys = set()

    def walk(value):
        if isinstance(value, dict):
            keys.update(value.keys())
            [walk(v) for v in value.values()]
        elif isinstance(value, list):
            [walk(v) for v in value]
    walk(payload)
    assert not (keys & set(CONTRACT["forbidden"])), keys & set(CONTRACT["forbidden"])


@pytest.fixture
def w(make_user, make_org, login_headers, db_session):
    user = make_user("contract@example.com")
    org = make_org("Contract Org", owner=user)
    return SimpleNamespace(org=org, h=login_headers(user, org), db=db_session)


def test_every_endpoint_returns_the_fields_the_screen_reads(client, w, provider_mock):
    provider_mock.always("openai", lambda r: jr(200, {"data": [{"id": "found-model"}]}))
    created = client.post(f"{P}/credentials", headers=w.h, json={"provider": "openai", "api_key": KEY, "verify": True})
    assert created.status_code == 201
    has(created.json(), "credential")
    credential_id = created.json()["id"]

    providers = client.get(f"{P}/providers", headers=w.h).json()
    assert {p["provider"] for p in providers} == {"anthropic", "gemini", "openai"}
    [has(p, "providers") for p in providers]

    listed = client.get(f"{P}/credentials", headers=w.h).json()
    [has(c, "credential") for c in listed]
    no_secret(listed)

    tested = client.post(f"{P}/credentials/{credential_id}/test", headers=w.h).json()
    has(tested, "credentialTest")
    has(tested["credential"], "credential")
    no_secret(tested)

    discovered = client.get(f"{P}/models/discover?provider=openai", headers=w.h).json()
    assert discovered and all(has(d, "discovered") is None for d in discovered)

    model = client.post(f"{P}/models", headers=w.h, json={
        "provider": "openai", "model_id": "found-model", "input_price_usd_per_mtok": "3", "output_price_usd_per_mtok": "15",
        "cached_input_price_usd_per_mtok": "0.3"}).json()
    has(model, "model")
    assert model["cached_input_price_usd_per_mtok"] == pytest.approx(0.3)
    [has(m, "model") for m in client.get(f"{P}/models", headers=w.h).json()]

    route = client.put(f"{P}/routing/default", headers=w.h, json={"primary_model_id": model["id"], "fallback_model_ids": []}).json()
    has(route, "route")
    [has(r, "route") for r in client.get(f"{P}/routing", headers=w.h).json()]

    budget = client.put(f"{P}/budgets/daily", headers=w.h, json={"limit_usd": "10"}).json()
    has(budget, "budget")
    [has(b, "budget") for b in client.get(f"{P}/budgets", headers=w.h).json()]

    w.db.add(AiUsageLog(organization_id=w.org.id, request_id="r1", attempt=1, operation="copy.generate", provider="openai", model_id="found-model",
                        status="success", input_tokens=10, output_tokens=5, cached_input_tokens=2, cost_micros=123, user_id=None))
    w.db.commit()
    summary = client.get(f"{P}/usage/summary", headers=w.h).json()
    has(summary["totals"], "usageTotals")
    for section, name in (("by_day", "usageByDay"), ("by_model", "usageByModel"), ("by_operation", "usageByOperation"), ("by_user", "usageByUser")):
        assert summary[section], section
        [has(row, name) for row in summary[section]]
    logs = client.get(f"{P}/usage/logs", headers=w.h).json()
    assert logs
    [has(row, "usageLog") for row in logs]
    no_secret([summary, logs, listed, providers])


def test_error_answers_carry_the_kind_the_screen_translates(client, w, provider_mock):
    """A provider failure reaches the UI as an HTTP status plus the X-AI-Error header (exposed to browsers by CORS)."""
    provider_mock.queue("openai", jr(401, {"error": {"message": f"bad {KEY}", "code": "invalid_api_key"}}))
    r = client.post(f"{P}/credentials", headers=w.h, json={"provider": "openai", "api_key": KEY, "verify": True})
    assert r.status_code == 422 and r.headers.get("X-AI-Error") == "authentication" and KEY not in r.text
    from app.main import app
    cors = next(m for m in app.user_middleware if m.cls.__name__ == "CORSMiddleware")
    assert "X-AI-Error" in cors.kwargs["expose_headers"]
