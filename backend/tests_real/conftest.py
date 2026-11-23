"""REAL-integration tests (etapa 02.7, Phase B). Kept OUTSIDE ``tests/`` on purpose: ``pytest`` / CI never collect them.

Run only with explicit authorization, on a DISPOSABLE database, with test credentials:

    HOMOLOG_AUTHORIZATION=I_AUTHORIZE_REAL_CALLS_PHASE_B  TEST_DATABASE_URL=postgresql://...@.../..._test \
    python -m pytest tests_real -p tests.conftest -m real

``-p tests.conftest`` reuses the disposable-database fixtures of the normal suite. The two autouse guards of that suite that
would defeat a real test (deny-all AI transport, hermetic DNS) are overridden HERE, and only here. Everything is skipped
unless the authorization phrase is present; a provider/Google test is skipped unless its own credentials are supplied.
Test reports never contain keys, tokens, prompts or completions (see app.homologation.guard.Redactor).
"""
import os

import pytest

from app.homologation import guard


def pytest_collection_modifyitems(config, items):
    for item in items:
        item.add_marker(pytest.mark.real)
        if not guard.authorized():
            item.add_marker(pytest.mark.skip(reason="real calls not authorized (HOMOLOG_AUTHORIZATION absent)"))


def pytest_configure(config):
    config.addinivalue_line("markers", "real: calls REAL paid/external services (needs HOMOLOG_AUTHORIZATION)")


@pytest.fixture(autouse=True)
def _no_paid_ai_calls():
    """Override of the suite-wide deny-all transport: real tests are the one place that reaches the providers."""
    from app.ai.adapters.base import set_transport
    set_transport(None)
    yield
    set_transport(None)


@pytest.fixture(autouse=True)
def _hermetic_dns():
    """Override: the real product-page analysis needs the real resolver."""
    yield


@pytest.fixture(autouse=True)
def _global_cap_and_read_only(db_session):
    """Phase B has ONE aggregate ceiling shared with the provider harness (app.homologation.guard.CostLedger file):
    a test starts only if budget remains, the organization's AI budget is capped to what remains (the gateway then refuses
    to send anything that could exceed it) and whatever the usage log recorded is added to the ledger afterwards.
    The Google Ads SDK clients are wrapped so only reads can reach Google."""
    from datetime import datetime, timezone
    from app.homologation import readonly
    from app.models import AiUsageLog
    ledger = guard.CostLedger.shared()
    if ledger.remaining_micros < guard.usd_to_micros(0.01):
        pytest.skip("aggregate Phase B ceiling reached: nothing more is sent")
    uninstall = readonly.install()
    started = datetime.now(timezone.utc)
    yield ledger
    uninstall()
    db_session.expire_all()
    spent = sum((r.cost_micros if r.cost_micros is not None else (r.estimated_cost_micros or 0))
                for r in db_session.query(AiUsageLog).filter(AiUsageLog.created_at >= started).all())
    ledger.add_external(spent)


def provider_config(provider: str):
    """(key, model, in_price, out_price) from HOMOLOG_* variables, or None when not fully supplied."""
    p = provider.upper()
    key, model = os.getenv(f"HOMOLOG_{p}_API_KEY"), os.getenv(f"HOMOLOG_{p}_MODEL")
    in_p, out_p = os.getenv(f"HOMOLOG_{p}_INPUT_PRICE_MICROS_PER_MTOK"), os.getenv(f"HOMOLOG_{p}_OUTPUT_PRICE_MICROS_PER_MTOK")
    if not (key and model and in_p and in_p.isdigit() and out_p and out_p.isdigit()):
        return None
    return key, model, int(in_p), int(out_p)


@pytest.fixture(params=["anthropic", "gemini", "openai"])
def real_provider(request):
    cfg = provider_config(request.param)
    if cfg is None:
        pytest.skip(f"{request.param}: key / model / prices not supplied (not tested)")
    return request.param, cfg


@pytest.fixture
def w(make_user, make_org, add_member, login_headers):
    from tests.unit.agents_support import world_of
    return world_of(make_user, make_org, add_member, login_headers)


@pytest.fixture
def real_org(w, db_session):
    """The organization with a REAL credential + priced model + default routing for every provider that was supplied."""
    from tests.unit.ai_support import seed_credential, seed_model
    from app.models import AiRouting
    models = {}
    for provider in ("anthropic", "gemini", "openai"):
        cfg = provider_config(provider)
        if cfg:
            key, model, in_p, out_p = cfg
            seed_credential(db_session, w.org.id, provider, key)
            models[provider] = seed_model(db_session, w.org.id, provider, model, input_price_micros_per_mtok=in_p,
                                          output_price_micros_per_mtok=out_p, supports_tools=True, supports_json=True)
    w.models = models
    # gateway-level ceiling: the organization can never reserve more than what is left of the aggregate cap
    from app.models import AiBudget
    remaining = guard.CostLedger.shared().remaining_micros
    for period in ("daily", "monthly"):
        db_session.add(AiBudget(organization_id=w.org.id, period=period, limit_micros=remaining, enabled=True))
    db_session.commit()
    return w


def route_to(db_session, org, *model_rows):
    """Default routing: first model primary, the rest fallbacks."""
    from app.models import AiRouting
    db_session.query(AiRouting).filter(AiRouting.organization_id == org.id).delete()
    db_session.add(AiRouting(organization_id=org.id, operation="default", primary_model_id=model_rows[0].id,
                             fallback_model_ids=[m.id for m in model_rows[1:]]))
    db_session.commit()
