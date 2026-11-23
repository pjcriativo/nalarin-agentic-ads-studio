"""Multi-tenant foundation: organization context, RBAC, isolation and IDOR.

Two users (alice, bob) own two organizations (A, B). Everything here goes
through the real HTTP API and the real application database layer (runtime role
+ row level security); ``db_session`` is the superuser seeding connection used
to arrange data and to look at the truth afterwards.
"""
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.models import (
    Brand, CustomerProfile, Organization, OrganizationMember, Product,
)

BRANDS = "/api/v1/brands"
PRODUCTS = "/api/v1/products"
PROFILES = "/api/v1/profiles"
ORGS = "/api/v1/organizations"


def brand_payload(name="Brand", **extra):
    return {
        "name": name,
        "colors": {"primary": "#111111", "secondary": "#222222", "highlight": "#333333"},
        "voice": "Plain",
        "products": [],
        "profileIds": [],
        **extra,
    }


@pytest.fixture
def tenants(make_user, make_org, login_headers):
    alice = make_user("alice@example.com")
    bob = make_user("bob@example.com")
    org_a = make_org("Org A", owner=alice)
    org_b = make_org("Org B", owner=bob)
    return SimpleNamespace(
        alice=alice, bob=bob, org_a=org_a, org_b=org_b,
        ha=login_headers(alice), hb=login_headers(bob),
    )


def seed_org_data(client, headers, tag):
    """Create a brand with a product and a profile through the API."""
    profile = client.post(PROFILES + "/", headers=headers,
                          json={"name": f"profile-{tag}", "demographics": "d", "painPoints": "p", "goals": "g"})
    assert profile.status_code == 200, profile.text
    brand = client.post(BRANDS + "/", headers=headers, json=brand_payload(
        f"brand-{tag}",
        products=[{"name": f"product-{tag}", "description": "x", "product_shots": []}],
        profileIds=[profile.json()["id"]],
    ))
    assert brand.status_code == 200, brand.text
    return SimpleNamespace(
        brand_id=brand.json()["id"],
        product_id=brand.json()["products"][0]["id"],
        profile_id=profile.json()["id"],
    )


# --------------------------------------------------------------------------
# Organization context (get_current_org)
# --------------------------------------------------------------------------
class TestOrganizationContext:
    def test_user_without_membership_is_rejected(self, client, make_user, login_headers):
        loner = make_user("loner@example.com")
        response = client.get(BRANDS, headers=login_headers(loner))
        assert response.status_code == 403

    def test_single_membership_is_the_default_organization(self, client, tenants):
        assert client.post(BRANDS + "/", headers=tenants.ha, json=brand_payload("only")).status_code == 200
        assert [b["name"] for b in client.get(BRANDS, headers=tenants.ha).json()] == ["only"]

    def test_several_memberships_require_the_header(self, client, tenants, add_member, login_headers):
        add_member(tenants.org_b, tenants.alice, role="viewer")
        assert client.get(BRANDS, headers=tenants.ha).status_code == 400
        ok = client.get(BRANDS, headers=login_headers(tenants.alice, tenants.org_a))
        assert ok.status_code == 200

    def test_header_selects_the_organization_data(self, client, tenants, add_member, login_headers):
        add_member(tenants.org_b, tenants.alice, role="manager")
        client.post(BRANDS + "/", headers=login_headers(tenants.alice, tenants.org_a), json=brand_payload("in-a"))
        client.post(BRANDS + "/", headers=login_headers(tenants.alice, tenants.org_b), json=brand_payload("in-b"))
        names_a = [b["name"] for b in client.get(BRANDS, headers=login_headers(tenants.alice, tenants.org_a)).json()]
        names_b = [b["name"] for b in client.get(BRANDS, headers=login_headers(tenants.alice, tenants.org_b)).json()]
        assert names_a == ["in-a"] and names_b == ["in-b"]

    def test_foreign_and_unknown_organization_answer_identically(self, client, tenants, login_headers):
        foreign = client.get(BRANDS, headers=login_headers(tenants.alice, tenants.org_b))
        ghost = Organization(id=str(uuid.uuid4()), name="ghost", slug="ghost")
        unknown = client.get(BRANDS, headers={**tenants.ha, "X-Organization-Id": ghost.id})
        assert foreign.status_code == unknown.status_code == 403
        assert foreign.json() == unknown.json()  # no way to tell "exists" from "does not exist"

    def test_revoked_membership_loses_access(self, client, tenants, db_session):
        assert client.get(BRANDS, headers=tenants.ha).status_code == 200
        member = db_session.query(OrganizationMember).filter_by(user_id=tenants.alice.id).one()
        member.status = "revoked"
        db_session.commit()
        assert client.get(BRANDS, headers=tenants.ha).status_code == 403

    def test_inactive_organization_is_blocked(self, client, tenants, db_session):
        tenants.org_a.is_active = False
        db_session.commit()
        assert client.get(BRANDS, headers=tenants.ha).status_code == 403

    def test_global_admin_role_and_superuser_do_not_bypass_tenancy(self, client, tenants, make_user, login_headers, db_session):
        """Old global roles / is_superuser must not open another tenant's data."""
        seed_org_data(client, tenants.hb, "b")
        from app.models import Role
        admin = make_user("root@example.com", superuser=True)
        role = db_session.query(Role).filter(Role.name == "admin").first()
        if role:
            admin.roles.append(role)
            db_session.commit()
        # no membership at all -> nothing, in spite of being superuser + admin role
        assert client.get(BRANDS, headers=login_headers(admin)).status_code == 403
        # explicitly asking for Org B -> same wall
        assert client.get(BRANDS, headers=login_headers(admin, tenants.org_b)).status_code == 403


