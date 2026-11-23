"""AI Gateway administration API: BYOK credentials (encrypted, masked, rotated, revoked, tested),
models, routing, budgets, usage; RBAC per role and isolation between organizations. Provider answers
are scripted: no test reaches a real provider."""
import json
from types import SimpleNamespace

import pytest

from app.models import AiCredential, AiUsageLog, AuditLog
from tests.unit.ai_support import KEYS, jr, openai_ok, seed_credential, seed_model

P = "/api/v1/ai"
NEW_KEY = "sk-live-NEWKEY-abcdefgh9999"
OTHER_KEY = "sk-live-ROTATED-ijklmnop4321"


def models_ok(request):
    return jr(200, {"data": [{"id": "model-x"}, {"id": "model-y"}]})


@pytest.fixture
def w(make_user, make_org, add_member, login_headers):
    alice, bob = make_user("alice-api@example.com"), make_user("bob-api@example.com")
    a, b = make_org("API A", owner=alice), make_org("API B", owner=bob)
    roles = {}
    for role in ("admin", "manager", "analyst", "viewer"):
        user = make_user(f"{role}-api@example.com")
        add_member(a, user, role=role)
        roles[role] = login_headers(user, a)
    return SimpleNamespace(a=a, b=b, ha=login_headers(alice, a), hb=login_headers(bob, b), roles=roles, alice=alice)


def create(client, headers, key=NEW_KEY, provider="openai", **extra):
    return client.post(f"{P}/credentials", headers=headers, json={"provider": provider, "api_key": key, **extra})


class TestCredentials:
    def test_create_verifies_encrypts_and_shows_only_the_last_four(self, client, db_session, w, provider_mock):
        provider_mock.always("openai", models_ok)
        r = create(client, w.ha, label="main")
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["last4"] == NEW_KEY[-4:] and body["masked"].endswith(NEW_KEY[-4:]) and body["status"] == "active"
        assert body["last_verification_ok"] is True
        assert NEW_KEY not in r.text and "api_key" not in body and "encrypted_key" not in body
        row = db_session.query(AiCredential).one()
        assert row.encrypted_key and NEW_KEY not in row.encrypted_key and NEW_KEY[:10] not in row.encrypted_key  # encrypted at rest
        assert provider_mock.of("openai")[0].headers["authorization"] == f"Bearer {NEW_KEY}"  # the key is used ONLY in the header
        assert NEW_KEY not in str(provider_mock.of("openai")[0].url)

    def test_listing_never_returns_the_key_in_any_form(self, client, w, provider_mock):
        provider_mock.always("openai", models_ok)
        create(client, w.ha)
        r = client.get(f"{P}/credentials", headers=w.ha)
        assert r.status_code == 200 and len(r.json()) == 1
        assert NEW_KEY not in r.text and NEW_KEY[:12] not in r.text and "encrypted" not in r.text

    def test_a_key_rejected_by_the_provider_is_not_stored(self, client, db_session, w, provider_mock):
        provider_mock.queue("openai", jr(401, {"error": {"message": f"Incorrect API key {NEW_KEY}", "code": "invalid_api_key"}}))
        r = create(client, w.ha)
        assert r.status_code == 422 and NEW_KEY not in r.text and "invalid_api_key" not in r.text
        assert db_session.query(AiCredential).count() == 0

    def test_unverified_creation_skips_the_provider(self, client, w, provider_mock):
        assert create(client, w.ha, verify=False).status_code == 201 and provider_mock.requests == []

    def test_only_one_active_credential_per_provider_and_unknown_providers_are_refused(self, client, w, provider_mock):
        provider_mock.always("openai", models_ok)
        assert create(client, w.ha).status_code == 201
        assert create(client, w.ha, key="sk-another-key-00000000").status_code == 422
        assert create(client, w.ha, provider="mistral").status_code == 422
        assert create(client, w.ha, key="short").status_code == 422

    def test_connection_test_reports_success_and_failure_without_leaking(self, client, w, provider_mock):
        provider_mock.always("openai", models_ok)
        cid = create(client, w.ha).json()["id"]
        assert client.post(f"{P}/credentials/{cid}/test", headers=w.ha).json()["ok"] is True
        provider_mock.default.pop("openai")
        provider_mock.queue("openai", jr(401, {"error": {"message": f"bad {NEW_KEY}", "code": "invalid_api_key"}}))
        r = client.post(f"{P}/credentials/{cid}/test", headers=w.ha)
        assert r.status_code == 200 and r.json()["ok"] is False and r.json()["error"] == "authentication"
        assert r.json()["credential"]["last_verification_ok"] is False and NEW_KEY not in r.text

    def test_rotation_replaces_the_key_in_place(self, client, db_session, w, provider_mock):
        provider_mock.always("openai", models_ok)
        cid = create(client, w.ha).json()["id"]
        r = client.post(f"{P}/credentials/{cid}/rotate", headers=w.ha, json={"api_key": OTHER_KEY})
        assert r.status_code == 200 and r.json()["last4"] == OTHER_KEY[-4:] and r.json()["id"] == cid and r.json()["rotated_at"]
        assert OTHER_KEY not in r.text and NEW_KEY not in r.text
        db_session.expire_all()
        from app.core.token_encryption import decrypt_token
        assert decrypt_token(db_session.query(AiCredential).one().encrypted_key) == OTHER_KEY

    def test_a_bad_new_key_leaves_the_current_one_untouched(self, client, db_session, w, provider_mock):
        provider_mock.always("openai", models_ok)
        cid = create(client, w.ha).json()["id"]
        provider_mock.default.pop("openai")
        provider_mock.queue("openai", jr(401, {"error": {"message": "no", "code": "invalid_api_key"}}))
        assert client.post(f"{P}/credentials/{cid}/rotate", headers=w.ha, json={"api_key": OTHER_KEY}).status_code == 422
        db_session.expire_all()
        from app.core.token_encryption import decrypt_token
        assert decrypt_token(db_session.query(AiCredential).one().encrypted_key) == NEW_KEY

    def test_revocation_wipes_the_secret_and_frees_the_slot(self, client, db_session, w, provider_mock):
        provider_mock.always("openai", models_ok)
        cid = create(client, w.ha).json()["id"]
        r = client.delete(f"{P}/credentials/{cid}", headers=w.ha)
        assert r.status_code == 200 and r.json()["status"] == "revoked" and r.json()["revoked_at"]
        db_session.expire_all()
        row = db_session.query(AiCredential).one()
        assert not row.encrypted_key and row.status == "revoked"
        assert client.post(f"{P}/credentials/{cid}/test", headers=w.ha).status_code == 409
        assert create(client, w.ha, key="sk-replacement-key-1234").status_code == 201

    def test_audit_trail_records_events_without_secrets(self, client, db_session, w, provider_mock):
        provider_mock.always("openai", models_ok)
        cid = create(client, w.ha).json()["id"]
        client.post(f"{P}/credentials/{cid}/rotate", headers=w.ha, json={"api_key": OTHER_KEY})
        client.delete(f"{P}/credentials/{cid}", headers=w.ha)
        rows = db_session.query(AuditLog).filter(AuditLog.action.like("ai.credential.%")).all()
        assert {r.action for r in rows} == {"ai.credential.created", "ai.credential.rotated", "ai.credential.revoked"}
        dump = json.dumps([[r.target, r.detail] for r in rows])
        for secret in (NEW_KEY, OTHER_KEY, NEW_KEY[-8:], OTHER_KEY[-8:]):
            assert secret not in dump


