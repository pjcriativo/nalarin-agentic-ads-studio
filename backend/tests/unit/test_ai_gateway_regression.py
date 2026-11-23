"""Regression: the Gemini-based features (copy generation, ad remix) now run ONLY through the
organization's AI Gateway: its own credential, model, budget and usage log. Provider answers are
scripted (Gemini wire format): nothing reaches a real provider."""
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.ai.types import ImagePart
from app.models import AiBudget, AiUsageLog, Brand, CustomerProfile, Product, WinningAd
from tests.unit.ai_support import KEYS, gemini_ok, jr, seed_credential, seed_model

COPY = "/api/v1/copy-generation"
REMIX = "/api/v1/ad-remix"
PRICE = dict(input_price_micros_per_mtok=300_000, output_price_micros_per_mtok=2_500_000)
BLUEPRINT = {"layout_framework": "split", "narrative_arc": "problem -> solution", "text_hierarchy": "headline, cta",
             "psychological_triggers": ["urgency"], "visual_style_guide": "minimal"}
CONCEPT = {"headline_remix": "h", "visual_description": "v", "body_copy": "b", "cta_button": "c", "image_generation_prompt": "p"}
GENERATE = {"brand": {"voice": "warm"}, "product": {"name": "Widget"}, "profile": {"demographics": "all"},
            "campaignDetails": {"offer": "50% off", "messaging": "now"}, "variationCount": 2}


@pytest.fixture
def w(make_user, make_org, add_member, login_headers, db_session, provider_mock):
    alice, bob = make_user("alice-reg@example.com"), make_user("bob-reg@example.com")
    a, b = make_org("REG A", owner=alice), make_org("REG B", owner=bob)
    viewer = make_user("viewer-reg@example.com")
    add_member(a, viewer, role="viewer")
    seed_credential(db_session, a.id, "gemini")
    seed_model(db_session, a.id, "gemini", "test-gemini-model", supports_vision=True, supports_json=True, **PRICE)
    base = COPY
    return SimpleNamespace(a=a, b=b, ha=login_headers(alice, a), hb=login_headers(bob, b), hv=login_headers(viewer, a),
                           base=base, alice=alice)


def usage(db_session, org):
    db_session.expire_all()
    return db_session.query(AiUsageLog).filter(AiUsageLog.organization_id == org.id).order_by(AiUsageLog.created_at).all()


