"""Agents + real-data tools, end to end through the real worker loop (etapa 02.5): real vs estimate vs hypothesis, no invented
metrics, tenant isolation of the tool context, limits, hostile data. Scripted model + SDK-shaped Google fakes: no network."""
import copy
import json

import pytest
from sqlalchemy import text

from app.agents import executor
from app.core.config import settings
from app.services import google_ads_reader as reader
from tests.unit.agents_support import (COPY, MARKET, PERFORMANCE, PLAN, PRODUCT, REPORT, STRATEGY, ScriptedGateway, UseTools, create_task_row, drain,
                                       say_json, snapshot, world_of)
from tests.unit.gads_support import Account, FakeGoogle, campaign_row, connect, idea, term_row
from tests.unit.test_agents_google_tools import (A_CUSTOMER, A_TOKEN, B_CUSTOMER, B_TOKEN, LAST_30, acme_a)

PAGE = """<html lang="pt-BR"><head><title>Widget Pro</title></head><body><h1>Widget Pro</h1>
<p>Por apenas R$ 197,00 com garantia de 30 dias.</p><button>Comprar agora</button></body></html>"""
URL = "https://shop.example.com/widgets"


@pytest.fixture
def w(make_user, make_org, add_member, login_headers):
    return world_of(make_user, make_org, add_member, login_headers)


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    for key, value in (("GOOGLE_ADS_CLIENT_ID", "cid"), ("GOOGLE_ADS_CLIENT_SECRET", "csecret"), ("GOOGLE_ADS_DEVELOPER_TOKEN", "devtoken")):
        monkeypatch.setattr(settings, key, value)
    monkeypatch.setattr(settings, "AGENT_MAX_ACTIVE_TASKS_PER_ORG", 100)
    monkeypatch.setattr(settings, "AGENT_RETRY_BASE_SECONDS", 30.0)


@pytest.fixture
def google(monkeypatch):
    fake = FakeGoogle()
    monkeypatch.setattr(reader, "CLIENT_FACTORY", fake.factory)
    return fake


@pytest.fixture
def connected(w, google, add_connection):
    google.accounts[A_CUSTOMER] = acme_a()
    google.tokens[A_TOKEN] = [A_CUSTOMER]
    google.accounts[B_CUSTOMER] = Account(B_CUSTOMER, name="Beta B", currency="USD",
                                          perf_campaigns=[campaign_row(777, "Beta Campaign", impressions=9, clicks=3, cost_micros=1_000_000)])
    google.tokens[B_TOKEN] = [B_CUSTOMER]
    connect(add_connection, w.org, w.users["owner"], A_CUSTOMER, A_TOKEN, login_customer_id="9999999999")
    connect(add_connection, w.other, w.other_owner, B_CUSTOMER, B_TOKEN)
    return google


def gateway(monkeypatch, *script):
    gw = ScriptedGateway(*script)
    monkeypatch.setattr(executor, "GATEWAY_FACTORY", lambda db, org_id, user_id: gw)
    return gw


def task(w, agent, context=None, org=None, user=None, **kw):
    return create_task_row(org or w.org, user or w.users["manager"], agent_key=agent, objective="Analyze this for Google Search ads",
                           context=context, **kw)


def metric(entity, name, value):
    return {"entity": entity, "name": name, "value": value}


def basis(statement, kind, source, *metrics):
    return {"statement": statement, "data_kind": kind, "source": source, "metrics": list(metrics)}


def perf(*items):
    return {**PERFORMANCE, "findings": ["Brand A converts"], "data_basis": list(items)}


PERF_CALL = ("google_ads.get_campaign_performance", LAST_30)
REAL_CLICKS = basis("Brand A had 120 clicks in January", "real", "google_ads_api", metric("campaign:111", "clicks", 120))


def outcome(db_session, task_id):
    t, runs, steps = snapshot(db_session, task_id)
    return t, runs, steps


