"""Phase B end-to-end: task -> worker (executor) -> AI Gateway -> REAL provider -> stored result, per provider and agent.

Each test spends a few cents at most (small objective, per-task cost cap ``HOMOLOG_TASK_MAX_USD`` default 0.10) and records
nothing but statuses/counts. Google-dependent agents run only when a real connection is supplied; otherwise they are
skipped = "not tested", never mocked.
"""
import asyncio
import os

import pytest

from app.agents import executor
from app.models import AgentTask, AiUsageLog, GoogleAdsConnection
from tests.unit.agents_support import create_task_row, snapshot

TASK_MAX_USD = float(os.getenv("HOMOLOG_TASK_MAX_USD", "0.10"))
PRODUCT_TEXT = "Product: a reusable stainless steel water bottle, 750 ml, keeps drinks cold for 24 hours. Price BRL 89. Free shipping."


def execute(task_id, org):
    return asyncio.run(executor.execute_task(task_id, org.id))


def usage(db_session, org):
    db_session.expire_all()
    return db_session.query(AiUsageLog).filter(AiUsageLog.organization_id == org.id).order_by(AiUsageLog.created_at).all()


def run_agent(db_session, w, agent_key, objective, context=None):
    task_id = create_task_row(w.org, w.users["manager"], objective=objective, agent_key=agent_key, context=context, max_cost_usd=TASK_MAX_USD)
    status = execute(task_id, w.org)
    task, runs, steps = snapshot(db_session, task_id)
    return status, task, steps


@pytest.mark.parametrize("agent_key", ["ad_copywriter", "policy_reviewer"])
def test_agents_without_external_data_complete_on_each_provider(real_org, real_provider, db_session, agent_key):
    from tests_real.conftest import route_to
    provider, _cfg = real_provider
    route_to(db_session, real_org.org, real_org.models[provider])
    objective = ("Write search ad copy for this product. " if agent_key == "ad_copywriter" else
                 "Review this ad text for policy problems: 'Best bottle, keeps drinks cold 24h. Free shipping.' ") + PRODUCT_TEXT
    status, task, _steps = run_agent(db_session, real_org, agent_key, objective)
    rows = usage(db_session, real_org.org)
    assert status == "completed", f"{provider}/{agent_key}: {status} {task.error_kind}"
    assert rows and all(r.provider == provider for r in rows)
    assert task.total_cost_micros > 0 and task.total_input_tokens > 0 and task.total_output_tokens > 0
    assert task.result  # structured output validated against the agent contract by the runner


def test_fallback_moves_to_a_real_second_provider_when_the_primary_is_unreachable(real_org, db_session, monkeypatch):
    """Primary = a provider whose endpoint is unreachable (connection refused => recoverable `unavailable`), fallback = a real one."""
    from app.core.config import settings
    from tests_real.conftest import route_to
    supplied = list(real_org.models)
    if len(supplied) < 2:
        pytest.skip("needs two providers supplied")
    broken, good = supplied[0], supplied[1]
    monkeypatch.setattr(settings, f"AI_{broken.upper()}_BASE_URL", "http://127.0.0.1:9")
    route_to(db_session, real_org.org, real_org.models[broken], real_org.models[good])
    status, task, _ = run_agent(db_session, real_org, "ad_copywriter", "Write search ad copy. " + PRODUCT_TEXT)
    rows = usage(db_session, real_org.org)
    assert status == "completed"
    assert [(r.provider, r.status) for r in rows][:2] == [(broken, "error"), (good, "success")]
    assert rows[0].error_kind == "unavailable"


def test_product_analyst_reads_a_real_public_page(real_org, real_provider, db_session):
    from tests_real.conftest import route_to
    url = os.getenv("HOMOLOG_PRODUCT_URL")
    if not url:
        pytest.skip("HOMOLOG_PRODUCT_URL not supplied (not tested)")
    provider, _ = real_provider
    route_to(db_session, real_org.org, real_org.models[provider])
    status, task, steps = run_agent(db_session, real_org, "product_analyst", f"Analyze the product page {url}")
    assert status == "completed"
    assert any(s.kind == "tool" and (s.name or "").startswith("web.") for s in steps), "the page analysis tool was not used"


GOOGLE_AGENTS = {"market_researcher": "Suggest keyword candidates for running shoes using the Keyword Planner.",
                 "search_ads_strategist": "Propose a Search campaign structure for running shoes.",
                 "performance_analyst": "Analyze the last 7 days of account performance."}


@pytest.mark.parametrize("agent_key", sorted(GOOGLE_AGENTS))
def test_google_dependent_agents_use_real_account_data_and_label_provenance(real_org, real_provider, db_session, add_connection, agent_key):
    from tests.unit.gads_support import connect
    from tests_real.conftest import route_to
    refresh, customer = os.getenv("HOMOLOG_GADS_REFRESH_TOKEN"), os.getenv("HOMOLOG_GADS_CUSTOMER_ID")
    if not (refresh and customer and os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN")):
        pytest.skip("Google Ads credentials not supplied (not tested)")
    provider, _ = real_provider
    route_to(db_session, real_org.org, real_org.models[provider])
    connect(add_connection, real_org.org, real_org.users["owner"], "".join(c for c in customer if c.isdigit()), refresh,
            login_customer_id=os.getenv("HOMOLOG_GADS_LOGIN_CUSTOMER_ID") or None)
    status, task, steps = run_agent(db_session, real_org, agent_key, GOOGLE_AGENTS[agent_key])
    assert status == "completed", f"{agent_key}: {status}"
    google_steps = [s for s in steps if s.kind == "tool" and (s.name or "").startswith("google_ads.")]
    assert google_steps, "no Google Ads tool was called"
    assert not any("mutate" in (s.name or "") for s in steps)   # read-only by construction; asserted anyway


def test_budget_blocks_real_calls_before_they_are_sent(real_org, real_provider, db_session):
    from app.models import AiBudget
    from tests_real.conftest import route_to
    provider, _ = real_provider
    route_to(db_session, real_org.org, real_org.models[provider])
    db_session.add(AiBudget(organization_id=real_org.org.id, period="daily", limit_micros=1, enabled=True))
    db_session.commit()
    status, task, _ = run_agent(db_session, real_org, "ad_copywriter", "Write search ad copy. " + PRODUCT_TEXT)
    assert status != "completed"
    assert usage(db_session, real_org.org) == [] or all(r.status != "success" for r in usage(db_session, real_org.org))
