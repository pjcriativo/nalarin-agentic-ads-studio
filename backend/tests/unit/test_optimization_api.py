"""HTTP-level tests for /api/v1/optimization: routing, trust boundaries and
the 'no fake autonomy' guarantees."""
import secrets

import pytest
from fastapi import status

from app.core.deps import _hash_api_key
from app.core.security import get_password_hash
from app.models import (
    ApiKey,
    GoogleAdsConnection,
    MetaAdsConnection,
    MutationAudit,
    TikTokAdsConnection,
    User,
)

BASE = "/api/v1/optimization"
SIGN_SECRET = "unit-test-proposal-secret-0123456789abcdef"


def _connect_google(db, user, customer_id="1234567890"):
    db.add(GoogleAdsConnection(
        user_id=user.id, customer_id=customer_id,
        encrypted_refresh_token="x", encrypted_access_token="x", is_active=True,
    ))
    db.commit()


class TestRouting:
    def test_routes_are_not_double_prefixed(self, client):
        paths = client.get("/api/v1/openapi.json").json()["paths"]
        assert f"{BASE}/status" in paths
        assert f"{BASE}/evaluate" in paths
        assert f"{BASE}/propose" in paths
        assert not any("/optimization/optimization" in p for p in paths)

    def test_status_requires_auth(self, client):
        assert client.get(f"{BASE}/status").status_code == status.HTTP_401_UNAUTHORIZED


class TestStatus:
    def test_engine_is_reported_inactive(self, client, auth_headers):
        data = client.get(f"{BASE}/status", headers=auth_headers).json()
        assert data["engine_active"] is False
        assert "timezone" in data["operating_hours"]
        assert "start_wib" not in data["operating_hours"]


class TestEvaluate:
    BODY = {
        "provider": "google_ads", "account_id": "1234567890",
        "action": "campaign.pause", "payload": {"campaign_id": "1"},
    }

    def test_verdict_is_advisory_only(self, client, auth_headers, monkeypatch):
        monkeypatch.setenv("ADS_AUTO_MODE", "true")
        verdict = client.post(f"{BASE}/evaluate", headers=auth_headers, json=self.BODY).json()
        assert verdict["executes"] is False

    def test_client_supplied_counters_are_ignored(self, client, auth_headers, monkeypatch):
        """A caller must not be able to bypass the daily ceiling by claiming it spent 0."""
        monkeypatch.setenv("ADS_AUTO_MODE", "true")
        monkeypatch.setenv("ADS_AUTO_ACTIONS", "campaign.pause")
        monkeypatch.setenv("ADS_AUTO_MAX_DAILY_SPEND_MICROS", "1000000")
        body = {**self.BODY, "today_spend_micros": 0, "today_action_count": 0}
        verdict = client.post(f"{BASE}/evaluate", headers=auth_headers, json=body).json()
        assert verdict["ok"] is False
        assert verdict["reason"] == "daily-spend-unverifiable"

    def test_unsupported_action_and_provider_rejected(self, client, auth_headers):
        assert client.post(f"{BASE}/evaluate", headers=auth_headers,
                           json={**self.BODY, "action": "account.delete"}).status_code == 400
        assert client.post(f"{BASE}/evaluate", headers=auth_headers,
                           json={**self.BODY, "provider": "myspace"}).status_code == 400

    def test_kill_switch_wins(self, client, auth_headers, monkeypatch):
        monkeypatch.setenv("ADS_MUTATION_KILL_SWITCH", "true")
        verdict = client.post(f"{BASE}/evaluate", headers=auth_headers, json=self.BODY).json()
        assert verdict["ok"] is False and verdict["reason"] == "kill-switch-engaged"

    def test_inactive_user_is_rejected(self, client, auth_headers, db_session, test_user):
        test_user.is_active = False
        db_session.commit()
        assert client.post(f"{BASE}/evaluate", headers=auth_headers, json=self.BODY).status_code == 403


class TestPropose:
    BODY = {
        "provider": "google_ads", "account_id": "123-456-7890",
        "action": "campaign.pause", "payload": {"campaign_id": "1"},
    }

    @pytest.fixture(autouse=True)
    def _secret(self, monkeypatch):
        monkeypatch.setenv("PROPOSAL_TOKEN_SECRET", SIGN_SECRET)

    def test_cannot_sign_for_an_account_you_do_not_own(self, client, auth_headers):
        response = client.post(f"{BASE}/propose", headers=auth_headers, json=self.BODY)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_cannot_sign_for_someone_elses_account(self, client, auth_headers, db_session):
        other = User(email="other-owner@example.com", hashed_password=get_password_hash("x"),
                     name="Other", is_active=True)
        db_session.add(other)
        db_session.commit()
        _connect_google(db_session, other)
        assert client.post(f"{BASE}/propose", headers=auth_headers, json=self.BODY).status_code == 403

    def test_owner_gets_a_token_and_an_audit_row(self, client, auth_headers, db_session, test_user):
        _connect_google(db_session, test_user)
        response = client.post(f"{BASE}/propose", headers=auth_headers, json=self.BODY)
        assert response.status_code == 200
        assert response.json()["token"].startswith("prop1_")
        audit = db_session.query(MutationAudit).filter(MutationAudit.user_id == test_user.id).one()
        assert audit.verdict_reason == "proposal-signed" and audit.executed is False

    def test_kill_switch_blocks_signing(self, client, auth_headers, db_session, test_user, monkeypatch):
        _connect_google(db_session, test_user)
        monkeypatch.setenv("ADS_MUTATION_KILL_SWITCH", "true")
        assert client.post(f"{BASE}/propose", headers=auth_headers, json=self.BODY).status_code == 423

    def test_meta_and_tiktok_ownership(self, client, auth_headers, db_session, test_user):
        db_session.add(MetaAdsConnection(user_id=test_user.id, ad_account_id="act_555",
                                         encrypted_access_token="x", is_active=True))
        db_session.add(TikTokAdsConnection(user_id=test_user.id, advertiser_id="777",
                                           encrypted_refresh_token="x", is_active=True))
        db_session.commit()
        meta = {**self.BODY, "provider": "meta", "account_id": "555"}
        tiktok = {**self.BODY, "provider": "tiktok", "account_id": "777"}
        assert client.post(f"{BASE}/propose", headers=auth_headers, json=meta).status_code == 200
        assert client.post(f"{BASE}/propose", headers=auth_headers, json=tiktok).status_code == 200


class TestBotConnectionsAreScopedToKeyOwner:
    def _key(self, db, owner):
        raw = f"ads_studio_bot_test_{secrets.token_urlsafe(16)}"
        db.add(ApiKey(name="bot", key_hash=_hash_api_key(raw), scopes=["ads:read"],
                      created_by_user_id=owner.id if owner else None))
        db.commit()
        return {"Authorization": f"Bearer {raw}"}

    def test_key_only_sees_its_owners_connections(self, client, db_session, test_user):
        other = User(email="bot-other@example.com", hashed_password=get_password_hash("x"),
                     name="Other", is_active=True)
        db_session.add(other)
        db_session.commit()
        _connect_google(db_session, test_user, "1111111111")
        _connect_google(db_session, other, "2222222222")
        data = client.get("/api/v1/bot/connections", headers=self._key(db_session, test_user)).json()
        assert [c["customer_id"] for c in data["google_ads"]] == ["1111111111"]

    def test_unbound_key_sees_nothing(self, client, db_session, test_user):
        _connect_google(db_session, test_user)
        response = client.get("/api/v1/bot/connections", headers=self._key(db_session, None))
        assert response.status_code == status.HTTP_403_FORBIDDEN