# ================================================================================================ performance analyst
class TestPerformanceAnalyst:
    def test_real_account_metrics_are_read_cited_and_stored(self, w, connected, monkeypatch, db_session):
        gw = gateway(monkeypatch, UseTools([PERF_CALL]), say_json(perf(REAL_CLICKS)))
        task_id = task(w, "performance_analyst")
        assert drain() == {"completed": 1}
        t, runs, steps = outcome(db_session, task_id)
        basis_out = t.result["output"]["data_basis"][0]
        assert basis_out["data_kind"] == "real" and basis_out["source"] == "google_ads_api" and basis_out["metrics"][0]["value"] == 120
        tool_step = next(s for s in steps if s.kind == "tool")
        assert tool_step.name == "google_ads.get_campaign_performance" and tool_step.status == "ok"
        assert "<untrusted_tool_output" in json.dumps(gw.requests[1][1].messages[-1].parts[0].__dict__, default=str)     # the data reaches the model as untrusted

    def test_a_fabricated_metric_is_rejected_with_one_repair_turn_then_accepted_when_corrected(self, w, connected, monkeypatch, db_session):
        fabricated = basis("Brand A had 999 clicks", "real", "google_ads_api", metric("campaign:111", "clicks", 999))
        gw = gateway(monkeypatch, UseTools([PERF_CALL]), say_json(perf(fabricated)), say_json(perf(REAL_CLICKS)))
        task_id = task(w, "performance_analyst")
        assert drain() == {"completed": 1}
        t, _r, steps = outcome(db_session, task_id)
        repair = [s for s in steps if s.name == "output_repair"]
        assert len(repair) == 1 and "does not match any value the tools returned" in json.dumps(repair[0].output)
        assert t.result["output"]["data_basis"][0]["metrics"][0]["value"] == 120
        assert "does not match" in gw.requests[2][1].messages[-1].parts[0].text                      # the model is told exactly what was wrong

    def test_a_second_fabrication_fails_the_task_and_nothing_fabricated_is_stored(self, w, connected, monkeypatch, db_session):
        fabricated = basis("Brand A had 999 clicks", "real", "google_ads_api", metric("campaign:111", "clicks", 999))
        gateway(monkeypatch, UseTools([PERF_CALL]), say_json(perf(fabricated)), say_json(perf(fabricated)))
        task_id = task(w, "performance_analyst")
        assert drain() == {"failed": 1}
        t, *_ = outcome(db_session, task_id)
        assert t.error_kind == "invalid_output" and t.result is None and "999" not in json.dumps(t.result)

    def test_claiming_account_data_without_reading_it_is_rejected(self, w, connected, monkeypatch, db_session):
        gateway(monkeypatch, say_json(perf(REAL_CLICKS)), say_json(perf(REAL_CLICKS)))
        task_id = task(w, "performance_analyst")
        assert drain() == {"failed": 1}
        assert outcome(db_session, task_id)[0].error_kind == "invalid_output"

    def test_a_hypothesis_cannot_carry_numbers(self, w, connected, monkeypatch, db_session):
        bad = basis("Branded terms probably convert", "hypothesis", "model_knowledge", metric("campaign:111", "clicks", 120))
        gateway(monkeypatch, UseTools([PERF_CALL]), say_json(perf(bad)), say_json(perf(bad)))
        task_id = task(w, "performance_analyst")
        assert drain() == {"failed": 1}
        assert outcome(db_session, task_id)[0].error_kind == "invalid_output"

    def test_model_knowledge_can_only_be_a_hypothesis(self, w, connected, monkeypatch, db_session):
        bad = basis("Industry CTR is 3%", "real", "model_knowledge")
        gateway(monkeypatch, say_json(perf(bad)), say_json(perf(bad)))
        task_id = task(w, "performance_analyst")
        assert drain() == {"failed": 1}

    def test_without_a_connected_account_the_tool_says_unavailable_and_the_agent_must_not_claim_data(self, w, google, monkeypatch, db_session):
        honest = perf(basis("No account data was available", "hypothesis", "model_knowledge"))
        honest["data_limitations"] = ["Google Ads is not connected: no measured data."]
        gw = gateway(monkeypatch, UseTools([PERF_CALL]), say_json(honest))
        task_id = task(w, "performance_analyst")
        assert drain() == {"completed": 1}
        t, _r, steps = outcome(db_session, task_id)
        preview = json.dumps(next(s for s in steps if s.kind == "tool").output)
        assert '\\"available\\": false' in preview or '"available": false' in preview.replace('\\"', '"')
        assert "not_connected" in preview and "Do not estimate or invent" in preview and google.factory_calls == []
        # ...and claiming data anyway is rejected
        gateway(monkeypatch, UseTools([PERF_CALL]), say_json(perf(REAL_CLICKS)), say_json(perf(REAL_CLICKS)))
        second = task(w, "performance_analyst")
        assert drain() == {"failed": 1}

    def test_numbers_the_requester_supplied_may_be_cited_as_supplied_context_and_only_those(self, w, monkeypatch, db_session):
        context = {"export": {"clicks": 55, "cost": 12.5}}
        ok = perf(basis("The export shows 55 clicks", "real", "supplied_context", metric("export", "clicks", 55)))
        gateway(monkeypatch, say_json(ok))
        good = task(w, "performance_analyst", context=context)
        assert drain() == {"completed": 1}
        bad = perf(basis("The export shows 56 clicks", "real", "supplied_context", metric("export", "clicks", 56)))
        gateway(monkeypatch, say_json(bad), say_json(bad))
        wrong = task(w, "performance_analyst", context=context)
        assert drain() == {"failed": 1}

    def test_a_rate_limited_read_is_visible_to_the_agent_and_costs_google_nothing(self, w, connected, google, monkeypatch, db_session):
        monkeypatch.setattr(settings, "AGENT_GOOGLE_ADS_MAX_CALLS_PER_TASK", 1)
        honest = perf(basis("Only one read was allowed", "hypothesis", "model_knowledge"))
        gateway(monkeypatch, UseTools([PERF_CALL]), UseTools([("google_ads.get_ad_performance", LAST_30)]), say_json(honest))
        task_id = task(w, "performance_analyst")
        assert drain() == {"completed": 1}
        _t, _r, steps = outcome(db_session, task_id)
        tools = [s for s in steps if s.kind == "tool"]
        assert "rate_limited" in json.dumps(tools[1].output) and "rate_limited" not in json.dumps(tools[0].output)
        assert sum(1 for _c, q in google.queries if "FROM ad_group_ad" in q and "segments.date" in q) == 0       # the 2nd read never reached Google

    def test_hostile_search_terms_are_flagged_and_stay_data(self, w, connected, monkeypatch, db_session):
        gw = gateway(monkeypatch, UseTools([("google_ads.get_search_terms_report", LAST_30)]), say_json(perf()))
        task_id = task(w, "performance_analyst")
        assert drain() == {"completed": 1}
        _t, _r, steps = outcome(db_session, task_id)
        tool = next(s for s in steps if s.kind == "tool")
        assert tool.injection_suspected is True                                              # "ignore previous instructions..." inside a search term
        assert "agent.injection.suspected" in [r[0] for r in db_session.execute(text("SELECT action FROM audit_logs WHERE target = :t"), {"t": task_id})]


