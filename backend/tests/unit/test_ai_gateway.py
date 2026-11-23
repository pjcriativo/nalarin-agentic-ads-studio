"""AI Gateway core: routing, capability selection, fallback policy, budgets (atomic reservation and
concurrency), usage/cost accounting, isolation. Real application session (runtime role + RLS) and
scripted provider transports: nothing reaches a paid endpoint."""
import asyncio
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text

from app.ai import errors as E
from app.ai import pricing
from app.ai import types as T
from app.ai.gateway import AIGateway
from app.models import AiBudget, AiCredential, AiRouting, AiUsageLog
from tests.unit.ai_support import (KEYS, anthropic_ok, gateway_for, gemini_ok, jr, openai_ok, seed_credential, seed_model)

PRICED = dict(input_price_micros_per_mtok=3_000_000, output_price_micros_per_mtok=15_000_000)


@pytest.fixture
def orgs(make_user, make_org):
    alice, bob = make_user("alice-ai@example.com"), make_user("bob-ai@example.com")
    return type("W", (), {"a": make_org("AI A", owner=alice), "b": make_org("AI B", owner=bob), "alice": alice, "bob": bob})


def req(text="Hello", **kw):
    return T.ChatRequest(messages=[T.Message.user(text)], max_tokens=kw.pop("max_tokens", 100), **kw)


def call(org_id, operation="op.test", request=None, user_id=None, **gw):
    gateway, db = gateway_for(org_id, user_id, **gw)
    try:
        return asyncio.run(gateway.chat(operation, request or req()))
    finally:
        db.close()


def logs(db_session, org_id):
    db_session.expire_all()
    return db_session.query(AiUsageLog).filter(AiUsageLog.organization_id == org_id).order_by(AiUsageLog.created_at, AiUsageLog.attempt).all()


def seed_three(db_session, org):
    """anthropic / openai / gemini, each with credential + one priced, fully capable model."""
    caps = dict(supports_tools=True, supports_vision=True, supports_json=True, **PRICED)
    rows = {}
    for provider, model in (("anthropic", "m-anth"), ("openai", "m-oai"), ("gemini", "m-gem")):
        seed_credential(db_session, org.id, provider)
        rows[provider] = seed_model(db_session, org.id, provider, model, **caps)
    return rows


def route(db_session, org, operation, primary, fallbacks=()):
    db_session.add(AiRouting(organization_id=org.id, operation=operation, primary_model_id=primary.id,
                             fallback_model_ids=[m.id for m in fallbacks]))
    db_session.commit()


