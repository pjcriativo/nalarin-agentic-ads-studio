"""Agents HTTP API: RBAC, tenant isolation, validation, idempotency, progress/results, cancellation, settings, recovery.
Background execution runs inside the request (TestClient) with a scripted gateway: no provider is contacted."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.agents import executor
from app.models import AgentTask, AuditLog
from tests.unit.agents_support import (COPY, PLAN, PRODUCT, REPORT, Say, ScriptedGateway, UseTools, create_task_row, drain, say_json, world_of)

P = "/api/v1/agents"


@pytest.fixture
def w(make_user, make_org, add_member, login_headers):
    return world_of(make_user, make_org, add_member, login_headers)


@pytest.fixture
def use(monkeypatch):
    def install(*script):
        gateway = ScriptedGateway(*script)
        monkeypatch.setattr(executor, "GATEWAY_FACTORY", lambda db, org_id, user_id: gateway)
        return gateway
    return install


@pytest.fixture
def manual(monkeypatch):
    """Tasks are created but never executed (stay pending): for tests about the API surface itself."""
    async def nothing(task_id, org_id):
        return "skipped"
    monkeypatch.setattr(executor, "execute_task", nothing)


def start(client, headers, **body):
    return client.post(f"{P}/tasks", headers=headers, json={"objective": "Analyze the widget product for Google Search ads", **body})


class TestCatalogEndpoints:
    def test_lists_the_six_specialists_with_version_tools_limits_and_routing(self, client, w):
        r = client.get(P, headers=w.h["analyst"])
        assert r.status_code == 200
        agents = {a["key"]: a for a in r.json()}
        assert set(agents) == {"product_analyst", "market_researcher", "search_ads_strategist", "ad_copywriter", "policy_reviewer", "performance_analyst"}
        one = agents["ad_copywriter"]
        assert one["version"] == "1.0.0" and len(one["instructions_hash"]) == 16 and one["enabled"] is True
        assert "ads.validate_rsa" in one["allowed_tools"] and one["limits"]["effective"]["max_steps"] == one["limits"]["defaults"]["max_steps"]
        assert one["routing"] == {"operation": "agent.ad_copywriter", "has_route": False, "inherited_from_default": False, "primary_model": None, "fallbacks": 0}
        assert one["output_schema"]["type"] == "object" and one["readiness"] and one["limitations"]
        assert "instructions" not in one and "SECURITY RULES" not in r.text                 # prompts are not served

    def test_tools_endpoint_separates_available_tools_from_planned_contracts(self, client, w):
        body = client.get(f"{P}/tools", headers=w.h["analyst"]).json()
        available = {t["name"] for t in body["available"]}
        planned = {t["name"]: t for t in body["planned"]}
        assert "plan.note" in available and "web.fetch_page" in available and not any(n.startswith("google_ads.") for n in available)
        assert all(t["side_effect"] == "none" and t["status"] == "available" for t in body["available"])
        assert planned and all(t["status"] == "contract_only" for t in planned.values()) and not (available & set(planned))
        assert planned["google_ads.apply_change_proposal"]["agent_may_hold"] is False and planned["google_ads.apply_change_proposal"]["requires_human_approval"] is True
        assert "arguments_schema" in planned["google_ads.get_campaign_performance"]


class TestRBAC:
    MATRIX = {                         # role -> (can read, can run, can manage)
        "owner": (True, True, True), "admin": (True, True, True), "manager": (True, True, False),
        "analyst": (True, False, False), "viewer": (False, False, False)}

    @pytest.mark.parametrize("role", list(MATRIX))
    def test_permission_matrix(self, client, w, manual, role):
        read, run, manage = self.MATRIX[role]
        h = w.h[role]
        refused = lambda r: r.status_code == 403
        for path in ("", "/tools", "/tasks"):
            assert refused(client.get(P + path, headers=h)) is (not read), (role, path)
        assert refused(start(client, h, agent_key="product_analyst")) is (not run), role
        assert refused(client.put(f"{P}/product_analyst/settings", headers=h, json={"enabled": True})) is (not manage), role
        assert refused(client.post(f"{P}/recover", headers=h)) is (not manage), role
        assert refused(client.post(f"{P}/tasks/nope/cancel", headers=h)) is (not run), role

    def test_unauthenticated_requests_are_refused(self, client):
        for method, path in (("get", ""), ("get", "/tools"), ("get", "/tasks"), ("post", "/tasks"), ("post", "/recover")):
            assert getattr(client, method)(P + path).status_code in (401, 403)

    def test_refusals_are_audited(self, client, w, db_session):
        client.post(f"{P}/tasks", headers=w.h["analyst"], json={"objective": "Not allowed to run this"})
        db_session.expire_all()
        assert db_session.query(AuditLog).filter(AuditLog.action == "permission.denied", AuditLog.target == "agents:run").count() == 1


class TestStartPollAndResult:
    def test_single_agent_task_runs_and_exposes_progress_steps_and_result(self, client, w, use):
        gw = use(UseTools([("plan.note", {"note": "look"})]), say_json(PRODUCT))
        r = start(client, w.h["manager"], agent_key="product_analyst")
        assert r.status_code == 201
        created = r.json()
        assert created["status"] == "pending" and created["kind"] == "single" and created["created_by_user_id"] == w.users["manager"].id
        assert gw.requests == []                                       # the API only enqueued: nothing ran in the request
        assert client.get(f"{P}/tasks/{created['id']}", headers=w.h["analyst"]).json()["status"] == "pending"
        assert drain() == {"completed": 1}                             # the worker executes it
        progress = client.get(f"{P}/tasks/{created['id']}", headers=w.h["analyst"]).json()
        assert progress["status"] == "completed" and progress["progress"] == {"completed_steps": 1, "total_steps": 1, "final": True}
        assert progress["usage"]["cost_usd"] == pytest.approx(0.002) and progress["step_count"] == 3 and progress["attempts"] == 1
        assert progress["runs"][0]["agent_key"] == "product_analyst" and progress["runs"][0]["status"] == "completed"
        steps = client.get(f"{P}/tasks/{created['id']}/steps", headers=w.h["analyst"]).json()
        assert [s["kind"] for s in steps] == ["llm", "tool", "llm"] and steps[1]["name"] == "plan.note"
        assert client.get(f"{P}/tasks/{created['id']}/steps?after=2", headers=w.h["analyst"]).json()[0]["sequence"] == 3
        result = client.get(f"{P}/tasks/{created['id']}/result", headers=w.h["analyst"]).json()
        assert result["status"] == "completed" and result["result"]["output"]["summary"] == "A concise analysis." and "Nothing was published" in result["disclaimer"]
        assert "SECURITY RULES" not in json.dumps(steps)                                     # prompts are never stored or served
        assert gw.operations == ["agent.product_analyst"] * 2

    def test_supervised_task_exposes_the_plan(self, client, w, use):
        use(say_json(PLAN), say_json(PRODUCT), say_json(COPY), say_json(REPORT))
        task_id = start(client, w.h["manager"]).json()["id"]
        drain()
        view = client.get(f"{P}/tasks/{task_id}", headers=w.h["manager"]).json()
        assert view["status"] == "completed" and view["kind"] == "supervised"
        assert [s["agent"] for s in view["plan"]["steps"]] == ["product_analyst", "ad_copywriter"] and view["plan"]["steps"][1]["depends_on"] == ["s1"]
        assert view["progress"] == {"completed_steps": 2, "total_steps": 2, "final": True}
        assert client.get(f"{P}/tasks/{task_id}/result", headers=w.h["manager"]).json()["result"]["report"]["summary"] == "Consolidated."

    def test_a_failed_task_reports_its_error_and_a_result_endpoint_still_answers(self, client, w, use):
        use(Say("nope"), Say("nope again"))
        task_id = start(client, w.h["manager"], agent_key="product_analyst").json()["id"]
        drain()
        view = client.get(f"{P}/tasks/{task_id}", headers=w.h["manager"]).json()
        assert view["status"] == "failed" and view["error"]["kind"] == "invalid_output"
        out = client.get(f"{P}/tasks/{task_id}/result", headers=w.h["manager"]).json()
        assert out["status"] == "failed" and out["error"]["kind"] == "invalid_output" and out["result"] is None

    def test_result_is_not_available_before_the_task_finishes(self, client, w, manual):
        task_id = start(client, w.h["manager"], agent_key="product_analyst").json()["id"]
        assert client.get(f"{P}/tasks/{task_id}/result", headers=w.h["manager"]).status_code == 409
        assert client.get(f"{P}/tasks/{task_id}", headers=w.h["manager"]).json()["progress"]["final"] is False

    def test_listing_with_filter_and_limit(self, client, w, use):
        use(say_json(PRODUCT), say_json(PRODUCT))
        for _ in range(2):
            start(client, w.h["manager"], agent_key="product_analyst")
        assert len(client.get(f"{P}/tasks?status=pending", headers=w.h["analyst"]).json()) == 2
        drain()
        assert len(client.get(f"{P}/tasks", headers=w.h["analyst"]).json()) == 2
        assert len(client.get(f"{P}/tasks?limit=1", headers=w.h["analyst"]).json()) == 1
        assert len(client.get(f"{P}/tasks?status=completed", headers=w.h["analyst"]).json()) == 2
        assert client.get(f"{P}/tasks?status=pending", headers=w.h["analyst"]).json() == []
        assert client.get(f"{P}/tasks?status=bogus", headers=w.h["analyst"]).status_code == 422


class TestValidation:
    @pytest.mark.parametrize("body,code", [
        ({"objective": "no"}, 422), ({"objective": "x" * 4001}, 422), ({"objective": "Valid objective", "agent_key": "ghost"}, 404),
        ({"objective": "Valid objective", "agent_key": "supervisor"}, 404), ({"objective": "Valid objective", "idempotency_key": "bad key!"}, 422),
        ({"objective": "Valid objective", "max_cost_usd": 0}, 422), ({"objective": "Valid objective", "context": {"x": "y" * 13000}}, 422),
    ])
    def test_bad_requests(self, client, w, manual, body, code):
        assert client.post(f"{P}/tasks", headers=w.h["manager"], json=body).status_code == code

    def test_unknown_task_is_404_for_every_task_endpoint(self, client, w):
        for method, path in (("get", "/tasks/x"), ("get", "/tasks/x/steps"), ("get", "/tasks/x/result"), ("post", "/tasks/x/cancel")):
            assert getattr(client, method)(P + path, headers=w.h["manager"]).status_code == 404

    def test_idempotency_key_creates_one_task_and_runs_it_once(self, client, w, use):
        gw = use(say_json(PRODUCT), say_json(PRODUCT))
        a = start(client, w.h["manager"], agent_key="product_analyst", idempotency_key="order-77").json()
        b = start(client, w.h["manager"], agent_key="product_analyst", idempotency_key="order-77").json()
        drain()
        drain()
        assert a["id"] == b["id"] and len(gw.requests) == 1

    def test_active_task_cap_answers_429(self, client, w, manual):
        for _ in range(3):
            assert start(client, w.h["manager"], agent_key="product_analyst").status_code == 201
        r = start(client, w.h["manager"], agent_key="product_analyst")
        assert r.status_code == 429 and "Too many tasks" in r.json()["detail"]

    def test_secrets_pasted_into_the_objective_or_context_never_reach_storage_or_responses(self, client, w, manual, db_session):
        secret = "sk-live-PASTEDPASTED1234567890"
        r = client.post(f"{P}/tasks", headers=w.h["manager"], json={"objective": f"Use my key {secret} to analyze", "context": {"note": f"token={secret}", "api_key": "abc"}})
        assert r.status_code == 201 and secret not in r.text
        db_session.expire_all()
        row = db_session.query(AgentTask).one()
        assert secret not in json.dumps([row.objective, row.input_context]) and "PASTEDPASTED" not in row.objective

    def test_the_audit_trail_has_no_content(self, client, w, use, db_session):
        use(say_json(PRODUCT))
        start(client, w.h["manager"], objective="Highly confidential launch plan for ACME widgets", agent_key="product_analyst")
        db_session.expire_all()
        dump = json.dumps([a.detail for a in db_session.query(AuditLog).filter(AuditLog.action.like("agent.%")).all()])
        assert "confidential" not in dump.lower() and "ACME" not in dump


class TestCancellation:
    def test_the_creator_cancels_a_pending_task(self, client, w, manual):
        task_id = start(client, w.h["manager"], agent_key="product_analyst").json()["id"]
        r = client.post(f"{P}/tasks/{task_id}/cancel", headers=w.h["manager"])
        assert r.status_code == 200 and r.json() == {"id": task_id, "status": "cancelled", "cancel_requested": True}
        assert client.get(f"{P}/tasks/{task_id}", headers=w.h["manager"]).json()["status"] == "cancelled"
        assert client.post(f"{P}/tasks/{task_id}/cancel", headers=w.h["manager"]).status_code == 409          # already final

    def test_another_manager_cannot_cancel_but_an_admin_can(self, client, w, manual, make_user, add_member, login_headers):
        task_id = start(client, w.h["manager"], agent_key="product_analyst").json()["id"]
        peer = make_user("peer-manager-ag@example.com")
        add_member(w.org, peer, role="manager")
        assert client.post(f"{P}/tasks/{task_id}/cancel", headers=login_headers(peer, w.org)).status_code == 403
        assert client.post(f"{P}/tasks/{task_id}/cancel", headers=w.h["admin"]).status_code == 200

    def test_cancellation_is_audited(self, client, w, manual, db_session):
        task_id = start(client, w.h["manager"], agent_key="product_analyst").json()["id"]
        client.post(f"{P}/tasks/{task_id}/cancel", headers=w.h["manager"])
        db_session.expire_all()
        assert db_session.query(AuditLog).filter(AuditLog.action == "agent.task.cancel_requested").count() == 1


class TestAgentSettings:
    def test_disabling_an_agent_blocks_new_tasks_and_is_reflected_in_the_list(self, client, w, manual):
        r = client.put(f"{P}/policy_reviewer/settings", headers=w.h["admin"], json={"enabled": False, "max_steps": 4, "timeout_seconds": 60})
        assert r.status_code == 200 and r.json() == {"agent_key": "policy_reviewer", "enabled": False, "max_steps": 4, "timeout_seconds": 60}
        listed = {a["key"]: a for a in client.get(P, headers=w.h["analyst"]).json()}["policy_reviewer"]
        assert listed["enabled"] is False and listed["limits"]["effective"]["max_steps"] == 4 and listed["limits"]["effective"]["timeout_seconds"] == 60
        assert start(client, w.h["manager"], agent_key="policy_reviewer").status_code == 409
        client.put(f"{P}/policy_reviewer/settings", headers=w.h["admin"], json={"enabled": True})
        assert start(client, w.h["manager"], agent_key="policy_reviewer").status_code == 201

    @pytest.mark.parametrize("body", [{"enabled": True, "max_steps": 21}, {"enabled": True, "max_steps": 0}, {"enabled": True, "timeout_seconds": 601}, {"enabled": True, "timeout_seconds": 5}])
    def test_overrides_are_capped_by_hard_limits(self, client, w, body):
        assert client.put(f"{P}/product_analyst/settings", headers=w.h["admin"], json=body).status_code == 422

    def test_unknown_agents_and_the_supervisor_are_not_configurable(self, client, w):
        for key in ("ghost", "supervisor"):
            assert client.put(f"{P}/{key}/settings", headers=w.h["admin"], json={"enabled": False}).status_code == 404

    def test_settings_are_per_organization_and_audited(self, client, w, db_session):
        client.put(f"{P}/product_analyst/settings", headers=w.h["admin"], json={"enabled": False})
        other = {a["key"]: a for a in client.get(P, headers=w.other_h).json()}
        assert other["product_analyst"]["enabled"] is True
        db_session.expire_all()
        assert db_session.query(AuditLog).filter(AuditLog.action == "agent.settings.updated", AuditLog.organization_id == w.org.id).count() == 1


class TestTenantIsolation:
    def test_another_organization_sees_and_touches_nothing_of_mine(self, client, w, use):
        use(say_json(PRODUCT))
        task_id = start(client, w.h["manager"], agent_key="product_analyst").json()["id"]
        drain()
        assert client.get(f"{P}/tasks", headers=w.other_h).json() == []
        for method, path in (("get", f"/tasks/{task_id}"), ("get", f"/tasks/{task_id}/steps"), ("get", f"/tasks/{task_id}/result"), ("post", f"/tasks/{task_id}/cancel")):
            assert getattr(client, method)(P + path, headers=w.other_h).status_code == 404, path

    def test_my_token_cannot_be_pointed_at_another_organization(self, client, w):
        assert client.get(f"{P}/tasks", headers={**w.h["owner"], "X-Organization-Id": w.other.id}).status_code == 403

    def test_the_database_enforces_the_boundary_even_for_direct_queries(self, w, db_session):
        from app.database import SessionLocal, bind_context
        task_id = create_task_row(w.org, w.users["manager"], agent_key="product_analyst")
        db_session.execute(text("INSERT INTO agent_settings (id, organization_id, agent_key, enabled) VALUES ('s1', :o, 'product_analyst', false)"), {"o": w.org.id})
        db_session.commit()
        for org_id, expected in ((w.org.id, 1), (w.other.id, 0), (None, 0)):
            db = SessionLocal()
            try:
                bind_context(db, org_id)
                assert db.execute(text("SELECT count(*) FROM agent_tasks WHERE id = :i"), {"i": task_id}).scalar() == expected
                assert db.execute(text("SELECT count(*) FROM agent_settings")).scalar() == expected
            finally:
                db.close()

    def test_recovery_endpoint_only_touches_my_organization(self, client, w, db_session, monkeypatch):
        async def nothing(task_id, org_id):
            return "skipped"
        monkeypatch.setattr(executor, "execute_task", nothing)
        mine = create_task_row(w.org, w.users["manager"], agent_key="product_analyst")
        theirs = create_task_row(w.other, w.other_owner, agent_key="product_analyst")
        for task_id in (mine, theirs):
            db_session.execute(text("UPDATE agent_tasks SET status = 'running', lease_owner = 'dead', attempts = 1, lease_expires_at = now() - interval '5 minutes' WHERE id = :i"), {"i": task_id})
        db_session.commit()
        r = client.post(f"{P}/recover", headers=w.h["admin"])
        assert r.status_code == 200 and r.json()["requeued_or_failed"] == [{"id": mine, "status": "pending"}] and r.json()["queued"] == 1
        db_session.expire_all()
        assert db_session.query(AgentTask).filter(AgentTask.id == mine).one().status == "pending"
        assert db_session.query(AgentTask).filter(AgentTask.id == theirs).one().status == "running"