# --------------------------------------------------------------------------
# Isolation between tenants, including IDOR with valid ids of the other tenant
# --------------------------------------------------------------------------
class TestIsolation:
    def test_lists_only_the_callers_data(self, client, tenants):
        seed_org_data(client, tenants.ha, "a")
        seed_org_data(client, tenants.hb, "b")
        for path, key in ((BRANDS, "name"), (PRODUCTS, "name"), (PROFILES, "name")):
            names_a = [x[key] for x in client.get(path, headers=tenants.ha).json()]
            names_b = [x[key] for x in client.get(path, headers=tenants.hb).json()]
            assert names_a and all(n.endswith("-a") for n in names_a), (path, names_a)
            assert names_b and all(n.endswith("-b") for n in names_b), (path, names_b)

    def test_idor_brand_read_update_delete(self, client, tenants, db_session):
        b = seed_org_data(client, tenants.hb, "b")
        # alice holds VALID ids of bob's records
        assert client.put(f"{BRANDS}/{b.brand_id}", headers=tenants.ha, json=brand_payload("pwned")).status_code == 404
        assert client.delete(f"{BRANDS}/{b.brand_id}", headers=tenants.ha).status_code == 404
        db_session.expire_all()
        brand = db_session.get(Brand, b.brand_id)
        assert brand is not None and brand.name == "brand-b" and brand.organization_id == tenants.org_b.id

    def test_idor_product_read_update_delete(self, client, tenants, db_session):
        b = seed_org_data(client, tenants.hb, "b")
        url = f"{PRODUCTS}/{b.product_id}"
        assert client.get(url, headers=tenants.ha).status_code == 404
        assert client.put(url, headers=tenants.ha, json={"name": "pwned"}).status_code == 404
        assert client.delete(url, headers=tenants.ha).status_code == 404
        db_session.expire_all()
        assert db_session.get(Product, b.product_id).name == "product-b"
        # the legitimate owner still sees it
        assert client.get(url, headers=tenants.hb).status_code == 200

    def test_idor_profile_update_delete(self, client, tenants, db_session):
        b = seed_org_data(client, tenants.hb, "b")
        body = {"name": "pwned", "demographics": "", "painPoints": "", "goals": ""}
        assert client.put(f"{PROFILES}/{b.profile_id}", headers=tenants.ha, json=body).status_code == 404
        assert client.delete(f"{PROFILES}/{b.profile_id}", headers=tenants.ha).status_code == 404
        db_session.expire_all()
        assert db_session.get(CustomerProfile, b.profile_id).name == "profile-b"

    def test_cannot_create_a_product_under_another_tenants_brand(self, client, tenants, db_session):
        b = seed_org_data(client, tenants.hb, "b")
        response = client.post(PRODUCTS + "/", headers=tenants.ha,
                               json={"name": "intruder", "brand_id": b.brand_id})
        assert response.status_code == 404
        assert db_session.query(Product).filter_by(name="intruder").count() == 0

    def test_foreign_profile_ids_are_never_linked(self, client, tenants, db_session):
        b = seed_org_data(client, tenants.hb, "b")
        response = client.post(BRANDS + "/", headers=tenants.ha,
                               json=brand_payload("mine", profileIds=[b.profile_id]))
        assert response.status_code == 200
        assert response.json()["profileIds"] == []
        links = db_session.execute(
            text("SELECT count(*) FROM brand_profiles WHERE profile_id = :p"), {"p": b.profile_id},
        ).scalar()
        assert links == 1  # only bob's own original link

    def test_brand_update_cannot_hijack_another_tenants_product(self, client, tenants, db_session):
        a = seed_org_data(client, tenants.ha, "a")
        b = seed_org_data(client, tenants.hb, "b")
        response = client.put(f"{BRANDS}/{a.brand_id}", headers=tenants.ha, json=brand_payload(
            "brand-a", products=[{"id": b.product_id, "name": "stolen", "description": "", "product_shots": []}]))
        assert response.status_code in (200, 409)
        db_session.expire_all()
        victim = db_session.get(Product, b.product_id)
        assert victim.name == "product-b"
        assert victim.brand_id == b.brand_id and victim.organization_id == tenants.org_b.id

    def test_id_collision_with_another_tenants_row_is_refused_not_overwritten(self, client, tenants, db_session):
        b = seed_org_data(client, tenants.hb, "b")
        response = client.post(BRANDS + "/", headers=tenants.ha, json=brand_payload("clash", id=b.brand_id))
        assert response.status_code == 409
        db_session.expire_all()
        assert db_session.get(Brand, b.brand_id).name == "brand-b"

    def test_client_cannot_choose_the_organization(self, client, tenants, db_session):
        response = client.post(BRANDS + "/", headers=tenants.ha,
                               json=brand_payload("sneaky", organization_id=tenants.org_b.id))
        assert response.status_code == 200
        assert db_session.get(Brand, response.json()["id"]).organization_id == tenants.org_a.id

    def test_dashboard_counts_only_the_callers_brands_and_products(self, client, tenants):
        seed_org_data(client, tenants.ha, "a")
        seed_org_data(client, tenants.hb, "b")
        seed_org_data(client, tenants.hb, "b2")
        a = client.get("/api/v1/dashboard/stats", headers=tenants.ha).json()
        b = client.get("/api/v1/dashboard/stats", headers=tenants.hb).json()
        assert (a["brands_count"], a["products_count"]) == (1, 1)
        assert (b["brands_count"], b["products_count"]) == (2, 2)

    def test_ad_remix_reconstruct_does_not_see_another_tenants_brand(self, client, tenants, db_session):
        from app.models import WinningAd
        b = seed_org_data(client, tenants.hb, "b")
        template = WinningAd(id=str(uuid.uuid4()), name="tpl", image_url="http://x/y.png", blueprint_json={"x": 1})
        db_session.add(template)
        db_session.commit()
        response = client.post("/api/v1/ad-remix/reconstruct", headers=tenants.ha, json={
            "template_id": template.id, "brand_id": b.brand_id,
            "product_id": b.product_id, "profile_id": b.profile_id,
            "campaign_offer": "o", "campaign_messaging": "m"})
        assert response.status_code == 404