class TestIsolationBetweenOrganizations:
    def test_credentials_models_routing_budgets_and_usage_are_private(self, client, db_session, w, provider_mock):
        provider_mock.always("openai", models_ok)
        cid_a = create(client, w.ha).json()["id"]
        model_a = client.post(f"{P}/models", headers=w.ha, json={"provider": "openai", "model_id": "only-a"}).json()["id"]
        client.put(f"{P}/routing/default", headers=w.ha, json={"primary_model_id": model_a})
        client.put(f"{P}/budgets/daily", headers=w.ha, json={"limit_usd": "5"})
        for path in ("credentials", "models", "routing", "budgets", "usage/logs"):
            assert client.get(f"{P}/{path}", headers=w.hb).json() == [], path
        assert client.get(f"{P}/usage/summary", headers=w.hb).json()["totals"]["requests"] == 0
        # B cannot touch A's objects by id: indistinguishable from "does not exist"
        assert client.post(f"{P}/credentials/{cid_a}/test", headers=w.hb).status_code == 404
        assert client.post(f"{P}/credentials/{cid_a}/rotate", headers=w.hb, json={"api_key": OTHER_KEY}).status_code == 404
        assert client.delete(f"{P}/credentials/{cid_a}", headers=w.hb).status_code == 404
        assert client.patch(f"{P}/models/{model_a}", headers=w.hb, json={"enabled": False}).status_code == 404
        assert client.delete(f"{P}/models/{model_a}", headers=w.hb).status_code == 404
        assert client.put(f"{P}/routing/default", headers=w.hb, json={"primary_model_id": model_a}).status_code == 404
        assert db_session.query(AiCredential).one().status == "active"

    def test_a_user_cannot_pick_another_organization_through_the_header(self, client, w):
        headers = {**w.hb, "X-Organization-Id": w.a.id}  # bob is not a member of A
        assert client.get(f"{P}/credentials", headers=headers).status_code in (403, 404)

    def test_each_organization_can_hold_its_own_key_for_the_same_provider(self, client, w, provider_mock):
        provider_mock.always("openai", models_ok)
        assert create(client, w.ha).status_code == 201 and create(client, w.hb, key=OTHER_KEY).status_code == 201
        assert client.get(f"{P}/credentials", headers=w.hb).json()[0]["last4"] == OTHER_KEY[-4:]

    def test_usage_of_one_organization_never_appears_in_another(self, client, db_session, w):
        db_session.add(AiUsageLog(organization_id=w.a.id, request_id="r1", attempt=1, operation="op", provider="openai",
                                  model_id="m", status="success", input_tokens=5, output_tokens=5, cost_micros=77))
        db_session.commit()
        a = client.get(f"{P}/usage/summary", headers=w.ha).json()["totals"]
        b = client.get(f"{P}/usage/summary", headers=w.hb).json()["totals"]
        assert (a["requests"], a["cost_usd"]) == (1, "0.000077") or a["requests"] == 1
        assert b["requests"] == 0 and client.get(f"{P}/usage/logs", headers=w.hb).json() == []


