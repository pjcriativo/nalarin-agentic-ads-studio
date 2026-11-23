"""Etapa 01.3: isolation of ad-platform integrations, credentials, API keys, bots and operations.

Real HTTP API + real runtime role (RLS in force). ``db_session`` is the superuser
seeding connection. No ad-platform call is ever made: every service function that
would talk to Google / Meta / TikTok is replaced by a mock, and the tests assert
whether the mutating ones were (not) called.
"""
import hashlib
import secrets
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models import (
    AdAccountLink, ApiKey, AuditLog, GoogleAdsConnection, MetaAdsConnection, MutationAudit,
    SpendApproval, TikTokAdsConnection,
)

G = "/api/v1/google-ads"
T = "/api/v1/tiktok-ads"
F = "/api/v1/facebook"
OPT = "/api/v1/optimization"
BOT = "/api/v1/bot"
CIPHER_A = "cipher-token-of-org-A-SECRET"
CIPHER_B = "cipher-token-of-org-B-SECRET"


@pytest.fixture
def world(make_user, make_org, add_member, login_headers):
    """alice owns TWO organizations (A1, A2); bob owns B. Different roles via helper."""
    alice = make_user("alice-i@example.com")
    bob = make_user("bob-i@example.com")
    a1 = make_org("A One", owner=alice)
    a2 = make_org("A Two", owner=alice)
    b = make_org("B Org", owner=bob)

    def member(org, role, email):
        user = make_user(email)
        add_member(org, user, role=role)
        return user, login_headers(user, org)

    return SimpleNamespace(
        alice=alice, bob=bob, a1=a1, a2=a2, b=b,
        h_a1=login_headers(alice, a1), h_a2=login_headers(alice, a2), h_b=login_headers(bob, b),
        member=member,
    )


@pytest.fixture
def link(db_session):
    """Seed an ad-platform connection + its explicit link for an organization."""
    def _link(org, provider, account, token=CIPHER_A, selected=True, status="active", user=None):
        from app.services.integrations import PROVIDERS
        model = PROVIDERS[provider]["model"]
        col = PROVIDERS[provider]["account_col"]
        fields = {"organization_id": org.id, "user_id": (user or SimpleNamespace(id=None)).id, col: account,
                  "is_active": selected, "account_name": f"acct {account}"}
        if provider == "meta":
            fields["encrypted_access_token"] = token
        else:
            fields["encrypted_refresh_token"] = token
        if fields["user_id"] is None:
            from app.models import OrganizationMember
            fields["user_id"] = db_session.query(OrganizationMember).filter(
                OrganizationMember.organization_id == org.id).first().user_id
        db_session.add(model(**fields))
        db_session.add(AdAccountLink(organization_id=org.id, provider=provider,
                                     external_account_id=account.replace("act_", ""), status=status))
        db_session.commit()
    return _link


@pytest.fixture
def google(monkeypatch, google_ads_configured):
    """Every Google service call mocked; returns the mocks."""
    from app.api.v1 import google_ads as mod
    mocks = SimpleNamespace(
        campaigns=AsyncMock(return_value=[{"id": "1", "name": "c"}]),
        create=AsyncMock(return_value={"campaign_id": "9", "status": "PAUSED"}),
        pause=AsyncMock(return_value={"campaign_id": "1", "status": "PAUSED"}),
        enable=AsyncMock(return_value={"campaign_id": "1", "status": "ENABLED"}),
        negative=AsyncMock(return_value={"ok": True}),
        budget=AsyncMock(return_value=5_000_000),
    )
    monkeypatch.setattr(mod, "get_campaign_performance", mocks.campaigns)
    monkeypatch.setattr(mod, "create_google_campaign", mocks.create)
    monkeypatch.setattr(mod, "pause_google_campaign", mocks.pause)
    monkeypatch.setattr(mod, "enable_google_campaign", mocks.enable)
    monkeypatch.setattr(mod, "add_google_negative_keywords", mocks.negative)
    monkeypatch.setattr(mod, "get_campaign_budget_micros", mocks.budget)
    monkeypatch.setattr(mod, "get_valid_access_token", AsyncMock(return_value="access"))
    monkeypatch.setattr(mod, "decrypt_token", lambda blob: f"plain:{blob}")
    return mocks