# ================================================================================================ market researcher
def market(candidates, **extra):
    out = copy.deepcopy(MARKET)
    out["keyword_candidates"] = candidates
    out.update(extra)
    return out


def kw(keyword, kind="hypothesis", source="model_knowledge", *metrics):
    return {"keyword": keyword, "intent": "transactional", "match_type_suggestion": "phrase", "rationale": "clear buying intent",
            "data_kind": kind, "source": source, "metrics": list(metrics)}


IDEAS_CALL = ("google_ads.keyword_ideas", {"seed_keywords": ["running shoes"], "language_code": "pt", "geo_target_ids": ["2076"]})


class TestMarketResearcher:
    def test_planner_figures_are_labelled_estimates_and_verified(self, w, connected, monkeypatch, db_session):
        answer = market([kw("running shoes", "estimate", "google_keyword_planner", metric("keyword:running shoes", "avg_monthly_searches", 12100),
                            metric("keyword:running shoes", "low_top_of_page_bid_micros", 400000)),
                         kw("shoes for flat feet")], evidence="keyword_planner")
        gateway(monkeypatch, UseTools([IDEAS_CALL]), say_json(answer))
        task_id = task(w, "market_researcher")
        assert drain() == {"completed": 1}
        out = outcome(db_session, task_id)[0].result["output"]
        by_kw = {k["keyword"]: k for k in out["keyword_candidates"]}
        assert by_kw["running shoes"]["data_kind"] == "estimate" and by_kw["running shoes"]["metrics"][0]["value"] == 12100
        assert by_kw["shoes for flat feet"]["data_kind"] == "hypothesis" and by_kw["shoes for flat feet"]["metrics"] == []

    def test_a_made_up_volume_is_rejected(self, w, connected, monkeypatch, db_session):
        fake = market([kw("running shoes", "estimate", "google_keyword_planner", metric("keyword:running shoes", "avg_monthly_searches", 50000))])
        gateway(monkeypatch, UseTools([IDEAS_CALL]), say_json(fake), say_json(fake))
        task_id = task(w, "market_researcher")
        assert drain() == {"failed": 1}
        assert outcome(db_session, task_id)[0].error_kind == "invalid_output"

    def test_a_keyword_the_planner_did_not_return_cannot_get_a_volume(self, w, connected, monkeypatch, db_session):
        fake = market([kw("zebra sneakers", "estimate", "google_keyword_planner", metric("keyword:zebra sneakers", "avg_monthly_searches", 900))])
        gateway(monkeypatch, UseTools([IDEAS_CALL]), say_json(fake), say_json(fake))
        task_id = task(w, "market_researcher")
        assert drain() == {"failed": 1}

    def test_without_planner_data_every_keyword_stays_a_hypothesis_and_an_estimate_is_rejected(self, w, google, monkeypatch, db_session):
        honest = market([kw("running shoes")], verification_notice="No volume or cost data was available.")
        gateway(monkeypatch, UseTools([IDEAS_CALL]), say_json(honest))
        ok = task(w, "market_researcher")
        assert drain() == {"completed": 1}
        claim = market([kw("running shoes", "estimate", "google_keyword_planner", metric("keyword:running shoes", "avg_monthly_searches", 12100))])
        gateway(monkeypatch, UseTools([IDEAS_CALL]), say_json(claim), say_json(claim))
        bad = task(w, "market_researcher")
        assert drain() == {"failed": 1}

    def test_the_default_candidate_is_an_honest_hypothesis_without_any_tool(self, w, monkeypatch, db_session):
        gateway(monkeypatch, say_json(MARKET))
        task_id = task(w, "market_researcher")
        assert drain() == {"completed": 1}
        candidate = outcome(db_session, task_id)[0].result["output"]["keyword_candidates"][0]
        assert (candidate["data_kind"], candidate["source"], candidate["metrics"]) == ("hypothesis", "model_knowledge", [])

    def test_planner_estimates_cannot_be_relabelled_as_real_or_attributed_to_the_account(self, w, connected, monkeypatch, db_session):
        for kind, source in (("real", "google_keyword_planner"), ("estimate", "google_ads_api")):
            bad = market([kw("running shoes", kind, source, metric("keyword:running shoes", "avg_monthly_searches", 12100))])
            gateway(monkeypatch, UseTools([IDEAS_CALL]), say_json(bad), say_json(bad))
            task(w, "market_researcher")
            assert drain() == {"failed": 1}, (kind, source)