class TestRouting:
    def test_without_a_route_the_enabled_models_are_used_in_creation_order(self, orgs, db_session, provider_mock):
        m = seed_three(db_session, orgs.a)
        provider_mock.queue("anthropic", jr(200, anthropic_ok("from anthropic")))
        result = call(orgs.a.id)
        assert (result.provider, result.model_id, result.attempts) == ("anthropic", "m-anth", 1)
        assert result.response.text == "from anthropic"
        assert provider_mock.of("openai") == []

    def test_an_operation_route_beats_the_default_route(self, orgs, db_session, provider_mock):
        m = seed_three(db_session, orgs.a)
        route(db_session, orgs.a, "default", m["openai"])
        route(db_session, orgs.a, "special.op", m["gemini"], [m["anthropic"]])
        provider_mock.queue("openai", jr(200, openai_ok("default")))
        provider_mock.queue("gemini", jr(200, gemini_ok("special")))
        assert call(orgs.a.id, "other.op").response.text == "default"
        assert call(orgs.a.id, "special.op").response.text == "special"

    def test_disabled_models_and_providers_without_an_active_credential_are_skipped(self, orgs, db_session, provider_mock):
        m = seed_three(db_session, orgs.a)
        route(db_session, orgs.a, "default", m["anthropic"], [m["openai"], m["gemini"]])
        m["anthropic"].enabled = False
        db_session.query(AiCredential).filter(AiCredential.provider == "openai").update({"status": "revoked", "encrypted_key": ""})
        db_session.commit()
        provider_mock.queue("gemini", jr(200, gemini_ok("only gemini is usable")))
        assert call(orgs.a.id).provider == "gemini"
        assert provider_mock.of("anthropic") == [] and provider_mock.of("openai") == []

    def test_capabilities_decide_which_models_may_serve_a_request(self, orgs, db_session, provider_mock):
        seed_credential(db_session, orgs.a.id, "anthropic")
        seed_credential(db_session, orgs.a.id, "openai")
        plain = seed_model(db_session, orgs.a.id, "anthropic", "plain", **PRICED)
        tools = seed_model(db_session, orgs.a.id, "openai", "with-tools", supports_tools=True, **PRICED)
        route(db_session, orgs.a, "default", plain, [tools])
        provider_mock.queue("openai", jr(200, openai_ok("tooled")))
        spec = T.ToolSpec("t", "d", {"type": "object", "properties": {}})
        assert call(orgs.a.id, request=req(tools=[spec])).model_id == "with-tools"
        assert provider_mock.of("anthropic") == []
        # nobody supports vision -> explicit configuration error, no provider call
        image = T.ChatRequest(messages=[T.Message.user("x", T.ImagePart("image/png", b"png"))])
        with pytest.raises(E.AIError) as caught:
            call(orgs.a.id, request=image)
        assert caught.value.kind == E.NOT_CONFIGURED and "vision" in caught.value.message

    def test_nothing_configured_is_a_clear_error(self, orgs):
        with pytest.raises(E.AIError) as caught:
            call(orgs.a.id)
        assert caught.value.kind == E.NOT_CONFIGURED

    def test_models_and_credentials_of_another_organization_are_invisible(self, orgs, db_session, provider_mock):
        seed_three(db_session, orgs.a)
        with pytest.raises(E.AIError) as caught:
            call(orgs.b.id)  # B has nothing; A's credentials and models must not leak in
        assert caught.value.kind == E.NOT_CONFIGURED
        assert provider_mock.requests == []

    def test_each_organization_calls_the_provider_with_its_own_key(self, orgs, db_session, provider_mock):
        seed_credential(db_session, orgs.a.id, "openai", key="sk-org-A-key-AAAAAAAA")
        seed_credential(db_session, orgs.b.id, "openai", key="sk-org-B-key-BBBBBBBB")
        seed_model(db_session, orgs.a.id, "openai", "m", **PRICED)
        seed_model(db_session, orgs.b.id, "openai", "m", **PRICED)
        provider_mock.always("openai", lambda r: jr(200, openai_ok()))
        call(orgs.a.id)
        call(orgs.b.id)
        assert [r.headers["authorization"] for r in provider_mock.of("openai")] == ["Bearer sk-org-A-key-AAAAAAAA", "Bearer sk-org-B-key-BBBBBBBB"]