# --------------------------------------------------------------------------
# Connections are per organization; accounts are linked explicitly
# --------------------------------------------------------------------------
class TestConnectionIsolation:
    def test_org_b_never_sees_org_as_connections_or_accounts(self, client, world, link):
        link(world.a1, "google_ads", "1111111111", CIPHER_A)
        link(world.a1, "tiktok", "tt-A", CIPHER_A)
        link(world.a1, "meta", "act_111", CIPHER_A)
        for base in (G, T, F):
            assert client.get(f"{base}/connection", headers=world.h_b).json() == {"connected": False}
            assert client.get(f"{base}/connections", headers=world.h_b).json() == {"connections": []}
            seen = client.get(f"{base}/connections", headers=world.h_a1).json()["connections"]
            assert len(seen) == 1 and seen[0]["selected"] is True

    def test_a_user_in_two_organizations_does_not_share_accounts_between_them(self, client, world, link, google):
        link(world.a1, "google_ads", "1111111111", CIPHER_A)       # alice connected it for A1 only
        assert client.get(f"{G}/connection", headers=world.h_a1).json()["customer_id"] == "1111111111"
        assert client.get(f"{G}/connection", headers=world.h_a2).json() == {"connected": False}
        assert client.get(f"{G}/campaigns", headers=world.h_a1).status_code == 200
        assert client.get(f"{G}/campaigns", headers=world.h_a2).status_code == 404   # fail closed
        google.campaigns.assert_awaited_once()

    def test_each_organization_uses_only_its_own_token(self, client, world, link, google):
        link(world.a1, "google_ads", "1111111111", CIPHER_A)
        link(world.b, "google_ads", "2222222222", CIPHER_B)
        client.get(f"{G}/campaigns", headers=world.h_a1)
        client.get(f"{G}/campaigns", headers=world.h_b)
        tokens = [c.args[0] for c in google.campaigns.await_args_list]
        assert tokens == [f"plain:{CIPHER_A}", f"plain:{CIPHER_B}"]

    def test_tokens_never_appear_in_any_response(self, client, world, link, google):
        link(world.a1, "google_ads", "1111111111", CIPHER_A)
        link(world.a1, "tiktok", "tt-A", CIPHER_A)
        link(world.a1, "meta", "act_111", CIPHER_A)
        for path in (f"{G}/connection", f"{G}/connections", f"{T}/connection", f"{T}/connections",
                     f"{F}/connection", f"{F}/connections", f"{G}/campaigns"):
            assert "SECRET" not in client.get(path, headers=world.h_a1).text, path

    def test_selecting_a_foreign_account_is_404_and_changes_nothing(self, client, world, link, db_session):
        link(world.b, "google_ads", "2222222222", CIPHER_B, selected=False, status="pending")
        link(world.a1, "google_ads", "1111111111", CIPHER_A)
        r = client.post(f"{G}/connection/select", headers=world.h_a1, json={"customer_id": "2222222222"})
        assert r.status_code == 404
        assert client.get(f"{G}/connection", headers=world.h_a1).json()["customer_id"] == "1111111111"
        assert db_session.query(AdAccountLink).filter(
            AdAccountLink.organization_id == world.b.id).one().status == "pending"

    def test_selection_activates_exactly_that_link(self, client, world, link, db_session):
        link(world.a1, "tiktok", "tt-1", CIPHER_A, selected=True, status="active")
        link(world.a1, "tiktok", "tt-2", CIPHER_A, selected=False, status="pending")
        assert client.post(f"{T}/connection/select", headers=world.h_a1, json={"advertiser_id": "tt-2"}).status_code == 200
        statuses = {l.external_account_id: l.status for l in db_session.query(AdAccountLink).filter(
            AdAccountLink.organization_id == world.a1.id)}
        assert statuses == {"tt-1": "active", "tt-2": "active"}      # tt-1 stays authorized, tt-2 now selected
        assert client.get(f"{T}/connection", headers=world.h_a1).json()["advertiser_id"] == "tt-2"

    def test_selected_connection_without_an_active_link_is_unusable(self, client, world, link, google):
        link(world.a1, "google_ads", "1111111111", CIPHER_A, status="pending")
        assert client.get(f"{G}/connection", headers=world.h_a1).json() == {"connected": False}
        assert client.get(f"{G}/campaigns", headers=world.h_a1).status_code == 404
        google.campaigns.assert_not_awaited()

    def test_disconnect_revokes_the_link(self, client, world, link, google, db_session):
        link(world.a1, "google_ads", "1111111111", CIPHER_A)
        assert client.delete(f"{G}/connection", headers=world.h_a1).status_code == 200
        assert db_session.query(AdAccountLink).one().status == "revoked"
        assert client.get(f"{G}/campaigns", headers=world.h_a1).status_code == 404

    def test_meta_requested_account_must_be_linked_to_the_organization(self, client, world, link, monkeypatch):
        from app.api.v1 import facebook as mod
        link(world.a1, "meta", "act_111", CIPHER_A)
        link(world.b, "meta", "act_999", CIPHER_B)
        monkeypatch.setattr(mod, "decrypt_token", lambda b: "plain")
        service = MagicMock()
        service.api = object()
        service.get_campaigns.return_value = []
        monkeypatch.setattr(mod, "FacebookService", lambda **kw: service)
        assert client.get(f"{F}/campaigns?ad_account_id=act_111", headers=world.h_a1).status_code == 200
        assert client.get(f"{F}/campaigns?ad_account_id=act_999", headers=world.h_a1).status_code == 404
        assert client.get(f"{F}/campaigns?ad_account_id=act_000", headers=world.h_a1).status_code == 404

    def test_meta_objects_outside_the_linked_accounts_are_refused(self, client, world, link, monkeypatch):
        from app.api.v1 import facebook as mod
        link(world.a1, "meta", "act_111", CIPHER_A)
        monkeypatch.setattr(mod, "decrypt_token", lambda b: "plain")
        service = MagicMock()
        service.api = object()
        service.object_account_id.side_effect = lambda oid: {"mine": "111", "theirs": "999"}[oid]
        service.get_ads.return_value = []
        monkeypatch.setattr(mod, "FacebookService", lambda **kw: service)
        assert client.get(f"{F}/ads?adset_id=mine", headers=world.h_a1).status_code == 200
        assert client.get(f"{F}/ads?adset_id=theirs", headers=world.h_a1).status_code == 404


class TestConnectionRBAC:
    def test_matrix(self, client, world, link, google):
        link(world.a1, "google_ads", "1111111111", CIPHER_A)
        link(world.a1, "google_ads", "3333333333", CIPHER_A, selected=False, status="pending")
        users = {role: world.member(world.a1, role, f"{role}-rbac@example.com")[1]
                 for role in ("viewer", "analyst", "manager", "admin")}
        h = users
        # viewers see no connection data at all; analysts read it; only admin+ manage it
        assert client.get(f"{G}/connections", headers=h["viewer"]).status_code == 403
        assert client.get(f"{G}/connections", headers=h["analyst"]).status_code == 200
        for role in ("viewer", "analyst", "manager"):
            assert client.post(f"{G}/connection/select", headers=h[role],
                               json={"customer_id": "3333333333"}).status_code == 403, role
            assert client.get(f"{G}/oauth/start", headers=h[role]).status_code == 403, role
            assert client.delete(f"{G}/connection", headers=h[role]).status_code == 403, role
        assert client.post(f"{G}/connection/select", headers=h["admin"], json={"customer_id": "3333333333"}).status_code == 200

    def test_ads_permissions_read_draft_publish_spend(self, client, world, link, google):
        link(world.a1, "google_ads", "1111111111", CIPHER_A)
        h = {role: world.member(world.a1, role, f"{role}-ads@example.com")[1]
             for role in ("viewer", "analyst", "manager", "admin")}
        h["owner"] = world.h_a1
        body = {"confirm": True}
        create = {"name": "n", "daily_budget_micros": 1_000_000, "confirm": True}
        # ads:read -> everybody
        assert all(client.get(f"{G}/campaigns", headers=h[r]).status_code == 200 for r in h)
        # ads:draft -> manager and up (campaign is always created PAUSED)
        assert [client.post(f"{G}/campaigns", headers=h[r], json=create).status_code
                for r in ("viewer", "analyst")] == [403, 403]
        assert client.post(f"{G}/campaigns", headers=h["manager"], json=create).status_code == 201
        # ads:publish -> admin and up
        assert client.post(f"{G}/campaigns/1/pause", headers=h["manager"], json=body).status_code == 403
        assert client.post(f"{G}/campaigns/1/pause", headers=h["admin"], json=body).status_code == 200
        assert client.post(f"{G}/campaigns/1/negative-keywords", headers=h["manager"],
                           json={"keywords": ["x"], "confirm": True}).status_code == 403
        # ads:spend -> owner only
        assert client.post(f"{G}/campaigns/1/enable", headers=h["admin"], json=body).status_code == 403
        google.enable.assert_not_awaited()

    def test_unauthenticated_and_foreign_organization_headers(self, client, world, link):
        link(world.a1, "google_ads", "1111111111", CIPHER_A)
        assert client.get(f"{G}/connections").status_code == 401
        foreign = {**world.h_b, "X-Organization-Id": world.a1.id}
        assert client.get(f"{G}/connections", headers=foreign).status_code == 403
        assert client.get(f"{G}/connections", headers={**world.h_a1, "X-Organization-Id": world.b.id}).status_code == 403


