"""Etapa 01.2: isolation of the remaining business data between organizations.

Organizations A and B with users of different roles, exercised through the real
HTTP API and the real runtime role (RLS in force). ``db_session`` is the
superuser seeding connection used to arrange and inspect data.
"""
import io
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models import (
    AuditLog, Brand, FacebookCampaign, GeneratedAd, KeywordBlacklist, Prompt, SavedSearch,
    Vertical, WinningAd,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def tenants(make_user, make_org, login_headers):
    alice = make_user("alice@example.com")
    bob = make_user("bob@example.com")
    org_a = make_org("Org A", owner=alice)
    org_b = make_org("Org B", owner=bob)
    return SimpleNamespace(alice=alice, bob=bob, org_a=org_a, org_b=org_b,
                           ha=login_headers(alice), hb=login_headers(bob))


@pytest.fixture
def seed(db_session):
    """Seed rows straight into an organization (superuser connection)."""
    def brand(org, name="B"):
        row = Brand(id=str(uuid.uuid4()), organization_id=org.id, name=name)
        db_session.add(row)
        db_session.commit()
        return row

    def template(org, name="T"):
        row = WinningAd(id=str(uuid.uuid4()), organization_id=org.id, name=name, image_url="http://x/y.png")
        db_session.add(row)
        db_session.commit()
        return row
    return SimpleNamespace(brand=brand, template=template)


def ad_payload(**extra):
    return {"id": str(uuid.uuid4()), "imageUrl": "https://example.com/i.png", "headline": "H",
            "body": "B", "cta": "SHOP_NOW", **extra}


def batch(client, headers, *ads):
    return client.post("/api/v1/generated-ads/batch", headers=headers, json={"ads": list(ads)})


# --------------------------------------------------------------------------
# Generated ads (creatives): CRUD, listing, export, batch, cross references
# --------------------------------------------------------------------------
class TestGeneratedAds:
    def test_ads_are_private_to_their_organization(self, client, tenants):
        ad = ad_payload()
        assert batch(client, tenants.ha, ad).status_code == 200
        assert [a["id"] for a in client.get("/api/v1/generated-ads/", headers=tenants.ha).json()] == [ad["id"]]
        assert client.get("/api/v1/generated-ads/", headers=tenants.hb).json() == []

    def test_idor_delete_and_export_of_another_tenants_ad(self, client, tenants, db_session):
        ad = ad_payload()
        batch(client, tenants.ha, ad)
        assert client.delete(f"/api/v1/generated-ads/{ad['id']}", headers=tenants.hb).status_code == 404
        exported = client.post("/api/v1/generated-ads/export-csv", headers=tenants.hb, json={"ids": [ad["id"]]})
        assert exported.status_code == 200
        assert ad["id"] not in exported.text  # header only: nothing of A leaks in B's export
        assert db_session.query(GeneratedAd).filter(GeneratedAd.id == ad["id"]).count() == 1

    def test_same_client_id_in_two_organizations_never_overwrites_or_leaks(self, client, tenants, db_session):
        ad = ad_payload()
        assert batch(client, tenants.ha, ad).status_code == 200
        response = batch(client, tenants.hb, {**ad, "headline": "hijack"})
        assert response.status_code in (200, 409)
        assert db_session.query(GeneratedAd).filter(GeneratedAd.id == ad["id"]).one().headline == "H"

    def test_batch_cannot_reference_another_tenants_brand_or_template(self, client, tenants, seed):
        foreign_brand = seed.brand(tenants.org_b)
        foreign_template = seed.template(tenants.org_b)
        assert batch(client, tenants.ha, ad_payload(brandId=foreign_brand.id)).status_code == 404
        assert batch(client, tenants.ha, ad_payload(templateId=foreign_template.id)).status_code == 404
        assert client.get("/api/v1/generated-ads/", headers=tenants.ha).json() == []

    def test_database_rejects_cross_organization_references_even_without_the_api(self, tenants, seed, db_session):
        foreign_brand = seed.brand(tenants.org_b)
        db_session.add(GeneratedAd(id=str(uuid.uuid4()), organization_id=tenants.org_a.id,
                                   brand_id=foreign_brand.id, headline="x"))
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_own_brand_reference_is_accepted(self, client, tenants, seed):
        own = seed.brand(tenants.org_a)
        assert batch(client, tenants.ha, ad_payload(brandId=own.id)).status_code == 200
        assert client.get(f"/api/v1/generated-ads/?brand_id={own.id}", headers=tenants.ha).json()[0]["brand_id"] == own.id
        assert client.get(f"/api/v1/generated-ads/?brand_id={own.id}", headers=tenants.hb).json() == []

    def test_deleting_a_brand_keeps_the_ad_and_its_organization(self, client, tenants, seed, db_session):
        own = seed.brand(tenants.org_a)
        ad = ad_payload(brandId=own.id)
        batch(client, tenants.ha, ad)
        assert client.delete(f"/api/v1/brands/{own.id}", headers=tenants.ha).status_code == 200
        row = db_session.query(GeneratedAd).filter(GeneratedAd.id == ad["id"]).one()
        assert row.brand_id is None and row.organization_id == tenants.org_a.id

    def test_media_of_another_tenant_cannot_be_attached(self, client, tenants):
        foreign = f"/uploads/{tenants.org_b.id}/{'a' * 32}_{'b' * 16}.png"
        assert batch(client, tenants.ha, ad_payload(imageUrl=foreign)).status_code == 400

    def test_csv_export_neutralizes_formula_injection(self, client, tenants):
        ad = ad_payload(headline="=HYPERLINK(\"http://evil\")")
        batch(client, tenants.ha, ad)
        text_ = client.post("/api/v1/generated-ads/export-csv", headers=tenants.ha, json={"ids": [ad["id"]]}).text
        assert "'=HYPERLINK" in text_

    def test_export_is_bounded(self, client, tenants):
        response = client.post("/api/v1/generated-ads/export-csv", headers=tenants.ha,
                               json={"ids": [str(i) for i in range(1001)]})
        assert response.status_code == 400

    def test_without_context_nothing_is_reachable(self, client, make_user, login_headers):
        loner = make_user("loner2@example.com")
        assert client.get("/api/v1/generated-ads/", headers=login_headers(loner)).status_code == 403
        assert client.get("/api/v1/generated-ads/").status_code == 401


# --------------------------------------------------------------------------
# Templates and uploads
# --------------------------------------------------------------------------
class TestTemplatesAndUploads:
    def test_templates_are_private(self, client, tenants, seed):
        seed.template(tenants.org_a, "mine")
        theirs = seed.template(tenants.org_b, "theirs")
        assert [t["name"] for t in client.get("/api/v1/templates/", headers=tenants.ha).json()] == ["mine"]
        assert client.get(f"/api/v1/templates/{theirs.id}/preview", headers=tenants.ha).status_code == 404
        assert client.get("/api/v1/templates/filters", headers=tenants.hb).status_code == 200

    def test_template_search_does_not_cross_tenants(self, client, tenants, seed):
        seed.template(tenants.org_b, "secret-campaign-template")
        assert client.get("/api/v1/templates/?search=secret", headers=tenants.ha).json() == []
        assert len(client.get("/api/v1/templates/?search=secret", headers=tenants.hb).json()) == 1

    def test_ad_remix_blueprints_are_scoped(self, client, tenants, seed, db_session):
        template = seed.template(tenants.org_b)
        template.blueprint_json = {"x": 1}
        db_session.commit()
        assert client.get(f"/api/v1/ad-remix/blueprints/{template.id}", headers=tenants.ha).status_code == 404
        assert client.get("/api/v1/ad-remix/blueprints", headers=tenants.ha).json() == []
        assert client.post("/api/v1/ad-remix/deconstruct", headers=tenants.ha,
                           json={"template_id": template.id}).status_code == 404

    def test_upload_lands_in_the_organization_namespace_and_is_served(self, client, tenants):
        response = client.post("/api/v1/uploads/", headers=tenants.ha,
                               files={"file": ("a.png", io.BytesIO(PNG), "image/png")})
        assert response.status_code == 200, response.text
        url = response.json()["url"]
        assert url.startswith(f"/uploads/{tenants.org_a.id}/") and url.endswith(".png")
        assert client.get(url).status_code == 200

    def test_a_file_is_not_reachable_through_another_organizations_path(self, client, tenants):
        url = client.post("/api/v1/uploads/", headers=tenants.ha,
                          files={"file": ("a.png", io.BytesIO(PNG), "image/png")}).json()["url"]
        name = url.rsplit("/", 1)[-1]
        assert client.get(f"/uploads/{tenants.org_b.id}/{name}").status_code == 404

    def test_forged_unsigned_traversal_and_legacy_urls_are_404(self, client, tenants):
        org = tenants.org_a.id
        for path in (f"/uploads/{org}/{'a' * 32}_{'0' * 16}.png", f"/uploads/{org}/..%2Fsecret.png",
                     f"/uploads/{org}/plain.png", "/uploads/legacy-flat-file.png", "/uploads/..%2F..%2Fx"):
            assert client.get(path).status_code == 404, path

    def test_viewer_cannot_upload(self, client, tenants, make_user, add_member, login_headers):
        viewer = make_user("viewer-up@example.com")
        add_member(tenants.org_a, viewer, role="viewer")
        response = client.post("/api/v1/uploads/", headers=login_headers(viewer, tenants.org_a),
                               files={"file": ("a.png", io.BytesIO(PNG), "image/png")})
        assert response.status_code == 403

    def test_template_upload_validates_content_and_is_private(self, client, tenants):
        bad = client.post("/api/v1/templates/upload", headers=tenants.ha,
                          files=[("images", ("x.png", io.BytesIO(b"not an image at all"), "image/png"))])
        assert bad.status_code == 400
        ok = client.post("/api/v1/templates/upload", headers=tenants.ha,
                         files=[("images", ("x.png", io.BytesIO(PNG), "image/png"))])
        assert ok.status_code == 200, ok.text
        assert client.get("/api/v1/templates/", headers=tenants.hb).json() == []


# --------------------------------------------------------------------------
# Research: searches, verticals, blacklists, logs, brand scrapes
# --------------------------------------------------------------------------
class TestResearch:
    def test_verticals_and_blacklists_are_per_organization(self, client, tenants):
        for headers in (tenants.ha, tenants.hb):  # the SAME name is allowed in both tenants
            assert client.post("/api/v1/research/verticals?name=Legal", headers=headers).status_code == 200
            assert client.post("/api/v1/research/blacklist?page_name=spam", headers=headers).status_code == 200
            assert client.post("/api/v1/research/keyword-blacklist?keyword=Free", headers=headers).status_code == 200
        assert len(client.get("/api/v1/research/verticals", headers=tenants.ha).json()) == 1
        assert client.post("/api/v1/research/verticals?name=Legal", headers=tenants.ha).status_code == 400

    def test_a_tenant_cannot_remove_or_see_the_others_blacklist(self, client, tenants):
        created = client.post("/api/v1/research/blacklist?page_name=only-a", headers=tenants.ha).json()
        assert client.get("/api/v1/research/blacklist", headers=tenants.hb).json() == []
        assert client.delete(f"/api/v1/research/blacklist/{created['id']}", headers=tenants.hb).status_code == 404
        assert len(client.get("/api/v1/research/blacklist", headers=tenants.ha).json()) == 1
        kw = client.post("/api/v1/research/keyword-blacklist?keyword=zzz", headers=tenants.ha).json()
        assert client.delete(f"/api/v1/research/keyword-blacklist/{kw['id']}", headers=tenants.hb).status_code == 404

    def test_saved_searches_and_aggregates_are_scoped(self, client, tenants, db_session):
        vertical = Vertical(id=str(uuid.uuid4()), organization_id=tenants.org_b.id, name="Fitness")
        db_session.add(vertical)
        db_session.flush()
        search = SavedSearch(id=str(uuid.uuid4()), organization_id=tenants.org_b.id, query="gym", vertical_id=vertical.id)
        db_session.add(search)
        db_session.commit()
        assert client.get("/api/v1/research/saved-searches", headers=tenants.ha).json() == []
        assert client.get(f"/api/v1/research/saved-searches/{search.id}", headers=tenants.ha).status_code == 404
        assert client.delete(f"/api/v1/research/saved-searches/{search.id}", headers=tenants.ha).status_code == 404
        assert client.get(f"/api/v1/research/verticals/{vertical.id}/aggregated-ads", headers=tenants.ha).json() == []
        assert len(client.get("/api/v1/research/saved-searches", headers=tenants.hb).json()) == 1

    def test_usage_logs_and_rate_limit_are_per_organization(self, client, tenants, db_session):
        db_session.execute(text(
            "INSERT INTO search_logs (id, organization_id, search_query, api_calls_made, date, created_at) "
            "VALUES (:i, :o, 'q', 150, '2026-01-01', now())"), {"i": str(uuid.uuid4()), "o": tenants.org_b.id})
        db_session.execute(text(
            "INSERT INTO api_usage_logs (id, organization_id, endpoint, api_calls, ads_returned, ads_saved, date) "
            "VALUES (:i, :o, 'e', 9, 9, 9, '2026-01-01')"), {"i": str(uuid.uuid4()), "o": tenants.org_b.id})
        db_session.commit()
        assert client.get("/api/v1/research/rate-limit", headers=tenants.ha).json()["used"] == 0
        assert client.get("/api/v1/research/rate-limit", headers=tenants.hb).json()["used"] == 150
        assert client.get("/api/v1/research/api-usage", headers=tenants.ha).json() == []
        assert len(client.get("/api/v1/research/api-usage", headers=tenants.hb).json()) == 1

    def test_brand_scrapes_are_scoped(self, client, tenants, db_session):
        row = db_session.execute(text(
            "INSERT INTO brand_scrapes (id, organization_id, brand_name, page_id, page_url, status) "
            "VALUES (:i, :o, 'B', '1', 'http://u', 'completed') RETURNING id"),
            {"i": str(uuid.uuid4()), "o": tenants.org_b.id}).scalar()
        db_session.commit()
        assert client.get("/api/v1/research/brand-scrapes", headers=tenants.ha).json() == []
        assert client.get(f"/api/v1/research/brand-scrapes/{row}", headers=tenants.ha).status_code == 404
        assert client.delete(f"/api/v1/research/brand-scrapes/{row}", headers=tenants.ha).status_code == 404

    def test_research_service_never_dedupes_against_another_tenants_ads(self, tenants, db_session):
        from app.database import SessionLocal, bind_context
        from app.models import ScrapedAd
        for org in (tenants.org_a, tenants.org_b):
            db = SessionLocal()
            try:
                bind_context(db, org.id)
                db.add(ScrapedAd(ad_link="http://l", content_hash="same-hash", external_id="same-ext"))
                db.commit()  # would collide if uniqueness were still global
            finally:
                db.close()
        assert db_session.query(ScrapedAd).filter(ScrapedAd.content_hash == "same-hash").count() == 2

    def test_viewer_reads_but_cannot_write_research(self, client, tenants, make_user, add_member, login_headers):
        viewer = make_user("viewer-r@example.com")
        add_member(tenants.org_a, viewer, role="viewer")
        h = login_headers(viewer, tenants.org_a)
        assert client.get("/api/v1/research/verticals", headers=h).status_code == 200
        assert client.post("/api/v1/research/verticals?name=x", headers=h).status_code == 403
        assert client.post("/api/v1/research/blacklist?page_name=x", headers=h).status_code == 403

    def test_analyst_can_research_but_not_delete(self, client, tenants, make_user, add_member, login_headers):
        analyst = make_user("analyst-r@example.com")
        add_member(tenants.org_a, analyst, role="analyst")
        h = login_headers(analyst, tenants.org_a)
        created = client.post("/api/v1/research/blacklist?page_name=x", headers=h)
        assert created.status_code == 200
        assert client.delete(f"/api/v1/research/blacklist/{created.json()['id']}", headers=h).status_code == 403


# --------------------------------------------------------------------------
# Campaigns (local records), dashboard and reports
# --------------------------------------------------------------------------
class TestCampaignsAndReports:
    def _campaign(self, client, headers, cid="camp_abc"):
        return client.post("/api/v1/facebook/campaigns/save", headers=headers,
                           json={"id": cid, "name": "C", "objective": "OUTCOME_SALES", "budgetType": "ABO"})

    def test_campaign_adset_ad_chain_is_private(self, client, tenants, db_session):
        assert self._campaign(client, tenants.ha).status_code == 200
        adset = client.post("/api/v1/facebook/adsets/save", headers=tenants.ha, json={
            "id": "adset_abc", "campaignId": "camp_abc", "name": "S", "optimizationGoal": "LINK_CLICKS"})
        assert adset.status_code == 200, adset.text
        ad = client.post("/api/v1/facebook/ads/save", headers=tenants.ha, json={
            "id": "ad_abc", "adsetId": "adset_abc", "name": "A", "imageUrl": "https://example.com/i.png"})
        assert ad.status_code == 200, ad.text
        # B cannot hang anything under A's campaign / adset (404, not even an existence oracle)
        assert client.post("/api/v1/facebook/adsets/save", headers=tenants.hb, json={
            "id": "adset_evil", "campaignId": "camp_abc", "name": "S", "optimizationGoal": "X"}).status_code == 404
        assert client.post("/api/v1/facebook/ads/save", headers=tenants.hb, json={
            "id": "ad_evil", "adsetId": "adset_abc", "name": "A"}).status_code == 404
        assert db_session.execute(text("SELECT count(*) FROM facebook_ads WHERE id = 'ad_evil'")).scalar() == 0

    def test_campaign_id_collision_does_not_expose_or_modify_the_other_tenant(self, client, tenants, db_session):
        assert self._campaign(client, tenants.ha).status_code == 200
        response = self._campaign(client, tenants.hb)
        assert response.status_code in (200, 409, 500)
        assert db_session.query(FacebookCampaign).filter(FacebookCampaign.id == "camp_abc").one().organization_id == tenants.org_a.id

    def test_database_rejects_adset_under_another_tenants_campaign(self, tenants, db_session):
        db_session.add(FacebookCampaign(id="camp_x", organization_id=tenants.org_b.id, name="c",
                                        objective="o", budget_type="ABO"))
        db_session.commit()
        with pytest.raises(IntegrityError):
            db_session.execute(text(
                "INSERT INTO facebook_adsets (id, organization_id, campaign_id, name, optimization_goal) "
                "VALUES ('as_x', :o, 'camp_x', 'n', 'g')"), {"o": tenants.org_a.id})
            db_session.commit()
        db_session.rollback()

    def test_dashboard_counts_only_the_callers_organization(self, client, tenants, seed):
        seed.brand(tenants.org_a)
        seed.brand(tenants.org_b)
        seed.brand(tenants.org_b)
        seed.template(tenants.org_b)
        batch(client, tenants.hb, ad_payload())
        self._campaign(client, tenants.hb, "camp_b")
        a = client.get("/api/v1/dashboard/stats", headers=tenants.ha).json()
        b = client.get("/api/v1/dashboard/stats", headers=tenants.hb).json()
        assert a == {"brands_count": 1, "products_count": 0, "generated_ads_count": 0,
                     "templates_count": 0, "campaigns_count": 0}
        assert (b["brands_count"], b["generated_ads_count"], b["templates_count"], b["campaigns_count"]) == (2, 1, 1, 1)

    def test_overview_needs_reports_permission_and_never_uses_the_server_token(
            self, client, tenants, make_user, add_member, login_headers, monkeypatch):
        from app.core.config import settings
        monkeypatch.setattr(settings, "FACEBOOK_ACCESS_TOKEN", "server-wide-token")
        viewer = make_user("viewer-o@example.com")
        add_member(tenants.org_a, viewer, role="viewer")
        assert client.get("/api/v1/overview", headers=login_headers(viewer, tenants.org_a)).status_code == 403
        body = client.get("/api/v1/overview", headers=tenants.ha).json()
        assert body["campaigns"] == [] and "meta" in body["errors"]
        assert "server-wide-token" not in str(body)

    def test_viewer_cannot_write_campaigns_or_ads(self, client, tenants, make_user, add_member, login_headers):
        viewer = make_user("viewer-c@example.com")
        add_member(tenants.org_a, viewer, role="viewer")
        h = login_headers(viewer, tenants.org_a)
        assert self._campaign(client, h).status_code == 403
        assert batch(client, h, ad_payload()).status_code == 403
        assert client.get("/api/v1/generated-ads/", headers=h).status_code == 200


# --------------------------------------------------------------------------
# Global catalogs are read-only for tenants
# --------------------------------------------------------------------------
class TestGlobalCatalogs:
    @pytest.fixture
    def prompt(self, db_session):
        row = Prompt(id=f"p-{uuid.uuid4().hex[:6]}", name="Catalog prompt", category="c", template="t")
        db_session.add(row)
        db_session.commit()
        yield row
        db_session.query(Prompt).filter(Prompt.name.in_(["Catalog prompt", "Hacked", "Admin prompt"])).delete(synchronize_session=False)
        db_session.commit()

    def test_every_tenant_reads_the_catalog(self, client, tenants, prompt):
        for headers in (tenants.ha, tenants.hb):
            assert any(p["id"] == prompt.id for p in client.get("/api/v1/prompts/", headers=headers).json())

    def test_tenant_owner_cannot_change_the_catalog(self, client, tenants, prompt):
        assert client.put(f"/api/v1/prompts/{prompt.id}", headers=tenants.ha, json={"name": "Hacked"}).status_code == 403
        assert client.delete(f"/api/v1/prompts/{prompt.id}", headers=tenants.ha).status_code == 403
        assert client.post("/api/v1/prompts/", headers=tenants.ha,
                           json={"id": "new-p", "name": "Hacked", "category": "c", "template": "t"}).status_code == 403
        assert client.post("/api/v1/ad-styles/", headers=tenants.ha, json={"id": "s1", "name": "n", "category": "c"}).status_code == 403
        assert client.get("/api/v1/prompts/" + prompt.id, headers=tenants.ha).json()["name"] == "Catalog prompt"

    def test_database_blocks_catalog_writes_without_the_signed_admin_context(self, tenants, db_session):
        from app.database import SessionLocal, bind_context
        db = SessionLocal()
        try:
            bind_context(db, tenants.org_a.id)
            db.add(Prompt(id="rls-p", name="Hacked", category="c", template="t"))
            with pytest.raises(Exception, match="row-level security"):
                db.commit()
            db.rollback()
            db.execute(text("SELECT set_config('app.platform_admin', 'on', true)"))  # unsigned: ignored
            db.add(Prompt(id="rls-p2", name="Hacked", category="c", template="t"))
            with pytest.raises(Exception, match="row-level security"):
                db.commit()
        finally:
            db.rollback()
            db.close()

    def test_platform_administrator_can_manage_the_catalog_and_still_sees_no_tenant_data(
            self, client, make_user, login_headers, tenants, seed, db_session):
        admin = make_user("root@example.com", superuser=True)
        h = login_headers(admin)
        created = client.post("/api/v1/prompts/", headers=h, json={"id": f"adm-{uuid.uuid4().hex[:6]}", "name": "Admin prompt",
                                                                    "category": "c", "template": "t"})
        assert created.status_code == 200, created.text
        client.delete(f"/api/v1/prompts/{created.json()['id']}", headers=h)
        seed.brand(tenants.org_a)
        assert client.get("/api/v1/brands", headers=h).status_code == 403  # superuser is not a tenant member

    def test_denied_catalog_write_is_audited(self, client, tenants, prompt, db_session):
        client.put(f"/api/v1/prompts/{prompt.id}", headers=tenants.ha, json={"name": "Hacked"})
        rows = db_session.query(AuditLog).filter(AuditLog.action == "catalog.write_denied").all()
        assert rows and rows[-1].actor_user_id == tenants.alice.id


# --------------------------------------------------------------------------
# Audit: member changes and denied access (and no secrets)
# --------------------------------------------------------------------------
class TestAudit:
    def _actions(self, db_session, org):
        return [(r.action, r.outcome) for r in db_session.query(AuditLog).filter(
            AuditLog.organization_id == org.id).order_by(AuditLog.created_at)]

    def test_member_changes_are_audited(self, client, tenants, make_user, db_session):
        carol = make_user("carol@example.com")
        base = f"/api/v1/organizations/{tenants.org_a.id}/members"
        assert client.post(base, headers=tenants.ha, json={"email": carol.email, "role": "viewer"}).status_code == 201
        assert client.patch(f"{base}/{carol.id}", headers=tenants.ha, json={"role": "manager"}).status_code == 200
        assert client.delete(f"{base}/{carol.id}", headers=tenants.ha).status_code == 200
        actions = [a for a, _ in self._actions(db_session, tenants.org_a)]
        assert actions == ["member.added", "member.role_changed", "member.revoked"]
        changed = db_session.query(AuditLog).filter(AuditLog.action == "member.role_changed").one()
        assert changed.detail == {"from": "viewer", "to": "manager"} and changed.target == carol.id
        assert changed.actor_user_id == tenants.alice.id

    def test_denied_access_to_a_foreign_organization_is_audited(self, client, tenants, db_session):
        response = client.get("/api/v1/brands", headers={**tenants.ha, "X-Organization-Id": tenants.org_b.id})
        assert response.status_code == 403
        row = db_session.query(AuditLog).filter(AuditLog.action == "access.denied").one()
        assert row.outcome == "denied" and row.actor_user_id == tenants.alice.id
        assert row.organization_id == tenants.org_b.id and row.target == "GET /api/v1/brands"

    def test_insufficient_permission_is_audited_inside_the_organization(
            self, client, tenants, make_user, add_member, login_headers, db_session):
        viewer = make_user("viewer-a@example.com")
        add_member(tenants.org_a, viewer, role="viewer")
        assert batch(client, login_headers(viewer, tenants.org_a), ad_payload()).status_code == 403
        row = db_session.query(AuditLog).filter(AuditLog.action == "permission.denied").one()
        assert row.organization_id == tenants.org_a.id and row.target == "ads:write"
        assert row.detail["role"] == "viewer"

    def test_audit_log_is_readable_by_admins_only_and_only_for_the_own_organization(
            self, client, tenants, make_user, add_member, login_headers):
        client.get("/api/v1/brands", headers={**tenants.hb, "X-Organization-Id": tenants.org_a.id})  # B probes A
        log = client.get(f"/api/v1/organizations/{tenants.org_a.id}/audit-log", headers=tenants.ha)
        assert log.status_code == 200 and log.json()[0]["action"] == "access.denied"
        assert client.get(f"/api/v1/organizations/{tenants.org_b.id}/audit-log", headers=tenants.ha).status_code == 403
        manager = make_user("manager-a@example.com")
        add_member(tenants.org_a, manager, role="manager")
        assert client.get(f"/api/v1/organizations/{tenants.org_a.id}/audit-log",
                          headers=login_headers(manager, tenants.org_a)).status_code == 403

    def test_audit_never_stores_secrets(self, client, tenants, db_session):
        client.get("/api/v1/brands", headers={**tenants.ha, "X-Organization-Id": tenants.org_b.id})
        token = tenants.ha["Authorization"].split(" ", 1)[1]
        dump = " ".join(str(vars(r)) for r in db_session.query(AuditLog).all())
        assert token not in dump and "Bearer" not in dump

    def test_sanitizer_drops_secret_like_keys_and_truncates(self):
        from app.core.audit import sanitize_detail
        out = sanitize_detail({"role": "x", "api_key": "k", "Authorization": "t", "password": "p", "note": "n" * 999})
        assert set(out) == {"role", "note"} and len(out["note"]) == 200

    def test_audit_table_is_append_only_for_the_application(self, tenants):
        from app.database import SessionLocal, bind_context
        db = SessionLocal()
        try:
            bind_context(db, tenants.org_a.id)
            with pytest.raises(Exception, match="permission denied"):
                db.execute(text("DELETE FROM audit_logs"))
            db.rollback()
            with pytest.raises(Exception, match="permission denied"):
                db.execute(text("UPDATE audit_logs SET action = 'x'"))
        finally:
            db.rollback()
            db.close()


class TestPlatformUploadReferences:
    """/facebook/upload-* hand a media reference to the ad platform service: it must
    never read an arbitrary server path or another tenant's private file."""

    def test_arbitrary_server_paths_and_foreign_files_are_refused(self, client, tenants, mock_facebook_service):
        url_b = client.post("/api/v1/uploads/", headers=tenants.hb,
                            files={"file": ("a.png", io.BytesIO(PNG), "image/png")}).json()["url"]
        for ref in ("/etc/passwd", "../../.env", "uploads/../.env", url_b, url_b.lstrip("/"), "uploads/legacy.png"):
            r = client.post("/api/v1/facebook/upload-image", headers=tenants.ha, json={"image_url": ref})
            assert r.status_code == 400, ref
            r = client.post("/api/v1/facebook/upload-video", headers=tenants.ha, json={"video_url": ref})
            assert r.status_code == 400, ref
        mock_facebook_service.upload_image.assert_not_called()
        mock_facebook_service.upload_video.assert_not_called()

    def test_own_upload_and_external_url_are_accepted(self, client, tenants, mock_facebook_service):
        mock_facebook_service.upload_image.return_value = "hash123"
        own = client.post("/api/v1/uploads/", headers=tenants.ha,
                          files={"file": ("a.png", io.BytesIO(PNG), "image/png")}).json()["url"]
        for ref in (own, own.lstrip("/"), "https://example.com/x.png"):
            r = client.post("/api/v1/facebook/upload-image", headers=tenants.ha, json={"image_url": ref})
            assert r.status_code == 200, (ref, r.text)
        first_arg = mock_facebook_service.upload_image.call_args_list[0].args[0]
        assert first_arg.replace("\\", "/").endswith(own.rsplit("/", 1)[-1])