class TestFallbackPolicy:
    @pytest.fixture
    def three(self, orgs, db_session):
        m = seed_three(db_session, orgs.a)
        route(db_session, orgs.a, "default", m["anthropic"], [m["openai"], m["gemini"]])
        return m

    @pytest.mark.parametrize("status,body", [
        (429, {"type": "error", "error": {"type": "rate_limit_error", "message": "slow"}}),
        (529, {"type": "error", "error": {"type": "overloaded_error", "message": "busy"}}),
        (500, {"type": "error", "error": {"type": "api_error", "message": "oops"}}),
        (504, {"type": "error", "error": {"type": "timeout_error", "message": "slow"}}),
    ])
    def test_recoverable_errors_switch_to_the_next_model(self, status, body, orgs, db_session, provider_mock, three):
        provider_mock.queue("anthropic", jr(status, body))
        provider_mock.queue("openai", jr(200, openai_ok("saved by the fallback")))
        result = call(orgs.a.id)
        assert (result.provider, result.attempts, result.response.text) == ("openai", 2, "saved by the fallback")
        rows = logs(db_session, orgs.a.id)
        assert [(r.provider, r.status, r.attempt) for r in rows] == [("anthropic", "error", 1), ("openai", "success", 2)]
        assert len({r.request_id for r in rows}) == 1 and rows[0].error_kind in (E.RATE_LIMIT, E.UNAVAILABLE, E.TIMEOUT)

    def test_a_network_timeout_switches_too(self, orgs, db_session, provider_mock, three):
        import httpx
        provider_mock.queue("anthropic", httpx.ReadTimeout("slow"))
        provider_mock.queue("openai", jr(200, openai_ok("ok")))
        assert call(orgs.a.id).provider == "openai"
        assert logs(db_session, orgs.a.id)[0].error_kind == E.TIMEOUT

    @pytest.mark.parametrize("status,body,kind", [
        (401, {"type": "error", "error": {"type": "authentication_error", "message": "bad key"}}, E.AUTHENTICATION),
        (403, {"type": "error", "error": {"type": "permission_error", "message": "no"}}, E.PERMISSION),
        (402, {"type": "error", "error": {"type": "billing_error", "message": "credit"}}, E.QUOTA),
        (400, {"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}}, E.INVALID_REQUEST),
        (404, {"type": "error", "error": {"type": "not_found_error", "message": "model"}}, E.NOT_FOUND),
    ])
    def test_every_other_error_stops_immediately_without_calling_another_provider(self, status, body, kind, orgs, db_session, provider_mock, three):
        provider_mock.queue("anthropic", jr(status, body))
        with pytest.raises(E.AIError) as caught:
            call(orgs.a.id)
        assert caught.value.kind == kind
        assert provider_mock.of("openai") == [] and provider_mock.of("gemini") == []
        assert [(r.provider, r.status) for r in logs(db_session, orgs.a.id)] == [("anthropic", "error")]

    def test_an_unusable_answer_is_not_retried_elsewhere(self, orgs, db_session, provider_mock, three):
        provider_mock.queue("anthropic", jr(200, {"nonsense": 1}))
        with pytest.raises(E.AIError) as caught:
            call(orgs.a.id)
        assert caught.value.kind == E.INVALID_RESPONSE and provider_mock.of("openai") == []

    def test_all_candidates_failing_raises_the_last_error_and_logs_every_attempt(self, orgs, db_session, provider_mock, three):
        provider_mock.queue("anthropic", jr(529, {"type": "error", "error": {"type": "overloaded_error", "message": "busy"}}))
        provider_mock.queue("openai", jr(503, {"error": {"message": "overloaded", "type": "service_unavailable_error", "code": "server_is_overloaded"}}))
        provider_mock.queue("gemini", jr(429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}}))
        with pytest.raises(E.AIError) as caught:
            call(orgs.a.id)
        assert caught.value.kind == E.RATE_LIMIT and caught.value.provider == "gemini"
        assert [r.status for r in logs(db_session, orgs.a.id)] == ["error"] * 3

    def test_an_adapter_bug_never_escapes_with_details(self, orgs, db_session, provider_mock, three, monkeypatch):
        from app.ai.adapters import PROVIDER_ADAPTERS

        async def boom(*a, **k):
            raise RuntimeError(f"internal failure with {KEYS['anthropic']}")

        monkeypatch.setattr(PROVIDER_ADAPTERS["anthropic"], "chat", boom)
        with pytest.raises(E.AIError) as caught:
            call(orgs.a.id)
        assert caught.value.kind == E.UNKNOWN and KEYS["anthropic"] not in str(caught.value)
        assert caught.value.__cause__ is None
        assert logs(db_session, orgs.a.id)[0].error_kind == E.UNKNOWN