# --------------------------------------------------------------------------
# OAuth state: single use, bound to user + organization + browser
# --------------------------------------------------------------------------
class TestOAuthState:
    @pytest.fixture(autouse=True)
    def _provider(self, monkeypatch, google_ads_configured):
        from app.api.v1 import google_ads as mod
        self.exchange = AsyncMock(return_value={"refresh_token": "r-tok", "access_token": "a-tok"})
        self.customers = AsyncMock(return_value=["111-111-1111", "2222222222"])
        monkeypatch.setattr(mod, "exchange_code_for_tokens", self.exchange)
        monkeypatch.setattr(mod, "list_accessible_customers", self.customers)

    def start(self, client, headers):
        client.cookies.clear()
        r = client.get(f"{G}/oauth/start", headers=headers)
        assert r.status_code == 200, r.text
        return parse_qs(urlparse(r.json()["oauth_url"]).query)["state"][0]

    def callback(self, client, state, code="c0de"):
        return client.get(f"{G}/oauth/callback", params={"code": code, "state": state}, follow_redirects=False)

    def test_valid_flow_stores_candidates_only_for_the_starting_organization(self, client, world, db_session):
        state = self.start(client, world.h_a1)
        r = self.callback(client, state)
        assert r.status_code in (302, 307) and "select=1" in r.headers["location"]
        rows = db_session.query(GoogleAdsConnection).all()
        assert sorted(c.customer_id for c in rows) == ["1111111111", "2222222222"]
        assert {c.organization_id for c in rows} == {world.a1.id}
        assert all(c.is_active is False for c in rows)                       # several accounts: nothing auto-selected
        assert {l.status for l in db_session.query(AdAccountLink).all()} == {"pending"}
        assert client.get(f"{G}/connections", headers=world.h_a2).json() == {"connections": []}  # same user, other org

    def test_single_account_grant_is_activated_for_that_organization(self, client, world, db_session):
        self.customers.return_value = ["5555555555"]
        r = self.callback(client, self.start(client, world.h_a2))
        assert "connected=1" in r.headers["location"]
        link = db_session.query(AdAccountLink).one()
        assert (link.organization_id, link.status, link.authorized_by_user_id) == (world.a2.id, "active", world.alice.id)

    def test_tokens_are_stored_encrypted(self, client, world, db_session):
        self.callback(client, self.start(client, world.h_a1))
        stored = db_session.query(GoogleAdsConnection).first()
        assert "r-tok" not in stored.encrypted_refresh_token and "a-tok" not in stored.encrypted_access_token

    def test_replayed_state_is_refused_and_exchanges_nothing_twice(self, client, world):
        state = self.start(client, world.h_a1)
        assert self.callback(client, state).status_code in (302, 307)
        again = self.callback(client, state)
        assert again.status_code == 400 and "already used" in again.json()["detail"]
        assert self.exchange.await_count == 1

    def test_a_failed_exchange_still_consumes_the_state(self, client, world):
        from app.services.google_ads_oauth import GoogleOAuthError
        self.exchange.side_effect = GoogleOAuthError("boom")
        state = self.start(client, world.h_a1)
        assert self.callback(client, state).status_code == 400
        self.exchange.side_effect = None
        assert self.callback(client, state).status_code == 400
        assert self.exchange.await_count == 1

    def test_expired_state_is_refused(self, client, world, db_session):
        state = self.start(client, world.h_a1)
        db_session.execute(text("UPDATE oauth_states SET expires_at = now() - interval '1 minute'"))
        db_session.commit()
        assert self.callback(client, state).status_code == 400
        self.exchange.assert_not_awaited()

    def test_forged_tampered_and_foreign_provider_states_are_refused(self, client, world):
        import jwt
        from app.core.config import settings
        state = self.start(client, world.h_a1)
        payload = jwt.decode(state, settings.SECRET_KEY, algorithms=["HS256"])
        tampered = jwt.encode({**payload, "org": world.b.id}, "not-the-server-secret-0123456789abcdef", algorithm="HS256")
        other_provider = jwt.encode({**payload, "provider": "tiktok-ads"}, settings.SECRET_KEY, algorithm="HS256")
        for bad in ("garbage", tampered, other_provider, state[:-3] + "xyz"):
            assert self.callback(client, bad).status_code == 400
        self.exchange.assert_not_awaited()

    def test_resigned_state_naming_another_organization_matches_no_server_record(self, client, world, db_session):
        import jwt
        from app.core.config import settings
        state = self.start(client, world.h_a1)
        payload = jwt.decode(state, settings.SECRET_KEY, algorithms=["HS256"])
        # even an attacker who could sign (they cannot) is stopped by the server-side record
        forged = jwt.encode({**payload, "org": world.b.id}, settings.SECRET_KEY, algorithm="HS256")
        assert self.callback(client, forged).status_code == 400
        assert db_session.query(GoogleAdsConnection).count() == 0
        assert self.callback(client, state).status_code in (302, 307)           # the genuine one still works once

    def test_state_without_or_with_a_different_browser_cookie_is_refused(self, client, world):
        state = self.start(client, world.h_a1)
        client.cookies.clear()
        assert self.callback(client, state).status_code == 400                   # no cookie
        state = self.start(client, world.h_a1)
        client.cookies.set("oauth_state", "someone-elses-cookie")
        assert self.callback(client, state).status_code == 400                   # other browser
        self.exchange.assert_not_awaited()

    def test_organization_switched_by_losing_membership_is_refused(self, client, world, db_session):
        from app.models import OrganizationMember
        state = self.start(client, world.h_a1)
        db_session.query(OrganizationMember).filter(
            OrganizationMember.organization_id == world.a1.id,
            OrganizationMember.user_id == world.alice.id).update({"status": "revoked"})
        db_session.commit()
        r = self.callback(client, state)
        assert r.status_code == 403
        assert db_session.query(GoogleAdsConnection).count() == 0
        denied = db_session.query(AuditLog).filter(AuditLog.action == "oauth.callback_denied").one()
        assert denied.organization_id == world.a1.id and denied.actor_user_id == world.alice.id

    def test_role_downgraded_between_start_and_callback_is_refused(self, client, world, db_session):
        from app.models import OrganizationMember
        member, headers = world.member(world.a1, "admin", "downgrade@example.com")
        state = self.start(client, headers)
        db_session.query(OrganizationMember).filter(OrganizationMember.user_id == member.id).update({"role": "viewer"})
        db_session.commit()
        assert self.callback(client, state).status_code == 403

    def test_state_started_in_one_organization_cannot_be_completed_in_another(self, client, world, db_session):
        state = self.start(client, world.h_a1)                                    # alice, org A1
        assert self.callback(client, state).status_code in (302, 307)
        assert {c.organization_id for c in db_session.query(GoogleAdsConnection)} == {world.a1.id}
        assert db_session.query(GoogleAdsConnection).filter(GoogleAdsConnection.organization_id == world.a2.id).count() == 0

    def test_concurrent_callbacks_consume_the_state_exactly_once(self, client, world):
        from app.core.oauth_state import consume_oauth_state, create_oauth_state
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            token, cookie = create_oauth_state(db, world.alice.id, world.a1.id, "google-ads")
        finally:
            db.close()
        results = []

        def attempt(_):
            session = SessionLocal()
            try:
                consume_oauth_state(session, token, "google-ads", cookie)
                results.append("ok")
            except ValueError:
                results.append("refused")
            finally:
                session.close()

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(attempt, range(8)))
        assert results.count("ok") == 1 and results.count("refused") == 7

    def test_start_requires_manage_permission_and_authentication(self, client, world):
        assert client.get(f"{G}/oauth/start").status_code == 401
        _, viewer = world.member(world.a1, "manager", "mgr-oauth@example.com")
        assert client.get(f"{G}/oauth/start", headers=viewer).status_code == 403

    def test_oauth_state_rows_are_server_side_and_hash_the_cookie(self, client, world, db_session):
        self.start(client, world.h_a1)
        row = db_session.query(text("1")).first() if False else db_session.execute(
            text("SELECT cookie_hash, used_at, organization_id FROM oauth_states")).one()
        cookie = client.cookies.get("oauth_state")
        assert cookie and cookie not in row.cookie_hash
        assert row.cookie_hash == hashlib.sha256(cookie.encode()).hexdigest()
        assert row.used_at is None and row.organization_id == world.a1.id