# --------------------------------------------------------------------------
# RBAC inside an organization
# --------------------------------------------------------------------------
READ_ALL = [BRANDS, PRODUCTS, PROFILES]


class TestRoleBasedAccess:
    @pytest.fixture
    def with_role(self, make_user, add_member, login_headers, tenants):
        def _make(role):
            user = make_user(f"{role}-member@example.com")
            add_member(tenants.org_a, user, role=role)
            return login_headers(user)
        return _make

    @pytest.mark.parametrize("role", ["viewer", "analyst"])
    def test_read_only_roles(self, client, tenants, with_role, role):
        headers = with_role(role)
        seed_org_data(client, tenants.ha, "a")
        for path in READ_ALL:
            assert client.get(path, headers=headers).status_code == 200
        assert client.post(BRANDS + "/", headers=headers, json=brand_payload("x")).status_code == 403
        assert client.post(PROFILES + "/", headers=headers, json={"name": "x"}).status_code == 403

    @pytest.mark.parametrize("role", ["manager", "admin", "owner"])
    def test_write_roles(self, client, tenants, with_role, role):
        headers = with_role(role)
        created = client.post(BRANDS + "/", headers=headers, json=brand_payload(f"by-{role}"))
        assert created.status_code == 200
        assert client.delete(f"{BRANDS}/{created.json()['id']}", headers=headers).status_code == 200

    def test_viewer_cannot_update_or_delete(self, client, tenants, with_role):
        a = seed_org_data(client, tenants.ha, "a")
        headers = with_role("viewer")
        assert client.put(f"{BRANDS}/{a.brand_id}", headers=headers, json=brand_payload("x")).status_code == 403
        assert client.delete(f"{BRANDS}/{a.brand_id}", headers=headers).status_code == 403
        assert client.delete(f"{PRODUCTS}/{a.product_id}", headers=headers).status_code == 403

    def test_a_role_in_one_organization_grants_nothing_in_another(self, client, tenants, make_user, add_member, login_headers):
        user = make_user("dual@example.com")
        add_member(tenants.org_a, user, role="owner")
        add_member(tenants.org_b, user, role="viewer")
        assert client.post(BRANDS + "/", headers=login_headers(user, tenants.org_a), json=brand_payload("ok")).status_code == 200
        assert client.post(BRANDS + "/", headers=login_headers(user, tenants.org_b), json=brand_payload("no")).status_code == 403