class TestAccounting:
    def test_tokens_estimate_cost_user_model_and_operation_are_recorded(self, orgs, db_session, provider_mock):
        seed_credential(db_session, orgs.a.id, "openai")
        seed_model(db_session, orgs.a.id, "openai", "priced", **PRICED)
        provider_mock.queue("openai", jr(200, openai_ok(inp=1000, out=500)))
        call(orgs.a.id, "my.operation", req("secret prompt text"), user_id=orgs.alice.id)
        row = logs(db_session, orgs.a.id)[0]
        assert (row.organization_id, row.user_id, row.operation, row.provider, row.model_id, row.status) == \
               (orgs.a.id, orgs.alice.id, "my.operation", "openai", "priced", "success")
        assert (row.input_tokens, row.output_tokens, row.cached_input_tokens, row.reasoning_tokens) == (1000, 500, 0, 0)
        # 1000 in * $3/M = 3000 micros + 500 out * $15/M = 7500 micros
        assert row.cost_micros == 10_500
        assert row.estimated_cost_micros and row.estimated_cost_micros >= pricing.tokens_cost_micros(100, 15_000_000)
        assert row.latency_ms is not None and row.credential_id

    def test_cost_rounds_up_and_unpriced_models_log_no_cost(self, orgs, db_session, provider_mock):
        assert pricing.tokens_cost_micros(1, 3_000_000) == 3 and pricing.tokens_cost_micros(1, 1) == 1 and pricing.tokens_cost_micros(0, 5) == 0
        seed_credential(db_session, orgs.a.id, "openai")
        seed_model(db_session, orgs.a.id, "openai", "free-form")
        provider_mock.queue("openai", jr(200, openai_ok(inp=10, out=5)))
        call(orgs.a.id)
        row = logs(db_session, orgs.a.id)[0]
        assert row.cost_micros is None and row.estimated_cost_micros is None and row.input_tokens == 10

    def test_logs_never_contain_prompts_completions_or_keys(self, orgs, db_session, provider_mock):
        seed_credential(db_session, orgs.a.id, "openai")
        seed_model(db_session, orgs.a.id, "openai", "m", **PRICED)
        provider_mock.queue("openai", jr(200, openai_ok(text="the completion text")))
        call(orgs.a.id, request=req("my private prompt"))
        provider_mock.queue("openai", jr(401, {"error": {"message": f"bad key {KEYS['openai']}", "code": "invalid_api_key"}}))
        with pytest.raises(E.AIError):
            call(orgs.a.id, request=req("another private prompt"))
        dump = json.dumps([{c.name: str(getattr(r, c.name)) for c in AiUsageLog.__table__.columns} for r in logs(db_session, orgs.a.id)])
        for secret in ("my private prompt", "another private prompt", "the completion text", KEYS["openai"], KEYS["openai"][-8:]):
            assert secret not in dump

    def test_failures_are_logged_with_zero_tokens_and_the_error_kind(self, orgs, db_session, provider_mock):
        seed_credential(db_session, orgs.a.id, "openai")
        seed_model(db_session, orgs.a.id, "openai", "m", **PRICED)
        provider_mock.queue("openai", jr(401, {"error": {"message": "x", "code": "invalid_api_key"}}))
        with pytest.raises(E.AIError):
            call(orgs.a.id)
        row = logs(db_session, orgs.a.id)[0]
        assert (row.status, row.error_kind, row.input_tokens, row.output_tokens, row.cost_micros) == ("error", E.AUTHENTICATION, 0, 0, None)

    def test_usage_is_append_only_for_the_application_role(self, orgs, db_session, provider_mock):
        from app.database import SessionLocal, bind_context
        seed_credential(db_session, orgs.a.id, "openai")
        seed_model(db_session, orgs.a.id, "openai", "m")
        provider_mock.queue("openai", jr(200, openai_ok()))
        call(orgs.a.id)
        db = SessionLocal()
        try:
            bind_context(db, orgs.a.id)
            for statement in ("DELETE FROM ai_usage_logs", "UPDATE ai_usage_logs SET input_tokens = 0", "TRUNCATE ai_usage_logs"):
                with pytest.raises(Exception, match="permission denied"):
                    db.execute(text(statement))
                db.rollback()
        finally:
            db.close()
        assert len(logs(db_session, orgs.a.id)) == 1


class TestRowLevelSecurity:
    def test_every_ai_table_is_invisible_to_another_organization_and_without_context(self, orgs, db_session, provider_mock):
        from app.database import SessionLocal, bind_context
        m = seed_three(db_session, orgs.a)
        route(db_session, orgs.a, "default", m["openai"])
        db_session.add(AiBudget(organization_id=orgs.a.id, period="daily", limit_micros=50_000_000))
        db_session.commit()
        provider_mock.queue("openai", jr(200, openai_ok()))
        call(orgs.a.id)
        tables = ("ai_credentials", "ai_models", "ai_routing", "ai_budgets", "ai_usage_logs")
        for org_id, expected in ((orgs.a.id, True), (orgs.b.id, False), (None, False)):
            db = SessionLocal()
            try:
                bind_context(db, org_id)
                counts = [db.execute(text(f"SELECT count(*) FROM {t}")).scalar() for t in tables]
                assert all(c > 0 for c in counts) == expected and (expected or counts == [0] * 5), (org_id, counts)
            finally:
                db.close()

    def test_a_write_for_another_organization_is_refused_by_the_database(self, orgs):
        from app.database import SessionLocal, bind_context
        db = SessionLocal()
        try:
            bind_context(db, orgs.b.id)
            with pytest.raises(Exception, match="row-level security"):
                db.execute(text("INSERT INTO ai_models (id, organization_id, provider, model_id) VALUES (:i, :o, 'openai', 'x')"),
                           {"i": str(uuid.uuid4()), "o": orgs.a.id})
        finally:
            db.rollback()
            db.close()

    def test_database_constraints(self, orgs, db_session):
        from sqlalchemy.exc import IntegrityError
        seed_credential(db_session, orgs.a.id, "openai")
        with pytest.raises(IntegrityError):  # one ACTIVE credential per organization and provider
            seed_credential(db_session, orgs.a.id, "openai", key="sk-another-key-12345678")
        db_session.rollback()
        with pytest.raises(IntegrityError):
            seed_model(db_session, orgs.a.id, "openai", "neg", input_price_micros_per_mtok=-1)
        db_session.rollback()
        with pytest.raises(IntegrityError):
            db_session.add(AiBudget(organization_id=orgs.a.id, period="weekly", limit_micros=1))
            db_session.commit()
        db_session.rollback()
        m = seed_model(db_session, orgs.a.id, "openai", "ok")
        db_session.add(AiRouting(organization_id=orgs.b.id, operation="x", primary_model_id=m.id, fallback_model_ids=[]))
        with pytest.raises(IntegrityError):  # composite FK: a route cannot point at another organization's model
            db_session.commit()
        db_session.rollback()