class TestMCC:
    def test_manager_account_is_per_organization_and_has_no_server_wide_fallback(
            self, client, world, link, google, monkeypatch):
        from app.core.config import settings
        from app.services import google_ads_service
        monkeypatch.setattr(settings, "GOOGLE_ADS_LOGIN_CUSTOMER_ID", "9999999999")   # must be ignored
        link(world.a1, "google_ads", "1111111111", CIPHER_A)
        link(world.a1, "google_ads", "7777777777", CIPHER_A, selected=False, status="pending")
        link(world.b, "google_ads", "2222222222", CIPHER_B)
        seen = []

        async def spy(*args, **kwargs):
            seen.append(google_ads_service._login_customer_id.get())
            return []

        from app.api.v1 import google_ads as mod
        monkeypatch.setattr(mod, "get_campaign_performance", spy)
        assert client.put(f"{G}/connection/mcc", headers=world.h_a1, json={"login_customer_id": "777-777-7777"}).status_code == 200
        client.get(f"{G}/campaigns", headers=world.h_a1)
        client.get(f"{G}/campaigns", headers=world.h_b)
        assert seen == ["7777777777", None]

    def test_mcc_must_belong_to_the_organizations_own_grant(self, client, world, link, google):
        link(world.a1, "google_ads", "1111111111", CIPHER_A)
        link(world.b, "google_ads", "4444444444", CIPHER_B)
        r = client.put(f"{G}/connection/mcc", headers=world.h_a1, json={"login_customer_id": "4444444444"})
        assert r.status_code == 404
        assert client.get(f"{G}/connection", headers=world.h_a1).json()["login_customer_id"] is None

    def test_selecting_an_individual_account_keeps_other_candidates_pending(self, client, world, link):
        link(world.a1, "google_ads", "1111111111", CIPHER_A, selected=False, status="pending")
        link(world.a1, "google_ads", "2222222222", CIPHER_A, selected=False, status="pending")
        r = client.post(f"{G}/connection/select", headers=world.h_a1, json={"customer_id": "2222222222"})
        assert r.status_code == 200 and r.json()["customer_id"] == "2222222222"
        listed = {c["customer_id"]: c["selected"] for c in client.get(f"{G}/connections", headers=world.h_a1).json()["connections"]}
        assert listed == {"1111111111": False, "2222222222": True}