# --------------------------------------------------------------------------
# Organizations and memberships API
# --------------------------------------------------------------------------
class TestOrganizationsApi:
    def test_list_and_create(self, client, tenants):
        mine = client.get(ORGS, headers=tenants.ha).json()
        assert [(o["id"], o["role"]) for o in mine] == [(tenants.org_a.id, "owner")]
        created = client.post(ORGS, headers=tenants.ha, json={"name": "Second"})
        assert created.status_code == 201 and created.json()["role"] == "owner"
        assert len(client.get(ORGS, headers=tenants.ha).json()) == 2

    def test_only_members_can_list_members(self, client, tenants):
        assert client.get(f"{ORGS}/{tenants.org_a.id}/members", headers=tenants.ha).status_code == 200
        assert client.get(f"{ORGS}/{tenants.org_a.id}/members", headers=tenants.hb).status_code == 403

    def test_add_member_then_isolation_applies_to_them(self, client, tenants, make_user, login_headers):
        carol = make_user("carol@example.com")
        res = client.post(f"{ORGS}/{tenants.org_a.id}/members", headers=tenants.ha,
                          json={"email": carol.email, "role": "viewer"})
        assert res.status_code == 201
        seed_org_data(client, tenants.ha, "a")
        assert len(client.get(BRANDS, headers=login_headers(carol)).json()) == 1

    def test_cannot_add_members_to_a_foreign_organization(self, client, tenants, make_user):
        carol = make_user("carol@example.com")
        res = client.post(f"{ORGS}/{tenants.org_b.id}/members", headers=tenants.ha,
                          json={"email": carol.email, "role": "viewer"})
        assert res.status_code == 403

    def test_admin_cannot_grant_or_touch_owner(self, client, tenants, make_user, add_member, login_headers):
        admin = make_user("adm@example.com")
        add_member(tenants.org_a, admin, role="admin")
        carol = make_user("carol@example.com")
        h = login_headers(admin)
        base = f"{ORGS}/{tenants.org_a.id}/members"
        assert client.post(base, headers=h, json={"email": carol.email, "role": "owner"}).status_code == 403
        assert client.patch(f"{base}/{tenants.alice.id}", headers=h, json={"role": "viewer"}).status_code == 403
        assert client.delete(f"{base}/{tenants.alice.id}", headers=h).status_code == 403
        assert client.post(base, headers=h, json={"email": carol.email, "role": "analyst"}).status_code == 201

    def test_manager_cannot_manage_members(self, client, tenants, make_user, add_member, login_headers):
        mgr = make_user("mgr@example.com")
        add_member(tenants.org_a, mgr, role="manager")
        carol = make_user("carol@example.com")
        res = client.post(f"{ORGS}/{tenants.org_a.id}/members", headers=login_headers(mgr),
                          json={"email": carol.email, "role": "viewer"})
        assert res.status_code == 403

    def test_last_owner_is_protected(self, client, tenants):
        base = f"{ORGS}/{tenants.org_a.id}/members"
        assert client.patch(f"{base}/{tenants.alice.id}", headers=tenants.ha, json={"role": "admin"}).status_code == 409
        assert client.delete(f"{base}/{tenants.alice.id}", headers=tenants.ha).status_code == 409

    def test_revoke_blocks_access_and_readd_restores_it(self, client, tenants, make_user, add_member, login_headers):
        dave = make_user("dave@example.com")
        add_member(tenants.org_a, dave, role="viewer")
        base = f"{ORGS}/{tenants.org_a.id}/members"
        assert client.get(BRANDS, headers=login_headers(dave)).status_code == 200
        assert client.delete(f"{base}/{dave.id}", headers=tenants.ha).status_code == 200
        assert client.get(BRANDS, headers=login_headers(dave)).status_code == 403
        assert client.post(base, headers=tenants.ha, json={"email": dave.email, "role": "analyst"}).status_code == 201
        assert client.get(BRANDS, headers=login_headers(dave)).status_code == 200

    def test_a_member_may_leave_on_their_own(self, client, tenants, make_user, add_member, login_headers):
        eve = make_user("eve@example.com")
        add_member(tenants.org_a, eve, role="viewer")
        res = client.delete(f"{ORGS}/{tenants.org_a.id}/members/{eve.id}", headers=login_headers(eve))
        assert res.status_code == 200

    def test_invalid_role_and_unknown_user(self, client, tenants):
        base = f"{ORGS}/{tenants.org_a.id}/members"
        assert client.post(base, headers=tenants.ha, json={"email": "x@example.com", "role": "god"}).status_code == 422
        assert client.post(base, headers=tenants.ha, json={"email": "nobody@example.com", "role": "viewer"}).status_code == 404

    def test_register_creates_a_personal_organization(self, client, make_user, login_headers, db_session):
        from app.models import Role
        admin = make_user("registrar@example.com", superuser=True)
        res = client.post("/api/v1/auth/register", headers=login_headers(admin),
                          json={"email": "fresh@example.com", "password": "Sup3rSecret!pw", "name": "Fresh"})
        assert res.status_code == 201, res.text
        from app.models import User
        fresh = db_session.query(User).filter_by(email="fresh@example.com").one()
        member = db_session.query(OrganizationMember).filter_by(user_id=fresh.id).one()
        assert member.role == "owner" and member.status == "active"