# ---------------------------------------------------------------------------------------------
class TestBudgets:
    def setup_budget(self, db_session, org, daily=None, monthly=None):
        for period, limit in (("daily", daily), ("monthly", monthly)):
            if limit is not None:
                db_session.add(AiBudget(organization_id=org.id, period=period, limit_micros=limit,
                                        window_start=__import__("app.ai.budget", fromlist=["x"]).window_start(period)))
        db_session.commit()

    def budgets(self, db_session, org):
        db_session.expire_all()
        return {b.period: (b.spent_micros, b.reserved_micros) for b in db_session.query(AiBudget).filter(AiBudget.organization_id == org.id)}

    def basic(self, db_session, org, **model_kw):
        seed_credential(db_session, org.id, "openai")
        return seed_model(db_session, org.id, "openai", "m", **(model_kw or PRICED))

    def test_spend_is_settled_to_the_real_cost_and_nothing_stays_reserved(self, orgs, db_session, provider_mock):
        self.basic(db_session, orgs.a)
        self.setup_budget(db_session, orgs.a, daily=10_000_000, monthly=50_000_000)
        provider_mock.queue("openai", jr(200, openai_ok(inp=1000, out=500)))  # 10_500 micros
        call(orgs.a.id)
        assert self.budgets(db_session, orgs.a) == {"daily": (10_500, 0), "monthly": (10_500, 0)}

    def test_an_exhausted_budget_blocks_before_any_provider_call(self, orgs, db_session, provider_mock):
        self.basic(db_session, orgs.a)
        self.setup_budget(db_session, orgs.a, daily=1)  # one millionth of a dollar
        with pytest.raises(E.AIError) as caught:
            call(orgs.a.id)
        assert caught.value.kind == E.BUDGET_EXCEEDED and provider_mock.requests == []
        assert self.budgets(db_session, orgs.a) == {"daily": (0, 0)}
        assert E.HTTP_STATUS[E.BUDGET_EXCEEDED] == 429 and not caught.value.fallback_eligible

    def test_the_tightest_of_daily_and_monthly_wins_and_nothing_is_half_reserved(self, orgs, db_session, provider_mock):
        self.basic(db_session, orgs.a)
        self.setup_budget(db_session, orgs.a, daily=100_000_000, monthly=1)
        with pytest.raises(E.AIError) as caught:
            call(orgs.a.id)
        assert caught.value.kind == E.BUDGET_EXCEEDED
        assert self.budgets(db_session, orgs.a) == {"daily": (0, 0), "monthly": (0, 0)}  # all-or-nothing

    def test_a_failed_provider_call_releases_the_reservation(self, orgs, db_session, provider_mock):
        self.basic(db_session, orgs.a)
        self.setup_budget(db_session, orgs.a, daily=10_000_000)
        provider_mock.queue("openai", jr(401, {"error": {"message": "x", "code": "invalid_api_key"}}))
        with pytest.raises(E.AIError):
            call(orgs.a.id)
        assert self.budgets(db_session, orgs.a) == {"daily": (0, 0)}

    def test_a_model_without_a_price_cannot_be_used_under_a_budget_but_can_without_one(self, orgs, db_session, provider_mock):
        seed_credential(db_session, orgs.a.id, "openai")
        seed_model(db_session, orgs.a.id, "openai", "unpriced")
        provider_mock.queue("openai", jr(200, openai_ok()))
        assert call(orgs.a.id).response.text == "hello"           # no budget: nothing to enforce
        self.setup_budget(db_session, orgs.a, daily=10_000_000)
        with pytest.raises(E.AIError) as caught:                    # budget: cost cannot be proven -> fail closed
            call(orgs.a.id)
        assert caught.value.kind == E.BUDGET_EXCEEDED and "price" in caught.value.message
        assert len(provider_mock.requests) == 1

    def test_a_disabled_budget_is_not_enforced(self, orgs, db_session, provider_mock):
        self.basic(db_session, orgs.a)
        self.setup_budget(db_session, orgs.a, daily=1)
        db_session.query(AiBudget).update({"enabled": False})
        db_session.commit()
        provider_mock.queue("openai", jr(200, openai_ok()))
        assert call(orgs.a.id).response.text == "hello"

    def test_a_new_window_resets_the_counters(self, orgs, db_session, provider_mock):
        self.basic(db_session, orgs.a)
        self.setup_budget(db_session, orgs.a, daily=20_000)
        db_session.query(AiBudget).update({"spent_micros": 20_000, "window_start": text("now() - interval '2 days'")}, synchronize_session=False)
        db_session.commit()
        provider_mock.queue("openai", jr(200, openai_ok(inp=1000, out=500)))
        call(orgs.a.id)  # yesterday's spend does not count today
        assert self.budgets(db_session, orgs.a) == {"daily": (10_500, 0)}

    def test_budgets_are_per_organization(self, orgs, db_session, provider_mock):
        self.basic(db_session, orgs.a)
        self.basic(db_session, orgs.b)
        self.setup_budget(db_session, orgs.a, daily=1)
        provider_mock.always("openai", lambda r: jr(200, openai_ok()))
        with pytest.raises(E.AIError):
            call(orgs.a.id)
        assert call(orgs.b.id).response.text == "hello"  # B has no limit and is unaffected by A's

    def test_concurrent_requests_can_never_exceed_the_limit(self, orgs, db_session, provider_mock):
        """24 simultaneous gateway calls compete for a budget that admits exactly 5 of them."""
        seed_credential(db_session, orgs.a.id, "openai")
        seed_model(db_session, orgs.a.id, "openai", "m", input_price_micros_per_mtok=0, output_price_micros_per_mtok=1_000_000_000)
        max_tokens = 100
        per_call = pricing.tokens_cost_micros(max_tokens, 1_000_000_000)      # 100_000 micros each (worst case == actual)
        self.setup_budget(db_session, orgs.a, daily=per_call * 5 + per_call // 2)
        provider_mock.always("openai", lambda r: jr(200, openai_ok(inp=0, out=max_tokens)))
        org_id = orgs.a.id  # plain value: ORM objects of the test session must not be touched from threads
        outcomes, barrier = [], threading.Barrier(24)

        def attempt(_):
            barrier.wait()  # released together BEFORE taking a pooled connection (avoids pool-exhaustion deadlock)
            gateway, db = gateway_for(org_id)
            try:
                asyncio.run(gateway.chat("op", T.ChatRequest(messages=[T.Message.user("x")], max_tokens=max_tokens)))
                outcomes.append("ok")
            except E.AIError as exc:
                outcomes.append(exc.kind)
            finally:
                db.close()

        with ThreadPoolExecutor(max_workers=24) as pool:
            list(pool.map(attempt, range(24)))
        assert outcomes.count("ok") == 5 and outcomes.count(E.BUDGET_EXCEEDED) == 19
        spent, reserved = self.budgets(db_session, orgs.a)["daily"]
        assert spent == per_call * 5 and reserved == 0 and spent <= per_call * 5 + per_call // 2
        assert len([r for r in logs(db_session, orgs.a.id) if r.status == "success"]) == 5
        assert len(provider_mock.requests) == 5  # the 19 refused calls never reached the provider

    def test_concurrent_calls_of_two_organizations_never_mix_keys_or_budgets(self, orgs, db_session, provider_mock):
        for org, key in ((orgs.a, "sk-key-of-org-A-1111"), (orgs.b, "sk-key-of-org-B-2222")):
            seed_credential(db_session, org.id, "openai", key=key)
            seed_model(db_session, org.id, "openai", "m", **PRICED)
            self.setup_budget(db_session, org, daily=500_000_000)
        provider_mock.always("openai", lambda r: jr(200, openai_ok(text=r.headers["authorization"])))
        errors = []
        ids = (orgs.a.id, orgs.b.id)

        def attempt(i):
            org_id = ids[i % 2]
            expected = "Bearer sk-key-of-org-A-1111" if i % 2 == 0 else "Bearer sk-key-of-org-B-2222"
            result = call(org_id)
            if result.response.text != expected:
                errors.append((i, result.response.text))

        with ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(attempt, range(36)))
        assert errors == []
        assert self.budgets(db_session, orgs.a)["daily"][1] == 0 and self.budgets(db_session, orgs.b)["daily"][1] == 0
