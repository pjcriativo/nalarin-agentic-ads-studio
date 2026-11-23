"""Safety boundaries for the Ads Studio machine-to-machine bot API."""
import secrets

from fastapi import status


class TestBotApiAuth:
    def test_connections_requires_api_key_not_jwt(self, client):
        response = client.get("/api/v1/bot/connections")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_safety_requires_api_key(self, client):
        response = client.get("/api/v1/bot/safety")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_overview_requires_api_key(self, client):
        response = client.get("/api/v1/bot/overview")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestBotApiScopes:
    def test_read_key_can_get_safety_policy(self, client, db_session):
        from app.models import ApiKey
        from app.core.deps import _hash_api_key
        import uuid
        from app.models import Organization
        org = Organization(id=str(uuid.uuid4()), name="bot org", slug=f"bot-{uuid.uuid4().hex[:8]}")
        db_session.add(org)
        db_session.commit()
        raw = f"ads_studio_bot_test_{secrets.token_urlsafe(16)}"
        db_session.add(ApiKey(name="bot", key_hash=_hash_api_key(raw), scopes=["ads:read"], organization_id=org.id))
        db_session.commit()

        response = client.get("/api/v1/bot/safety", headers={"Authorization": f"Bearer {raw}"})
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["can_spend"] is False

    def test_bot_has_no_write_routes_in_openapi(self, client):
        schema = client.get("/api/v1/openapi.json").json()
        bot_paths = [path for path in schema["paths"] if path.startswith("/api/v1/bot")]
        assert sorted(bot_paths) == sorted([
            "/api/v1/bot/connections",
            "/api/v1/bot/safety",
            "/api/v1/bot/spend",
            "/api/v1/bot/overview",
            "/api/v1/bot/approvals",
        ])
        # the ONLY write a bot has is REQUESTING a spend approval (a human decides)
        assert [p for p in bot_paths if "post" in schema["paths"][p]] == ["/api/v1/bot/approvals"]