# ================================================================================================ product analyst / strategist
def product(*items):
    return {**PRODUCT, "data_basis": list(items)}


class TestProductAndStrategy:
    def fetch(self, monkeypatch):
        async def fetch(url):
            return PAGE.encode()
        monkeypatch.setattr(executor, "FETCH_PAGE", fetch)

    def test_page_facts_are_real_with_a_verified_price_and_inferences_are_hypotheses(self, w, monkeypatch, db_session):
        self.fetch(monkeypatch)
        answer = product(basis("The page sells it for R$ 197,00 with a 30-day guarantee", "real", "product_page", metric(f"page:{URL}", "price", 197)),
                         basis("Buyers are probably price sensitive", "hypothesis", "model_knowledge"))
        gateway(monkeypatch, UseTools([("web.analyze_page", {"url": URL})]), say_json(answer))
        task_id = task(w, "product_analyst")
        assert drain() == {"completed": 1}
        t, _r, steps = outcome(db_session, task_id)
        assert next(s for s in steps if s.kind == "tool").name == "web.analyze_page"
        assert [b["data_kind"] for b in t.result["output"]["data_basis"]] == ["real", "hypothesis"]

    def test_a_price_the_page_does_not_show_is_rejected(self, w, monkeypatch, db_session):
        self.fetch(monkeypatch)
        wrong = product(basis("The page sells it for R$ 97,00", "real", "product_page", metric(f"page:{URL}", "price", 97)))
        gateway(monkeypatch, UseTools([("web.analyze_page", {"url": URL})]), say_json(wrong), say_json(wrong))
        task_id = task(w, "product_analyst")
        assert drain() == {"failed": 1}
        assert outcome(db_session, task_id)[0].error_kind == "invalid_output"

    def test_a_page_claim_without_analyzing_the_page_is_rejected(self, w, monkeypatch, db_session):
        claim = product(basis("The page has a guarantee", "real", "product_page"))
        gateway(monkeypatch, say_json(claim), say_json(claim))
        task(w, "product_analyst")
        assert drain() == {"failed": 1}

    def test_the_strategist_grounds_the_plan_on_real_structure_and_performance(self, w, connected, monkeypatch, db_session):
        plan = {**STRATEGY, "data_basis": [basis("Brand A already runs and converts", "real", "google_ads_api", metric("campaign:111", "conversions", 6.0)),
                                           basis("Generic terms need testing", "hypothesis", "model_knowledge")]}
        gateway(monkeypatch, UseTools([("google_ads.get_account_structure", {})]), UseTools([PERF_CALL]), say_json(plan))
        task_id = task(w, "search_ads_strategist")
        assert drain() == {"completed": 1}
        assert [s.name for s in outcome(db_session, task_id)[2] if s.kind == "tool"] == ["google_ads.get_account_structure", "google_ads.get_campaign_performance"]

    def test_numbers_from_an_upstream_agent_may_be_cited_as_supplied_and_invented_ones_may_not(self, w, connected, monkeypatch, db_session):
        market_out = market([kw("running shoes", "estimate", "google_keyword_planner", metric("keyword:running shoes", "avg_monthly_searches", 12100))])
        good = {**STRATEGY, "data_basis": [basis("Demand estimated at 12100 monthly searches", "real", "supplied_context", metric("keyword:running shoes", "avg_monthly_searches", 12100))]}
        bad = {**STRATEGY, "data_basis": [basis("Demand is 99999", "real", "supplied_context", metric("keyword:running shoes", "avg_monthly_searches", 99999))]}
        for strategy, expected in ((good, {"completed": 1}), (bad, {"failed": 1})):
            script = [say_json(PLAN_MS), UseTools([IDEAS_CALL]), say_json(market_out), say_json(strategy)]
            if strategy is bad:
                script.append(say_json(strategy))
            else:
                script.append(say_json(REPORT))
            gateway(monkeypatch, *script)
            create_task_row(w.org, w.users["manager"], objective="Plan search ads for running shoes")
            assert drain() == expected