# --------------------------------------------------------------------------
# Spend: human approval, budget scope, idempotency; fail closed
# --------------------------------------------------------------------------
class TestSpendControls:
    @pytest.fixture
    def acct(self, world, link):
        link(world.a1, "google_ads", "1111111111", CIPHER_A)
        return "1111111111"

    def request(self, client, headers, key="k-1", budget=5_000_000, campaign="1", action="campaign.enable"):
        return client.post(f"{OPT}/approvals", headers=headers, json={
            "provider": "google_ads", "account_id": "1111111111", "action": action,
            "payload": {"campaign_id": campaign}, "budget_micros": budget, "idempotency_key": key})

    def approved(self, client, world, key="k-1", budget=5_000_000, scope=6_000_000, campaign="1"):
        approval = self.request(client, world.h_a1, key, budget, campaign).json()
        assert client.post(f"{OPT}/approvals/{approval['id']}/approve", headers=world.h_a1,
                           json={"max_budget_micros": scope}).status_code == 200
        return approval["id"]

    def enable(self, client, headers, approval_id=None, campaign="1"):
        return client.post(f"{G}/campaigns/{campaign}/enable", headers=headers,
                           json={"confirm": True, "approval_id": approval_id})

    def test_enabling_without_any_approval_fails_closed(self, client, world, acct, google, db_session):
        r = self.enable(client, world.h_a1)
        assert r.status_code == 403 and "approval" in r.json()["detail"]
        google.enable.assert_not_awaited()
        assert db_session.query(SpendApproval).count() == 0

    def test_enabling_with_an_unknown_or_pending_approval_fails_closed(self, client, world, acct, google):
        assert self.enable(client, world.h_a1, "nope").status_code == 403
        pending = self.request(client, world.h_a1).json()["id"]
        r = self.enable(client, world.h_a1, pending)
        assert r.status_code == 403 and "pending" in r.json()["detail"]
        google.enable.assert_not_awaited()

    def test_full_flow_executes_once_and_replays_idempotently(self, client, world, acct, google, db_session):
        approval_id = self.approved(client, world)
        first = self.enable(client, world.h_a1, approval_id)
        assert first.status_code == 200 and first.json()["status"] == "ENABLED"
        second = self.enable(client, world.h_a1, approval_id)
        assert second.status_code == 200 and second.json()["idempotent_replay"] is True
        assert google.enable.await_count == 1                                  # spent ONCE
        row = db_session.query(SpendApproval).one()
        assert row.status == "consumed" and row.result["status"] == "ENABLED"
        reasons = [a.verdict_reason for a in db_session.query(MutationAudit).order_by(MutationAudit.created_at)]
        assert reasons == ["approval-requested", "approved", "executed"]
        assert all(a.organization_id == world.a1.id for a in db_session.query(MutationAudit))

    def test_budget_scope_is_checked_against_the_real_campaign_budget(self, client, world, acct, google):
        approval_id = self.approved(client, world, budget=1_000_000, scope=1_000_000)
        google.budget.return_value = 50_000_000                                # the campaign really spends more
        r = self.enable(client, world.h_a1, approval_id)
        assert r.status_code == 403 and "budget scope" in r.json()["detail"]
        google.enable.assert_not_awaited()

    def test_budget_that_cannot_be_verified_fails_closed(self, client, world, acct, google):
        from app.services.google_ads_service import GoogleAdsConnectionError
        approval_id = self.approved(client, world)
        google.budget.side_effect = GoogleAdsConnectionError("down")
        assert self.enable(client, world.h_a1, approval_id).status_code == 502
        google.enable.assert_not_awaited()

    def test_approval_is_bound_to_the_exact_campaign(self, client, world, acct, google):
        approval_id = self.approved(client, world, campaign="1")
        assert self.enable(client, world.h_a1, approval_id, campaign="2").status_code == 403
        google.enable.assert_not_awaited()

    def test_approval_of_another_organization_is_useless(self, client, world, link, acct, google):
        link(world.a2, "google_ads", "1111111111", CIPHER_A)
        approval_id = self.approved(client, world)
        r = self.enable(client, world.h_a2, approval_id)                       # same user, other organization
        assert r.status_code == 403
        assert client.get(f"{OPT}/approvals/{approval_id}", headers=world.h_a2).status_code == 404
        google.enable.assert_not_awaited()

    def test_expired_approval_is_refused(self, client, world, acct, google, db_session):
        approval_id = self.approved(client, world)
        db_session.execute(text("UPDATE spend_approvals SET expires_at = now() - interval '1 minute'"))
        db_session.commit()
        assert self.enable(client, world.h_a1, approval_id).status_code == 403
        google.enable.assert_not_awaited()

    def test_a_failed_platform_call_does_not_allow_a_blind_retry(self, client, world, acct, google, db_session):
        from app.services.google_ads_service import GoogleAdsConnectionError
        approval_id = self.approved(client, world)
        google.enable.side_effect = GoogleAdsConnectionError("timeout")
        assert self.enable(client, world.h_a1, approval_id).status_code == 502
        google.enable.side_effect = None
        again = self.enable(client, world.h_a1, approval_id)
        assert again.status_code == 409 and "unknown" in again.json()["detail"]
        assert google.enable.await_count == 1
        assert db_session.query(SpendApproval).one().status == "consumed"

    def test_rejected_approval_never_executes(self, client, world, acct, google):
        approval = self.request(client, world.h_a1).json()
        assert client.post(f"{OPT}/approvals/{approval['id']}/reject", headers=world.h_a1, json={"reason": "no"}).status_code == 200
        assert self.enable(client, world.h_a1, approval["id"]).status_code == 403
        google.enable.assert_not_awaited()

    def test_approving_needs_a_stated_budget_scope_at_least_the_requested_budget(self, client, world, acct):
        approval = self.request(client, world.h_a1, budget=5_000_000).json()
        url = f"{OPT}/approvals/{approval['id']}/approve"
        assert client.post(url, headers=world.h_a1, json={}).status_code == 422
        assert client.post(url, headers=world.h_a1, json={"max_budget_micros": 1_000_000}).status_code == 422
        assert client.post(url, headers=world.h_a1, json={"max_budget_micros": 5_000_000}).status_code == 200
        assert client.post(url, headers=world.h_a1, json={"max_budget_micros": 5_000_000}).status_code == 409  # decided once

    def test_idempotency_key_returns_the_same_approval_and_rejects_a_different_operation(self, client, world, acct):
        a = self.request(client, world.h_a1, key="same").json()
        b = self.request(client, world.h_a1, key="same").json()
        assert a["id"] == b["id"]
        assert self.request(client, world.h_a1, key="same", campaign="2").status_code == 409
        assert client.post(f"{OPT}/approvals", headers=world.h_a1, json={
            "provider": "google_ads", "account_id": "1111111111", "action": "campaign.enable",
            "payload": {}, "idempotency_key": ""}).status_code == 400

    def test_requests_need_a_linked_account_and_draft_permission(self, client, world, acct):
        _, viewer = world.member(world.a1, "viewer", "viewer-spend@example.com")
        _, admin = world.member(world.a1, "admin", "admin-spend@example.com")
        assert self.request(client, viewer).status_code == 403
        assert self.request(client, admin, key="adm").status_code == 201
        unlinked = client.post(f"{OPT}/approvals", headers=world.h_a1, json={
            "provider": "google_ads", "account_id": "5555555555", "action": "campaign.enable",
            "payload": {}, "idempotency_key": "x"})
        assert unlinked.status_code == 403
        assert client.post(f"{OPT}/approvals/{self.request(client, world.h_a1, key='y').json()['id']}/approve",
                           headers=admin, json={"max_budget_micros": 9_000_000}).status_code == 403  # admin has no ads:spend

    def test_only_a_human_with_spend_permission_can_approve_a_bot_cannot(self, client, world, acct, db_session):
        raw = self.make_key(db_session, world.a1, ["ads:read", "ads:draft"])
        approval = client.post(f"{BOT}/approvals", headers={"Authorization": f"Bearer {raw}"}, json={
            "provider": "google_ads", "account_id": "1111111111", "action": "campaign.enable",
            "payload": {"campaign_id": "1"}, "budget_micros": 100, "idempotency_key": "bot-1"})
        assert approval.status_code == 201, approval.text
        row = db_session.query(SpendApproval).one()
        assert row.requested_by_api_key_id and row.requested_by_user_id is None and row.status == "pending"
        # a bearer API key is not a user session: the approve route refuses it
        r = client.post(f"{OPT}/approvals/{row.id}/approve", headers={"Authorization": f"Bearer {raw}"},
                        json={"max_budget_micros": 1000})
        assert r.status_code == 401
        db_session.expire_all()
        assert db_session.query(SpendApproval).one().status == "pending"

    def make_key(self, db_session, org, scopes):
        raw = "ads_studio_t_" + secrets.token_urlsafe(24)
        db_session.add(ApiKey(organization_id=org.id, name="bot", key_hash=hashlib.sha256(raw.encode()).hexdigest(),
                              scopes=scopes))
        db_session.commit()
        return raw

    def test_concurrent_executions_of_one_approval_spend_it_exactly_once(self, client, world, acct, db_session):
        from fastapi import HTTPException
        from app.database import SessionLocal, bind_context
        from app.services import approvals
        approval_id = self.approved(client, world)
        org_id, user_id = world.a1.id, world.alice.id
        outcomes = []

        def attempt(_):
            db = SessionLocal()
            try:
                bind_context(db, org_id)
                ctx = SimpleNamespace(org=SimpleNamespace(id=org_id), user=SimpleNamespace(id=user_id))
                approvals.consume(db, ctx, approval_id=approval_id, provider="google_ads", account_id="1111111111",
                                  action="campaign.enable", payload={"campaign_id": "1"}, budget_micros=5_000_000)
                outcomes.append("spent")
            except HTTPException as exc:
                outcomes.append(exc.status_code)
            finally:
                db.close()

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(attempt, range(8)))
        assert outcomes.count("spent") == 1 and outcomes.count(403) == 7

    def test_meta_active_creation_needs_spend_permission_budget_and_approval(self, client, world, link, mock_facebook_service):
        link(world.a1, "meta", "act_555", CIPHER_A)
        mock_facebook_service.ad_account_id = "act_555"
        mock_facebook_service.create_campaign.return_value = {"id": "c-1"}
        paused = client.post(f"{F}/campaigns", headers=world.h_a1, json={"name": "n", "objective": "X"})
        assert paused.status_code == 200
        assert mock_facebook_service.create_campaign.call_args.args[0]["status"] == "PAUSED"   # default draft
        mock_facebook_service.create_campaign.reset_mock()
        active = {"name": "n", "objective": "X", "status": "ACTIVE", "budgetType": "CBO", "dailyBudget": 2000}
        assert client.post(f"{F}/campaigns", headers=world.h_a1, json=active).status_code == 403   # no approval
        assert client.post(f"{F}/campaigns", headers=world.h_a1,
                           json={**active, "dailyBudget": None}).status_code == 400             # no explicit budget
        _, manager = world.member(world.a1, "manager", "mgr-meta@example.com")
        assert client.post(f"{F}/campaigns", headers=manager, json=active).status_code == 403   # no ads:spend
        mock_facebook_service.create_campaign.assert_not_called()
        # approved: 2000 cents == 20_000_000 micros
        approval = client.post(f"{OPT}/approvals", headers=world.h_a1, json={
            "provider": "meta", "account_id": "act_555", "action": "campaign.create.active",
            "payload": {"kind": "campaign", **active}, "budget_micros": 20_000_000, "idempotency_key": "m-1"}).json()
        client.post(f"{OPT}/approvals/{approval['id']}/approve", headers=world.h_a1, json={"max_budget_micros": 20_000_000})
        ok = client.post(f"{F}/campaigns", headers=world.h_a1, json={**active, "approval_id": approval["id"]})
        assert ok.status_code == 200, ok.text
        sent = mock_facebook_service.create_campaign.call_args.args[0]
        assert "approval_id" not in sent and "_approval" not in sent and sent["status"] == "ACTIVE"
        reuse = client.post(f"{F}/campaigns", headers=world.h_a1, json={**active, "approval_id": approval["id"]})
        assert reuse.status_code == 403                                                          # single use
        assert mock_facebook_service.create_campaign.call_count == 1

    def test_meta_ads_cannot_be_created_active_at_all(self, client, world, link, mock_facebook_service):
        link(world.a1, "meta", "act_555", CIPHER_A)
        mock_facebook_service.object_account_id.return_value = "555"
        r = client.post(f"{F}/ads", headers=world.h_a1, json={"adset_id": "s", "name": "a", "status": "ACTIVE"})
        assert r.status_code == 403
        mock_facebook_service.create_ad.assert_not_called()

    def test_tiktok_campaigns_are_drafts_gated_by_ads_draft(self, client, world, link, monkeypatch):
        from app.api.v1 import tiktok_ads as mod
        from app.core.config import settings
        monkeypatch.setattr(settings, "TIKTOK_ADS_APP_ID", "id")
        monkeypatch.setattr(settings, "TIKTOK_ADS_APP_SECRET", "secret")
        create = AsyncMock(return_value={"campaign_id": "t1"})
        monkeypatch.setattr(mod, "create_tiktok_campaign", create)
        monkeypatch.setattr(mod, "get_valid_access_token", AsyncMock(return_value="tok"))
        link(world.a1, "tiktok", "tt-1", CIPHER_A)
        body = {"name": "n", "daily_budget": 5, "confirm": True}
        _, analyst = world.member(world.a1, "analyst", "an-tt@example.com")
        assert client.post(f"{T}/campaigns", headers=analyst, json=body).status_code == 403
        assert client.post(f"{T}/campaigns", headers=world.h_b, json=body).status_code == 404   # B has no advertiser
        assert client.post(f"{T}/campaigns", headers=world.h_a1, json=body).status_code == 201
        assert create.await_args.args[1] == "tt-1"