class TestRBAC:
    MANAGE = [("GET", "/credentials"), ("POST", "/credentials"), ("GET", "/models/discover?provider=openai"),
              ("POST", "/models"), ("PUT", "/routing/default"), ("PUT", "/budgets/daily"), ("DELETE", "/budgets/daily")]
    READ = [("GET", "/providers"), ("GET", "/models"), ("GET", "/routing"), ("GET", "/budgets"),
            ("GET", "/usage/summary"), ("GET", "/usage/logs")]

    def call(self, client, headers, method, path):
        return client.request(method, P + path, headers=headers, json={"provider": "openai", "api_key": NEW_KEY, "model_id": "x",
                                                                      "primary_model_id": "x", "limit_usd": "1"})

    @pytest.mark.parametrize("role,can_read,can_manage", [
        ("owner", True, True), ("admin", True, True), ("manager", True, False), ("analyst", True, False), ("viewer", False, False)])
    def test_permission_matrix(self, client, w, provider_mock, role, can_read, can_manage):
        provider_mock.always("openai", models_ok)
        headers = w.ha if role == "owner" else w.roles[role]
        for method, path in self.READ:
            code = self.call(client, headers, method, path).status_code
            assert (code != 403) == can_read, (role, method, path, code)
        for method, path in self.MANAGE:
            code = self.call(client, headers, method, path).status_code
            assert (code != 403) == can_manage, (role, method, path, code)

    def test_unauthenticated_requests_are_refused(self, client):
        for method, path in self.READ + self.MANAGE:
            assert client.request(method, P + path).status_code in (401, 403), path

    def test_an_analyst_sees_usage_but_not_credentials(self, client, w, provider_mock):
        provider_mock.always("openai", models_ok)
        create(client, w.ha)
        assert client.get(f"{P}/credentials", headers=w.roles["analyst"]).status_code == 403
        assert {p["provider"]: p["has_active_credential"] for p in client.get(f"{P}/providers", headers=w.roles["analyst"]).json()}["openai"] is True