PLAN_MS = {"summary": "Research then plan.", "steps": [
    {"id": "s1", "agent": "market_researcher", "objective": "Research keywords for running shoes"},
    {"id": "s2", "agent": "search_ads_strategist", "objective": "Plan the campaign", "depends_on": ["s1"]}]}


# ================================================================================================ tenancy, secrets
class TestTenancyAndSecrets:
    def test_concurrent_tasks_of_two_organizations_read_only_their_own_accounts(self, w, connected, monkeypatch, db_session):
        monkeypatch.setattr(settings, "AGENT_ORG_MAX_RUNNING", 2)
        by_org = {}

        def factory(db, org_id, user_id):
            gw = ScriptedGateway(UseTools([PERF_CALL]), say_json(perf()))
            by_org[org_id] = gw
            return gw
        monkeypatch.setattr(executor, "GATEWAY_FACTORY", factory)
        a = task(w, "performance_analyst")
        b = task(w, "performance_analyst", org=w.other, user=w.other_owner)
        assert drain(concurrency=2) == {"completed": 2}
        for task_id, mine, theirs in ((a, "Brand A", "Beta Campaign"), (b, "Beta Campaign", "Brand A")):
            _t, _r, steps = outcome(db_session, task_id)
            blob = json.dumps([s.output for s in steps if s.kind == "tool"])
            assert mine in blob and theirs not in blob
        assert set(connected.factory_calls) == {(A_TOKEN, "9999999999"), (B_TOKEN, None)}

    def test_a_task_naming_another_organizations_account_gets_nothing_and_google_is_not_called_for_it(self, w, connected, monkeypatch, db_session):
        gateway(monkeypatch, UseTools([("google_ads.get_campaign_performance", {**LAST_30, "customer_id": B_CUSTOMER})]), say_json(perf()))
        task_id = task(w, "performance_analyst")
        assert drain() == {"completed": 1}
        tool = next(s for s in outcome(db_session, task_id)[2] if s.kind == "tool")
        assert "account_not_linked" in json.dumps(tool.output) and "Beta Campaign" not in json.dumps(tool.output)
        assert connected.factory_calls == []

    def test_no_token_or_secret_reaches_steps_results_runs_or_audit(self, w, connected, monkeypatch, db_session):
        gateway(monkeypatch, UseTools([PERF_CALL]), UseTools([("google_ads.get_account_info", {})]), say_json(perf(REAL_CLICKS)))
        task_id = task(w, "performance_analyst")
        assert drain() == {"completed": 1}
        dump = json.dumps([list(map(str, row)) for row in db_session.execute(text(
            "SELECT s.input::text, s.output::text FROM agent_steps s WHERE s.task_id = :t "
            "UNION ALL SELECT r.output::text, r.error_message FROM agent_runs r WHERE r.task_id = :t "
            "UNION ALL SELECT t.result::text, t.error_message FROM agent_tasks t WHERE t.id = :t "
            "UNION ALL SELECT a.detail::text, a.action FROM audit_logs a WHERE a.target = :t"), {"t": task_id})])
        for secret in (A_TOKEN, "access-token-xyz", "devtoken", "csecret"):
            assert secret not in dump, secret

    def test_a_user_demoted_before_the_run_gets_no_google_data(self, w, connected, monkeypatch, db_session):
        from app.models import OrganizationMember
        gw = gateway(monkeypatch, UseTools([PERF_CALL]), say_json(perf()))
        task_id = task(w, "performance_analyst")
        db_session.query(OrganizationMember).filter(OrganizationMember.user_id == w.users["manager"].id).update({"role": "viewer"})
        db_session.commit()
        assert drain() == {"failed": 1} and gw.requests == [] and connected.factory_calls == []
        assert outcome(db_session, task_id)[0].error_kind == "authorization"