class TestCopyGeneration:
    def test_generate_uses_the_organizations_gemini_credential_and_logs_usage(self, client, db_session, w, provider_mock):
        variations = {"variations": [{"headline": "H", "body": "B", "cta": "C"}]}
        provider_mock.queue("gemini", jr(200, gemini_ok(json.dumps(variations), inp=400, out=100)))
        r = client.post(f"{w.base}/generate", headers=w.ha, json=GENERATE)
        assert r.status_code == 200, r.text
        assert r.json() == variations
        request = provider_mock.of("gemini")[0]
        assert KEYS["gemini"] in {request.headers.get("x-goog-api-key"), request.headers.get("authorization", "").replace("Bearer ", "")}
        assert KEYS["gemini"] not in str(request.url)
        assert "test-gemini-model" in str(request.url)
        rows = usage(db_session, w.a)
        assert [(x.operation, x.status, x.user_id, x.input_tokens, x.output_tokens) for x in rows] == [
            ("copy.generate", "success", w.alice.id, 400, 100)]
        assert rows[0].cost_micros == 120 + 250  # 400 in @ $0.30/M + 100 out @ $2.5/M, rounded up

    def test_markdown_fences_and_surrounding_prose_are_tolerated(self, client, w, provider_mock):
        inner = '{"variations": [{"headline": "H", "body": "B", "cta": "C"}]}'
        provider_mock.queue("gemini", jr(200, gemini_ok(f"Here you go:\n```json\n{inner}\n```")))
        assert client.post(f"{w.base}/generate", headers=w.ha, json=GENERATE).json()["variations"][0]["headline"] == "H"

    def test_a_non_json_answer_keeps_the_legacy_500_message(self, client, w, provider_mock):
        provider_mock.queue("gemini", jr(200, gemini_ok("sorry, no json here")))
        r = client.post(f"{w.base}/generate", headers=w.ha, json=GENERATE)
        assert r.status_code == 500 and r.json()["detail"] == "Failed to parse AI response as JSON"

    def test_regenerate_field_returns_the_trimmed_text(self, client, db_session, w, provider_mock):
        provider_mock.queue("gemini", jr(200, gemini_ok('  "Fresh headline"  ')))
        r = client.post(f"{w.base}/regenerate-field", headers=w.ha, json={
            "field": "headline", "currentValue": "old", "brand": {}, "product": {"name": "W"}, "profile": {},
            "campaignDetails": {"offer": "x"}})
        assert r.status_code == 200 and r.json() == {"newValue": "Fresh headline"}
        assert usage(db_session, w.a)[0].operation == "copy.regenerate_field"

    def test_organization_without_a_credential_gets_a_clear_conflict(self, client, w, provider_mock):
        r = client.post(f"{w.base}/generate", headers=w.hb, json=GENERATE)
        assert r.status_code == 409 and provider_mock.requests == []

    def test_the_global_environment_key_is_ignored(self, client, w, monkeypatch, provider_mock):
        monkeypatch.setenv("GEMINI_API_KEY", "AIza-global-env-key-should-never-be-used")
        provider_mock.queue("gemini", jr(200, gemini_ok('{"variations": []}')))
        client.post(f"{w.base}/generate", headers=w.ha, json=GENERATE)
        assert "global-env-key" not in json.dumps([dict(r.headers) | {"u": str(r.url)} for r in provider_mock.requests])
        # and an org WITHOUT its own credential does not fall back to it
        assert client.post(f"{w.base}/generate", headers=w.hb, json=GENERATE).status_code == 409

    @pytest.mark.parametrize("status,body,expected", [
        (429, {"error": {"code": 429, "message": "quota KEY", "status": "RESOURCE_EXHAUSTED"}}, 429),
        (401, {"error": {"code": 401, "message": "API key not valid KEY", "status": "UNAUTHENTICATED"}}, None),
        (503, {"error": {"code": 503, "message": "overloaded", "status": "UNAVAILABLE"}}, 503),
    ])
    def test_provider_failures_become_safe_http_errors(self, client, w, provider_mock, status, body, expected):
        provider_mock.queue("gemini", jr(status, body))
        r = client.post(f"{w.base}/generate", headers=w.ha, json=GENERATE)
        assert r.status_code >= 400 and (expected is None or r.status_code == expected)
        assert KEYS["gemini"] not in r.text and "KEY" not in r.text and "RESOURCE_EXHAUSTED" not in r.text

    def test_permissions_are_unchanged(self, client, w, provider_mock):
        assert client.post(f"{w.base}/generate", headers=w.hv, json=GENERATE).status_code == 403
        assert client.post(f"{w.base}/generate", json=GENERATE).status_code in (401, 403)
        assert provider_mock.requests == []

    def test_an_exhausted_budget_blocks_the_feature_with_429_before_calling_the_provider(self, client, db_session, w, provider_mock):
        from app.ai.budget import window_start
        db_session.add(AiBudget(organization_id=w.a.id, period="daily", limit_micros=1, window_start=window_start("daily")))
        db_session.commit()
        r = client.post(f"{w.base}/generate", headers=w.ha, json=GENERATE)
        assert r.status_code == 429 and provider_mock.requests == []

    def test_fallback_model_serves_the_feature_after_a_recoverable_error(self, client, db_session, w, provider_mock):
        from app.models import AiRouting
        primary = db_session.query(__import__("app.models", fromlist=["AiModel"]).AiModel).one()
        second = seed_model(db_session, w.a.id, "gemini", "test-gemini-fallback", **PRICE)
        db_session.add(AiRouting(organization_id=w.a.id, operation="default", primary_model_id=primary.id, fallback_model_ids=[second.id]))
        db_session.commit()
        provider_mock.queue("gemini", jr(503, {"error": {"code": 503, "message": "x", "status": "UNAVAILABLE"}}),
                            jr(200, gemini_ok('{"variations": []}')))
        assert client.post(f"{w.base}/generate", headers=w.ha, json=GENERATE).status_code == 200
        assert [str(r.url).count("test-gemini-fallback") for r in provider_mock.of("gemini")] == [0, 1]
        assert [x.status for x in usage(db_session, w.a)] == ["error", "success"]