# --------------------------------------------------------------------------
# API keys and the bot
# --------------------------------------------------------------------------
class TestApiKeysAndBots:
    def mint(self, client, headers, org, scopes=("ads:read",), name="bot"):
        return client.post(f"/api/v1/organizations/{org.id}/api-keys", headers=headers,
                           json={"name": name, "scopes": list(scopes)})

    def test_key_is_shown_once_and_only_its_hash_is_stored(self, client, world, db_session):
        r = self.mint(client, world.h_a1, world.a1)
        assert r.status_code == 201
        raw = r.json()["key"]
        stored = db_session.query(ApiKey).one()
        assert stored.key_hash == hashlib.sha256(raw.encode()).hexdigest() and raw not in str(vars(stored))
        listed = client.get(f"/api/v1/organizations/{world.a1.id}/api-keys", headers=world.h_a1).json()
        assert "key" not in listed[0] and "key_hash" not in listed[0] and raw not in str(listed)

    def test_keys_can_never_publish_or_spend(self, client, world):
        for scope in ("ads:publish", "ads:spend", "ads:write", "admin"):
            assert self.mint(client, world.h_a1, world.a1, scopes=["ads:read", scope]).status_code == 422
        assert self.mint(client, world.h_a1, world.a1, scopes=[]).status_code == 422

    def test_only_admins_manage_keys_and_only_of_their_own_organization(self, client, world):
        _, manager = world.member(world.a1, "manager", "mgr-key@example.com")
        assert self.mint(client, manager, world.a1).status_code == 403
        assert self.mint(client, world.h_b, world.a1).status_code == 403         # B's owner, A's organization
        key = self.mint(client, world.h_a1, world.a1).json()
        assert client.get(f"/api/v1/organizations/{world.a1.id}/api-keys", headers=world.h_b).status_code == 403
        assert client.delete(f"/api/v1/organizations/{world.b.id}/api-keys/{key['id']}", headers=world.h_b).status_code == 404

    def test_a_key_acts_only_inside_its_organization(self, client, world, link, monkeypatch):
        link(world.a1, "google_ads", "1111111111", CIPHER_A)
        link(world.b, "google_ads", "2222222222", CIPHER_B)
        key_a = self.mint(client, world.h_a1, world.a1).json()["key"]
        key_b = self.mint(client, world.h_b, world.b).json()["key"]
        seen = {k: client.get(f"{BOT}/connections", headers={"Authorization": f"Bearer {k}"}).json()["google_ads"]
                for k in (key_a, key_b)}
        assert [c["customer_id"] for c in seen[key_a]] == ["1111111111"]
        assert [c["customer_id"] for c in seen[key_b]] == ["2222222222"]
        assert "SECRET" not in str(seen)

    def test_bot_overview_is_computed_for_the_keys_organization(self, client, world, monkeypatch):
        from app.api.v1 import bot as mod
        build = AsyncMock(return_value={"campaigns": [], "errors": {}})
        monkeypatch.setattr(mod, "build_overview", build)
        key = self.mint(client, world.h_a2, world.a2).json()["key"]
        assert client.get(f"{BOT}/overview", headers={"Authorization": f"Bearer {key}"}).status_code == 200
        assert build.await_args.args[1] == world.a2.id

    def test_bot_scopes_and_unauthenticated_access(self, client, world):
        key = self.mint(client, world.h_a1, world.a1, scopes=["ads:read"]).json()["key"]
        h = {"Authorization": f"Bearer {key}"}
        assert client.get(f"{BOT}/safety", headers=h).status_code == 200
        assert client.post(f"{BOT}/approvals", headers=h, json={
            "provider": "google_ads", "account_id": "1", "action": "campaign.enable",
            "payload": {}, "idempotency_key": "k"}).status_code == 403                # key lacks ads:draft
        assert client.get(f"{BOT}/safety", headers={"Authorization": "Bearer nope"}).status_code == 401
        assert client.get(f"{BOT}/safety", headers=world.h_a1).status_code == 401    # a JWT is not an API key

    def test_revoked_key_and_key_of_an_inactive_organization_are_refused(self, client, world, db_session):
        key = self.mint(client, world.h_a1, world.a1).json()
        h = {"Authorization": f"Bearer {key['key']}"}
        assert client.get(f"{BOT}/safety", headers=h).status_code == 200
        assert client.delete(f"/api/v1/organizations/{world.a1.id}/api-keys/{key['id']}", headers=world.h_a1).status_code == 200
        assert client.get(f"{BOT}/safety", headers=h).status_code == 401
        key2 = self.mint(client, world.h_a2, world.a2).json()["key"]
        db_session.execute(text("UPDATE organizations SET is_active = false WHERE id = :o"), {"o": world.a2.id})
        db_session.commit()
        assert client.get(f"{BOT}/safety", headers={"Authorization": f"Bearer {key2}"}).status_code == 401

    def test_revoking_a_member_revokes_the_keys_they_minted(self, client, world, db_session, add_member):
        member, headers = world.member(world.a1, "admin", "leaver@example.com")
        key = self.mint(client, headers, world.a1).json()["key"]
        assert client.delete(f"/api/v1/organizations/{world.a1.id}/members/{member.id}", headers=world.h_a1).status_code == 200
        assert client.get(f"{BOT}/safety", headers={"Authorization": f"Bearer {key}"}).status_code == 401

    def test_a_legacy_key_without_organization_authenticates_nobody(self, client, db_session):
        raw = "ads_studio_legacy_" + secrets.token_urlsafe(16)
        db_session.execute(text("ALTER TABLE api_keys DROP CONSTRAINT ck_api_keys_org_required"))
        try:
            db_session.execute(text("INSERT INTO api_keys (id, name, key_hash, scopes) VALUES (:i, 'legacy', :h, '[\"ads:read\"]')"),
                               {"i": str(uuid.uuid4()), "h": hashlib.sha256(raw.encode()).hexdigest()})
            db_session.commit()
            r = client.get(f"{BOT}/safety", headers={"Authorization": f"Bearer {raw}"})
            assert r.status_code == 403 and "not bound" in r.json()["detail"]
        finally:
            db_session.execute(text("DELETE FROM api_keys WHERE organization_id IS NULL"))
            db_session.execute(text("ALTER TABLE api_keys ADD CONSTRAINT ck_api_keys_org_required "
                                    "CHECK (organization_id IS NOT NULL) NOT VALID"))
            db_session.commit()

    def test_database_hides_other_organizations_keys_and_only_yields_the_presented_hash(self, client, world, db_session):
        from app.database import SessionLocal, bind_context
        ka = self.mint(client, world.h_a1, world.a1).json()["key"]
        self.mint(client, world.h_b, world.b)
        db = SessionLocal()
        try:
            bind_context(db, world.b.id)
            assert db.execute(text("SELECT count(*) FROM api_keys")).scalar() == 1             # only B's own
            db.rollback()
            assert db.execute(text("SELECT count(*) FROM api_keys")).scalar() == 1
            db.rollback()
            bind_context(db, None)
            assert db.execute(text("SELECT count(*) FROM api_keys")).scalar() == 0             # no context: nothing
            db.execute(text("SELECT set_config('app.api_key_hash', :h, true)"),
                       {"h": hashlib.sha256(ka.encode()).hexdigest()})
            assert db.execute(text("SELECT count(*) FROM api_keys")).scalar() == 1             # exactly the presented one
        finally:
            db.rollback()
            db.close()