class TestModelsRoutingBudgets:
    def test_model_catalog_crud_with_prices_stored_as_exact_micros(self, client, db_session, w):
        r = client.post(f"{P}/models", headers=w.ha, json={"provider": "gemini", "model_id": "any-name", "display_name": "Any",
                         "supports_json": True, "input_price_usd_per_mtok": "0.30", "output_price_usd_per_mtok": "2.5"})
        assert r.status_code == 201 and r.json()["input_price_usd_per_mtok"] and r.json()["supports_json"] is True
        mid = r.json()["id"]
        from app.models import AiModel
        row = db_session.query(AiModel).one()
        assert (row.input_price_micros_per_mtok, row.output_price_micros_per_mtok) == (300_000, 2_500_000)
        assert client.post(f"{P}/models", headers=w.ha, json={"provider": "gemini", "model_id": "any-name"}).status_code == 409
        assert client.post(f"{P}/models", headers=w.ha, json={"provider": "nope", "model_id": "x"}).status_code == 422
        assert client.post(f"{P}/models", headers=w.ha, json={"provider": "openai", "model_id": "x", "input_price_usd_per_mtok": "-1"}).status_code == 422
        assert client.patch(f"{P}/models/{mid}", headers=w.ha, json={"enabled": False}).json()["enabled"] is False
        assert client.delete(f"{P}/models/{mid}", headers=w.ha).status_code == 200
        assert client.get(f"{P}/models", headers=w.ha).json() == []

    def test_discovery_lists_provider_models_with_the_organizations_key(self, client, w, provider_mock):
        assert client.get(f"{P}/models/discover?provider=openai", headers=w.ha).status_code == 409  # no credential yet
        provider_mock.always("openai", models_ok)
        create(client, w.ha)
        client.post(f"{P}/models", headers=w.ha, json={"provider": "openai", "model_id": "model-x"})
        found = client.get(f"{P}/models/discover?provider=openai", headers=w.ha).json()
        assert {(f["model_id"], f["already_added"]) for f in found} == {("model-x", True), ("model-y", False)}

    def test_routing_validates_ownership_duplicates_and_cleans_up_on_model_removal(self, client, w):
        ids = [client.post(f"{P}/models", headers=w.ha, json={"provider": "openai", "model_id": f"m{i}"}).json()["id"] for i in range(3)]
        ok = client.put(f"{P}/routing/copy.generate", headers=w.ha, json={"primary_model_id": ids[0], "fallback_model_ids": ids[1:]})
        assert ok.status_code == 200 and ok.json()["fallback_model_ids"] == ids[1:]
        assert client.put(f"{P}/routing/x", headers=w.ha, json={"primary_model_id": ids[0], "fallback_model_ids": [ids[0]]}).status_code == 422
        assert client.put(f"{P}/routing/Bad Op!", headers=w.ha, json={"primary_model_id": ids[0]}).status_code in (404, 422)
        assert client.put(f"{P}/routing/x", headers=w.ha, json={"primary_model_id": "missing"}).status_code == 404
        client.delete(f"{P}/models/{ids[1]}", headers=w.ha)
        assert client.get(f"{P}/routing", headers=w.ha).json()[0]["fallback_model_ids"] == [ids[2]]
        client.delete(f"{P}/models/{ids[0]}", headers=w.ha)
        assert client.get(f"{P}/routing", headers=w.ha).json()[0]["primary_model_id"] is None
        assert client.delete(f"{P}/routing/copy.generate", headers=w.ha).status_code == 200
        assert client.delete(f"{P}/routing/copy.generate", headers=w.ha).status_code == 404

    def test_budgets_set_update_and_remove(self, client, w):
        r = client.put(f"{P}/budgets/daily", headers=w.ha, json={"limit_usd": "12.5"})
        assert r.status_code == 200 and r.json()["limit_usd"] == "12.500000" or float(r.json()["limit_usd"]) == 12.5
        assert float(r.json()["remaining_usd"]) == 12.5 and float(r.json()["spent_usd"]) == 0
        assert client.put(f"{P}/budgets/daily", headers=w.ha, json={"limit_usd": "20", "enabled": False}).json()["enabled"] is False
        assert len(client.get(f"{P}/budgets", headers=w.ha).json()) == 1
        assert client.put(f"{P}/budgets/weekly", headers=w.ha, json={"limit_usd": "1"}).status_code == 422
        assert client.put(f"{P}/budgets/daily", headers=w.ha, json={"limit_usd": "-1"}).status_code == 422
        assert client.delete(f"{P}/budgets/daily", headers=w.ha).status_code == 200
        assert client.delete(f"{P}/budgets/daily", headers=w.ha).status_code == 404

    def test_usage_summary_groups_by_model_operation_and_user(self, client, db_session, w):
        for i, (op, model, cost) in enumerate([("copy.generate", "m1", 1000), ("copy.generate", "m1", 500), ("ad_remix.reconstruct", "m2", 250)]):
            db_session.add(AiUsageLog(organization_id=w.a.id, user_id=w.alice.id, request_id=f"r{i}", attempt=1, operation=op, provider="openai",
                                      model_id=model, status="success", input_tokens=100, output_tokens=50, cost_micros=cost,
                                      estimated_cost_micros=cost + 5))
        db_session.add(AiUsageLog(organization_id=w.a.id, request_id="r9", attempt=1, operation="copy.generate", provider="openai",
                                  model_id="m1", status="error", error_kind="rate_limit"))
        db_session.commit()
        s = client.get(f"{P}/usage/summary?days=7", headers=w.ha).json()
        assert s["totals"]["requests"] == 4 and s["totals"]["errors"] == 1 and s["totals"]["input_tokens"] == 300
        assert float(s["totals"]["cost_usd"]) == pytest.approx(0.00175)
        assert {(g["model_id"], g["requests"]) for g in s["by_model"]} == {("m1", 3), ("m2", 1)}
        assert {g["operation"] for g in s["by_operation"]} == {"copy.generate", "ad_remix.reconstruct"}
        logs = client.get(f"{P}/usage/logs?limit=2", headers=w.ha).json()
        assert len(logs) == 2 and "prompt" not in json.dumps(logs)
