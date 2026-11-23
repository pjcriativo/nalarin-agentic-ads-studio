"""Etapa 02.2 backend: recovery of abandoned budget reservations and cached-token pricing.
Provider answers are scripted; nothing reaches a real provider."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.ai import budget, pricing
from app.ai import errors as E
from app.ai import types as T
from app.models import AiBudget, AiBudgetReservation, AuditLog
from tests.unit.ai_support import gateway_for, jr, openai_ok, seed_credential, seed_model

PRICED = dict(input_price_micros_per_mtok=3_000_000, output_price_micros_per_mtok=15_000_000)


@pytest.fixture
def org(make_user, make_org):
    return make_org("CC A", owner=make_user("cc-a@example.com"))


def add_budget(db, org, period="daily", limit=1_000_000):
    row = AiBudget(organization_id=org.id, period=period, limit_micros=limit, window_start=budget.window_start(period))
    db.add(row)
    db.commit()
    return row


def counters(db, org, period="daily"):
    db.expire_all()
    row = db.query(AiBudget).filter(AiBudget.organization_id == org.id, AiBudget.period == period).one()
    return row.spent_micros, row.reserved_micros


def bound_session(org_id):
    from app.database import SessionLocal, bind_context
    db = SessionLocal()
    bind_context(db, org_id)
    return db


class TestAbandonedReservations:
    def test_a_reservation_is_recorded_and_removed_when_settled(self, org, db_session):
        add_budget(db_session, org)
        db = bound_session(org.id)
        try:
            held = budget.reserve(db, org.id, 400_000)
            assert db_session.query(AiBudgetReservation).count() == 1 and counters(db_session, org) == (0, 400_000)
            budget.settle(db, org.id, held, 150_000)
        finally:
            db.close()
        assert db_session.query(AiBudgetReservation).count() == 0 and counters(db_session, org) == (150_000, 0)

    def test_a_crash_leaves_a_row_that_is_charged_not_released(self, org, db_session):
        """Process dies after reserving: the amount may already have been billed, so it is CHARGED."""
        add_budget(db_session, org, limit=1_000_000)
        db = bound_session(org.id)
        try:
            budget.reserve(db, org.id, 600_000)          # ...and the process "dies" here: no settle, no release
            future = datetime.now(timezone.utc) + timedelta(seconds=10_000)
            assert budget.recover_abandoned(db, org.id, now=future) == 1
        finally:
            db.close()
        assert counters(db_session, org) == (600_000, 0)          # moved from reserved to SPENT
        assert db_session.query(AiBudgetReservation).count() == 0
        assert db_session.query(AuditLog).filter(AuditLog.action == "ai.budget.reservation_recovered").count() == 1

    def test_recovery_can_never_open_room_above_the_limit(self, org, db_session):
        add_budget(db_session, org, limit=1_000_000)
        db = bound_session(org.id)
        try:
            budget.reserve(db, org.id, 700_000)
            future = datetime.now(timezone.utc) + timedelta(seconds=10_000)
            budget.recover_abandoned(db, org.id, now=future)
            with pytest.raises(E.AIError) as caught:              # 700k are spent: 400k more does not fit
                budget.reserve(db, org.id, 400_000, now=future)
            assert caught.value.kind == E.BUDGET_EXCEEDED
            budget.reserve(db, org.id, 300_000, now=future)       # 300k does
        finally:
            db.close()

    def test_a_live_reservation_younger_than_the_ttl_is_untouched(self, org, db_session):
        add_budget(db_session, org)
        db = bound_session(org.id)
        try:
            budget.reserve(db, org.id, 100_000)
            assert budget.recover_abandoned(db, org.id) == 0
        finally:
            db.close()
        assert counters(db_session, org) == (0, 100_000)

    def test_reserve_itself_recovers_stale_rows_before_judging_the_limit(self, org, db_session):
        add_budget(db_session, org, limit=500_000)
        db = bound_session(org.id)
        try:
            budget.reserve(db, org.id, 400_000)
            future = datetime.now(timezone.utc) + timedelta(seconds=10_000)
            with pytest.raises(E.AIError):                         # stale 400k is now SPENT: 200k cannot fit
                budget.reserve(db, org.id, 200_000, now=future)
        finally:
            db.close()
        assert counters(db_session, org) == (400_000, 0)

    def test_a_late_settle_after_recovery_does_not_double_charge(self, org, db_session):
        add_budget(db_session, org)
        db = bound_session(org.id)
        try:
            held = budget.reserve(db, org.id, 300_000)
            budget.recover_abandoned(db, org.id, now=datetime.now(timezone.utc) + timedelta(seconds=10_000))
            budget.settle(db, org.id, held, 250_000)               # the slow process finally finishes
        finally:
            db.close()
        assert counters(db_session, org) == (300_000, 0)           # charged once, at the estimate

    def test_a_reservation_of_a_previous_window_does_not_touch_the_new_one(self, org, db_session):
        add_budget(db_session, org)
        db = bound_session(org.id)
        try:
            held = budget.reserve(db, org.id, 300_000)
            db_session.query(AiBudget).update({"window_start": budget.window_start("daily") + timedelta(days=1),
                                               "spent_micros": 10, "reserved_micros": 0})
            db_session.commit()
            budget.settle(db, org.id, held, 200_000)
        finally:
            db.close()
        assert counters(db_session, org) == (10, 0)

    def test_two_concurrent_recoveries_charge_once(self, org, db_session):
        import threading
        add_budget(db_session, org)
        db = bound_session(org.id)
        budget.reserve(db, org.id, 300_000)
        db.close()
        future, org_id, results = datetime.now(timezone.utc) + timedelta(seconds=10_000), org.id, []
        barrier = threading.Barrier(2)

        def recover():
            barrier.wait()
            session = bound_session(org_id)
            try:
                results.append(budget.recover_abandoned(session, org_id, now=future))
            finally:
                session.close()
        threads = [threading.Thread(target=recover) for _ in range(2)]
        [t.start() for t in threads]
        [t.join(30) for t in threads]
        assert sorted(results) == [0, 1] and counters(db_session, org) == (300_000, 0)

    def test_the_ttl_is_always_longer_than_the_provider_timeout(self):
        from app.core.config import settings
        assert settings.AI_RESERVATION_TTL_SECONDS >= 5 * settings.AI_TIMEOUT_SECONDS

    def test_the_gateway_leaves_no_reservation_row_behind(self, org, db_session, provider_mock):
        add_budget(db_session, org)
        seed_credential(db_session, org.id, "openai")
        seed_model(db_session, org.id, "openai", "m", **PRICED)
        provider_mock.queue("openai", jr(200, openai_ok()), jr(401, {"error": {"message": "x", "code": "invalid_api_key"}}))
        gateway, db = gateway_for(org.id)
        try:
            asyncio.run(gateway.chat("op", T.ChatRequest(messages=[T.Message.user("hi")], max_tokens=50)))
            with pytest.raises(E.AIError):
                asyncio.run(gateway.chat("op", T.ChatRequest(messages=[T.Message.user("hi")], max_tokens=50)))
        finally:
            db.close()
        assert db_session.query(AiBudgetReservation).count() == 0 and counters(db_session, org)[1] == 0


class TestCachedTokenPricing:
    def model(self, **kw):
        return SimpleNamespace(input_price_micros_per_mtok=3_000_000, output_price_micros_per_mtok=15_000_000,
                               cached_input_price_micros_per_mtok=None, max_output_tokens=None, **kw)

    def test_without_a_cache_price_cached_tokens_cost_the_input_price(self):
        usage = T.Usage(input_tokens=1000, output_tokens=500, cached_input_tokens=800)
        assert pricing.usage_cost_micros(usage, self.model()) == 3000 + 7500

    def test_with_a_cache_price_cached_tokens_use_it(self):
        model = self.model()
        model.cached_input_price_micros_per_mtok = 300_000          # a tenth of the input price
        usage = T.Usage(input_tokens=1000, output_tokens=500, cached_input_tokens=800)
        # 200 fresh * $3/M = 600 + 800 cached * $0.30/M = 240 + 500 out * $15/M = 7500
        assert pricing.usage_cost_micros(usage, model) == 600 + 240 + 7500

    def test_cached_tokens_never_exceed_the_prompt_and_zero_is_free(self):
        model = self.model()
        model.cached_input_price_micros_per_mtok = 0
        assert pricing.usage_cost_micros(T.Usage(input_tokens=100, output_tokens=0, cached_input_tokens=999), model) == 0

    def test_a_model_without_prices_is_still_unpriced(self):
        model = self.model()
        model.input_price_micros_per_mtok = None
        model.cached_input_price_micros_per_mtok = 1
        assert pricing.usage_cost_micros(T.Usage(input_tokens=10, cached_input_tokens=5), model) is None

    def test_the_estimate_uses_the_dearer_price_so_it_cannot_under_reserve(self):
        request = T.ChatRequest(messages=[T.Message.user("x" * 3000)], max_tokens=100)
        cheap, dear = self.model(), self.model()
        dear.cached_input_price_micros_per_mtok = 9_000_000
        assert pricing.estimate_cost_micros(request, dear) > pricing.estimate_cost_micros(request, cheap)

    def test_the_gateway_logs_the_cached_cost_through_the_catalog_price(self, org, db_session, provider_mock):
        seed_credential(db_session, org.id, "openai")
        seed_model(db_session, org.id, "openai", "m", cached_input_price_micros_per_mtok=300_000, **PRICED)
        provider_mock.queue("openai", jr(200, openai_ok(inp=1000, out=500, usage_extra={"prompt_tokens_details": {"cached_tokens": 800}})))
        gateway, db = gateway_for(org.id)
        try:
            result = asyncio.run(gateway.chat("op", T.ChatRequest(messages=[T.Message.user("hi")], max_tokens=600)))
        finally:
            db.close()
        assert result.cost_micros == 600 + 240 + 7500
        row = db_session.execute(text("SELECT cached_input_tokens, cost_micros FROM ai_usage_logs")).one()
        assert tuple(row) == (800, 600 + 240 + 7500)


class TestApiAdditions:
    @pytest.fixture
    def w(self, make_user, make_org, login_headers):
        user = make_user("cc-api@example.com")
        org = make_org("CC API", owner=user)
        return SimpleNamespace(org=org, h=login_headers(user, org), user=user)

    def test_model_cached_price_roundtrip_and_validation(self, client, w):
        base = {"provider": "openai", "model_id": "m", "input_price_usd_per_mtok": "3", "output_price_usd_per_mtok": "15"}
        r = client.post("/api/v1/ai/models", headers=w.h, json={**base, "cached_input_price_usd_per_mtok": "0.3"})
        assert r.status_code == 201 and r.json()["cached_input_price_usd_per_mtok"] == pytest.approx(0.3)
        mid = r.json()["id"]
        assert client.patch(f"/api/v1/ai/models/{mid}", headers=w.h, json={"cached_input_price_usd_per_mtok": None}).json()["cached_input_price_usd_per_mtok"] is None
        assert client.post("/api/v1/ai/models", headers=w.h, json={**base, "model_id": "n", "cached_input_price_usd_per_mtok": "-1"}).status_code == 422

    def test_usage_summary_has_cached_tokens_and_a_daily_series(self, client, db_session, w):
        from app.models import AiUsageLog
        for i, cost in enumerate((100, 200)):
            db_session.add(AiUsageLog(organization_id=w.org.id, request_id=f"r{i}", attempt=1, operation="op", provider="openai",
                                      model_id="m", status="success", input_tokens=10, output_tokens=5, cached_input_tokens=4, cost_micros=cost))
        db_session.commit()
        body = client.get("/api/v1/ai/usage/summary", headers=w.h).json()
        assert body["totals"]["cached_input_tokens"] == 8
        assert len(body["by_day"]) == 1 and body["by_day"][0]["requests"] == 2
        assert body["by_day"][0]["cost_usd"] == pytest.approx(0.0003)

    def test_listing_budgets_charges_stale_reservations(self, client, db_session, w):
        add_budget(db_session, w.org)
        db = bound_session(w.org.id)
        try:
            budget.reserve(db, w.org.id, 200_000)
        finally:
            db.close()
        db_session.query(AiBudgetReservation).update({"created_at": datetime.now(timezone.utc) - timedelta(days=1)})
        db_session.commit()
        view = client.get("/api/v1/ai/budgets", headers=w.h).json()[0]
        assert view["spent_usd"] == pytest.approx(0.2) and view["reserved_usd"] == 0