# --------------------------------------------------------------------------
# Audits, overview and RLS on the new tables
# --------------------------------------------------------------------------
class TestAuditsAndReports:
    def test_mutation_audits_are_per_organization(self, client, world, link, db_session, monkeypatch):
        monkeypatch.setenv("PROPOSAL_TOKEN_SECRET", "unit-test-proposal-secret-0123456789abcdef")
        link(world.a1, "google_ads", "1111111111", CIPHER_A)
        body = {"provider": "google_ads", "account_id": "1111111111", "action": "campaign.pause", "payload": {"campaign_id": "1"}}
        assert client.post(f"{OPT}/propose", headers=world.h_a1, json=body).status_code == 200
        assert client.post(f"{OPT}/propose", headers=world.h_b, json=body).status_code == 403     # B has no link to it
        assert db_session.query(MutationAudit).one().organization_id == world.a1.id
        from app.database import SessionLocal, bind_context
        db = SessionLocal()
        try:
            bind_context(db, world.b.id)
            assert db.execute(text("SELECT count(*) FROM mutation_audits")).scalar() == 0
        finally:
            db.close()

    def test_overview_of_one_organization_never_uses_anothers_connection(self, client, world, link, monkeypatch):
        from app.api.v1 import overview as mod
        link(world.a1, "meta", "act_1350206440591360", CIPHER_A)
        captured = []

        class Fake:
            def __init__(self, **kw):
                captured.append(kw)
                self.api, self.ad_account_id = None, kw.get("ad_account_id")

            def initialize(self):
                self.api = object()

            def get_campaigns(self, a=None):
                return []

        monkeypatch.setattr(mod, "FacebookService", Fake)
        monkeypatch.setattr(mod, "decrypt_token", lambda blob: f"plain:{blob}")
        assert client.get("/api/v1/overview", headers=world.h_a1).status_code == 200
        body = client.get("/api/v1/overview", headers=world.h_b).json()
        assert [c["access_token"] for c in captured] == [f"plain:{CIPHER_A}"]            # B made no fetch
        assert "meta" in body["errors"] and body["campaigns"] == []

    def test_every_new_table_returns_nothing_for_another_organization(self, client, world, link, db_session):
        from app.database import SessionLocal, bind_context
        link(world.a1, "google_ads", "1111111111", CIPHER_A)
        link(world.a1, "meta", "act_1", CIPHER_A)
        link(world.a1, "tiktok", "t1", CIPHER_A)
        db_session.add(SpendApproval(organization_id=world.a1.id, provider="meta", account_id="1", action="campaign.enable",
                                     payload_hash="h", idempotency_key="i", expires_at=db_session.execute(text("SELECT now() + interval '1 hour'")).scalar()))
        db_session.add(MutationAudit(organization_id=world.a1.id, provider="meta", account_id="1", action="x",
                                     payload_hash="h", mode="manual"))
        db_session.commit()
        for org, expected, links in ((world.a1, 1, 3), (world.b, 0, 0)):
            db = SessionLocal()
            try:
                bind_context(db, org.id)
                for table in ("google_ads_connections", "meta_ads_connections", "tiktok_ads_connections",
                              "ad_account_links", "spend_approvals", "mutation_audits"):
                    count = db.execute(text(f"SELECT count(*) FROM {table}")).scalar()
                    assert count == (links if table == "ad_account_links" else expected), (table, org.id)
            finally:
                db.close()

    def test_database_refuses_a_connection_or_link_for_the_wrong_organization(self, world):
        from app.database import SessionLocal, bind_context
        db = SessionLocal()
        try:
            bind_context(db, world.b.id)
            db.add(AdAccountLink(organization_id=world.a1.id, provider="meta", external_account_id="1", status="active"))
            with pytest.raises(Exception, match="row-level security|Cross-organization"):
                db.commit()
        finally:
            db.rollback()
            db.close()

    def test_concurrent_requests_of_two_organizations_never_mix_connections(self, client, world, link):
        link(world.a1, "google_ads", "1111111111", CIPHER_A)
        link(world.b, "google_ads", "2222222222", CIPHER_B)
        errors = []

        def work(i):
            headers, expected = ((world.h_a1, "1111111111"), (world.h_b, "2222222222"))[i % 2]
            for _ in range(6):
                got = client.get(f"{G}/connection", headers=headers).json().get("customer_id")
                if got != expected:
                    errors.append((i, got))

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(work, range(16)))
        assert errors == []