class TestAdRemix:
    @pytest.fixture
    def data(self, db_session, w, monkeypatch):
        template = WinningAd(organization_id=w.a.id, name="T", image_url="/uploads/whatever.png")
        brand = Brand(organization_id=w.a.id, name="B", primary_color="#000", secondary_color="#111", highlight_color="#222")
        db_session.add_all([template, brand])
        db_session.flush()
        product = Product(organization_id=w.a.id, brand_id=brand.id, name="P", description="d")
        profile = CustomerProfile(organization_id=w.a.id, name="Pr", demographics="x")
        db_session.add_all([product, profile])
        db_session.commit()

        async def fake_loader(reference, org_id):
            assert org_id == w.a.id
            return ImagePart(mime_type="image/png", data=b"\x89PNG\r\n\x1a\n-fake")
        monkeypatch.setattr("app.api.v1.ad_remix.load_template_image", fake_loader)
        return SimpleNamespace(template=template, brand=brand, product=product, profile=profile)

    def test_deconstruct_sends_the_image_through_the_gateway_and_stores_the_blueprint(self, client, db_session, w, data, provider_mock):
        provider_mock.queue("gemini", jr(200, gemini_ok(json.dumps(BLUEPRINT), inp=900, out=120)))
        r = client.post(f"{REMIX}/deconstruct", headers=w.ha, json={"template_id": data.template.id})
        assert r.status_code == 200, r.text
        assert r.json()["layout_framework"] == "split"
        body = provider_mock.body(provider_mock.of("gemini")[0])
        parts = body["contents"][0]["parts"]
        assert any("inlineData" in p or "inline_data" in p for p in parts)  # vision input reached the provider
        db_session.expire_all()
        assert db_session.query(WinningAd).one().blueprint_json["narrative_arc"] == "problem -> solution"
        assert usage(db_session, w.a)[0].operation == "ad_remix.deconstruct"

    def test_deconstruct_needs_a_vision_capable_model(self, client, db_session, w, data, provider_mock):
        from app.models import AiModel
        db_session.query(AiModel).update({"supports_vision": False})
        db_session.commit()
        r = client.post(f"{REMIX}/deconstruct", headers=w.ha, json={"template_id": data.template.id})
        assert r.status_code == 409 and provider_mock.requests == []

    def test_an_unusable_blueprint_keeps_the_legacy_500_message(self, client, w, data, provider_mock):
        provider_mock.queue("gemini", jr(200, gemini_ok('{"layout_framework": "only this"}')))
        r = client.post(f"{REMIX}/deconstruct", headers=w.ha, json={"template_id": data.template.id})
        assert r.status_code == 500 and r.json()["detail"] == "Deconstruction failed"

    def test_reconstruct_returns_a_concept_and_logs_usage(self, client, db_session, w, data, provider_mock):
        data.template.blueprint_json = BLUEPRINT
        db_session.commit()
        provider_mock.queue("gemini", jr(200, gemini_ok(json.dumps(CONCEPT))))
        r = client.post(f"{REMIX}/reconstruct", headers=w.ha, json={
            "template_id": data.template.id, "brand_id": data.brand.id, "product_id": data.product.id,
            "profile_id": data.profile.id, "campaign_offer": "o", "campaign_messaging": "m"})
        assert r.status_code == 200, r.text
        assert r.json()["headline_remix"] == "h" and usage(db_session, w.a)[0].operation == "ad_remix.reconstruct"

    def test_another_organization_cannot_run_it_on_these_templates(self, client, w, data, provider_mock):
        assert client.post(f"{REMIX}/deconstruct", headers=w.hb, json={"template_id": data.template.id}).status_code == 404
        assert provider_mock.requests == []


class TestNoGlobalAIConfiguration:
    ROOT = Path(__file__).resolve().parents[2] / "app"

    def test_no_module_configures_gemini_globally_or_imports_a_provider_sdk(self):
        offenders = []
        for path in self.ROOT.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if re.search(r"google\.generativeai|import\s+genai|genai\.configure|GenerativeModel|settings\.GEMINI_API_KEY", text):
                if "ai_gateway" not in path.name:
                    offenders.append(str(path.relative_to(self.ROOT)))
        assert offenders == []

    def test_there_is_no_global_ai_key_setting(self):
        from app.core.config import settings
        assert not hasattr(settings, "GEMINI_API_KEY")

    def test_model_names_are_data_not_code(self):
        pattern = re.compile(r"(claude-(?:opus|sonnet|haiku|fable)|gpt-[0-9o]|\bo[1-9]-|gemini-[0-9])", re.I)
        files = [*(self.ROOT / "ai").rglob("*.py"), self.ROOT / "api/v1/ai.py", self.ROOT / "api/v1/copy_generation.py",
                 self.ROOT / "services/ad_remix_service.py"]
        hits = [f"{f.name}: {m.group(0)}" for f in files for m in pattern.finditer(f.read_text(encoding="utf-8"))]
        assert hits == []