# --------------------------------------------------------------------------
# Secure database mode (dedicated login role, no RESET ROLE dependency)
# --------------------------------------------------------------------------
class TestSecureDatabaseMode:
    def test_production_without_a_runtime_login_url_refuses_to_start(self, monkeypatch):
        from app.core.config import settings
        if settings.RUNTIME_DATABASE_URL:
            pytest.skip("this run already uses RUNTIME_DATABASE_URL (secure-mode homologation)")
        from app.database import verify_runtime_role_is_safe
        monkeypatch.setattr(settings, "APP_ENV", "production")
        assert settings.secure_db_mode_required is True
        with pytest.raises(RuntimeError, match="RUNTIME_DATABASE_URL"):
            verify_runtime_role_is_safe()

    def test_railway_environment_counts_as_production(self, monkeypatch):
        from app.core.config import settings
        monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
        assert settings.is_production is True and settings.secure_db_mode_required is True

    def test_development_default_does_not_force_it(self):
        from app.core.config import settings
        if settings.is_production:
            pytest.skip("APP_ENV=production in this run (secure-mode homologation)")
        assert settings.APP_ENV == "development" and settings.secure_db_mode_required is False

    def test_provisioned_login_role_has_no_privilege_and_cannot_reset_role(self, db_session, monkeypatch):
        from sqlalchemy import create_engine
        from sqlalchemy.engine import make_url
        from app.core.config import settings
        from app.database import verify_runtime_role_is_safe
        from app.scripts.provision_runtime_login import provision
        name = f"ads_login_{uuid.uuid4().hex[:6]}"
        try:
            provision(db_session.connection(), name, settings.DB_RUNTIME_ROLE, "a-very-strong-password-123")
            db_session.commit()
            attrs = db_session.execute(text(
                "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolcanlogin FROM pg_roles WHERE rolname = :r"),
                {"r": name}).one()
            assert tuple(attrs) == (False, False, False, False, True)
            engine = create_engine(make_url(settings.DATABASE_URL).set(username=name, password="a-very-strong-password-123"))
            monkeypatch.setattr(settings, "APP_ENV", "production")
            try:
                verify_runtime_role_is_safe(engine)                                        # secure mode passes
                with engine.connect() as conn:
                    conn.execute(text("RESET ROLE"))                                        # nothing to escape to
                    assert conn.execute(text("SELECT current_user, session_user")).one() == (name, name)
                    for sql in ("SET ROLE ads_studio_test", "SET ROLE postgres"):
                        with pytest.raises(Exception, match="permission denied|must be member|does not exist"):
                            conn.execute(text(sql))
                        conn.rollback()
            finally:
                engine.dispose()
        finally:
            db_session.execute(text(f"DROP OWNED BY {name}"))
            db_session.execute(text(f"DROP ROLE IF EXISTS {name}"))
            db_session.commit()

    def test_secure_mode_rejects_a_login_that_is_a_member_of_a_privileged_role(self, db_session, monkeypatch):
        from sqlalchemy import create_engine
        from sqlalchemy.engine import make_url
        from app.core.config import settings
        from app.database import verify_runtime_role_is_safe
        name = f"ads_bad_{uuid.uuid4().hex[:6]}"
        db_session.execute(text(f"CREATE ROLE {name} LOGIN PASSWORD 'pw-pw-pw-pw-pw-pw' NOSUPERUSER NOBYPASSRLS"))
        db_session.execute(text(f"GRANT {settings.DB_RUNTIME_ROLE} TO {name}"))
        db_session.execute(text(f"GRANT ads_studio_test TO {name}"))      # the superuser owner: can SET ROLE to it
        db_session.commit()
        engine = create_engine(make_url(settings.DATABASE_URL).set(username=name, password="pw-pw-pw-pw-pw-pw"))
        try:
            monkeypatch.setattr(settings, "APP_ENV", "production")
            with pytest.raises(RuntimeError, match="member of privileged roles"):
                verify_runtime_role_is_safe(engine)
        finally:
            engine.dispose()
            db_session.execute(text(f"DROP OWNED BY {name}"))
            db_session.execute(text(f"DROP ROLE IF EXISTS {name}"))
            db_session.commit()

    def test_provisioning_refuses_weak_passwords_and_bad_names(self, db_session):
        from app.scripts.provision_runtime_login import provision
        with pytest.raises(ValueError):
            provision(db_session.connection(), "ok_name", "ads_app", "short")
        with pytest.raises(ValueError):
            provision(db_session.connection(), "bad name;drop", "ads_app", "a-very-strong-password-123")
        db_session.rollback()


# --------------------------------------------------------------------------
# SSRF guard for server-fetched URLs
# --------------------------------------------------------------------------
class TestExternalUrlGuard:
    @pytest.mark.parametrize("url", [
        "http://127.0.0.1/x.png", "http://localhost/x.png", "http://169.254.169.254/latest/meta-data",
        "http://10.0.0.5/x", "http://192.168.1.1/x", "http://[::1]/x", "http://172.16.0.1/x",
        "http://user:pw@example.com/x", "ftp://example.com/x", "gopher://example.com/x", "http:///nohost",
    ])
    def test_internal_or_odd_urls_are_refused_before_any_fetch(self, client, world, link, mock_facebook_service, monkeypatch, url):
        from app.core import storage
        link(world.a1, "meta", "act_1", CIPHER_A)
        monkeypatch.setattr(storage, "_resolve_host", lambda host: ["127.0.0.1"] if host == "localhost" else ["93.184.216.34"])
        r = client.post(f"{F}/upload-image", headers=world.h_a1, json={"image_url": url})
        assert r.status_code == 400
        mock_facebook_service.upload_image.assert_not_called()

    def test_a_hostname_that_resolves_to_a_private_address_is_refused(self, client, world, link, mock_facebook_service, monkeypatch):
        from app.core import storage
        link(world.a1, "meta", "act_1", CIPHER_A)
        monkeypatch.setattr(storage, "_resolve_host", lambda host: ["93.184.216.34", "10.1.2.3"])
        assert client.post(f"{F}/upload-video", headers=world.h_a1, json={"video_url": "https://cdn.example.com/v.mp4"}).status_code == 400

    def test_public_urls_pass(self, client, world, link, mock_facebook_service):
        link(world.a1, "meta", "act_1", CIPHER_A)
        mock_facebook_service.upload_image.return_value = "h"
        assert client.post(f"{F}/upload-image", headers=world.h_a1, json={"image_url": "https://example.com/x.png"}).status_code == 200
