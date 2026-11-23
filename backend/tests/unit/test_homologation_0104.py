"""Etapa 01.4 (backend side): contracts the interface relies on + SSRF / private-file hardening."""
import io
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

from app.core import storage
from app.core.executor import canonical_payload_hash
from app.core.tenancy import ROLE_PERMISSIONS, ROLES

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def world(make_user, make_org, add_member, login_headers):
    alice = make_user("alice-h@example.com")
    org = make_org("H Org", owner=alice)
    return SimpleNamespace(alice=alice, org=org, h=login_headers(alice, org),
                           member=lambda role, email: (lambda u: (add_member(org, u, role=role), login_headers(u, org))[1])(make_user(email)))


class TestInterfaceContracts:
    def test_cors_preflight_allows_the_organization_header(self, client):
        r = client.options("/api/v1/brands", headers={
            "Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization,x-organization-id"})
        assert r.status_code == 200
        assert "x-organization-id" in r.headers["access-control-allow-headers"].lower()
        assert r.headers["access-control-allow-origin"] == "http://localhost:5173"

    def test_a_foreign_origin_is_not_allowed(self, client):
        r = client.options("/api/v1/brands", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
        assert "access-control-allow-origin" not in r.headers

    def test_my_organizations_carry_the_servers_real_permissions(self, client, world):
        mgr = world.member("manager", "mgr-h@example.com")
        mine = client.get("/api/v1/organizations", headers=mgr).json()
        assert [(o["id"], o["role"]) for o in mine] == [(world.org.id, "manager")]
        assert mine[0]["permissions"] == sorted(ROLE_PERMISSIONS["manager"])
        owner = client.get("/api/v1/organizations", headers=world.h).json()[0]
        assert "ads:spend" in owner["permissions"] and "ads:spend" not in mine[0]["permissions"]

    def test_role_matrix_is_the_servers_matrix_and_needs_a_session(self, client, world):
        assert client.get("/api/v1/organizations/role-matrix").status_code == 401
        body = client.get("/api/v1/organizations/role-matrix", headers=world.h).json()
        assert body["roles"] == list(ROLES)  # owner first
        assert body["permissions"] == {r: sorted(ROLE_PERMISSIONS[r]) for r in ROLES}

    def test_approval_payload_hash_is_exposed_and_matches_the_hash_the_ui_computes(self, client, world, db_session):
        from app.models import AdAccountLink
        db_session.add(AdAccountLink(organization_id=world.org.id, provider="google_ads", external_account_id="1111111111", status="active"))
        db_session.commit()
        r = client.post("/api/v1/optimization/approvals", headers=world.h, json={
            "provider": "google_ads", "account_id": "1111111111", "action": "campaign.enable",
            "payload": {"campaign_id": "123"}, "budget_micros": 1000, "idempotency_key": "k"})
        assert r.status_code == 201
        # the SAME constant is asserted by the front-end test (sha256 of the canonical JSON)
        assert r.json()["payload_hash"] == canonical_payload_hash({"campaign_id": "123"}) \
            == "cf4d9ba784e28d4bf3769725ced28978b4f30db89ef3c8764f965b104e6768e2"
        assert client.get("/api/v1/optimization/approvals", headers=world.h).json()[0]["payload_hash"] == r.json()["payload_hash"]


class TestPrivateFiles:
    def test_served_uploads_are_sandboxed_non_sniffable_and_not_leaked_through_referer(self, client, world):
        url = client.post("/api/v1/uploads/", headers=world.h, files={"file": ("a.png", io.BytesIO(PNG), "image/png")}).json()["url"]
        r = client.get(url)
        assert r.status_code == 200
        assert r.headers["content-security-policy"] == "default-src 'none'; sandbox"
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["referrer-policy"] == "no-referrer"
        assert r.headers["cache-control"].startswith("private")

    def test_an_upload_url_cannot_be_used_for_another_organization_or_guessed(self, client, world, make_user, make_org, login_headers):
        url = client.post("/api/v1/uploads/", headers=world.h, files={"file": ("a.png", io.BytesIO(PNG), "image/png")}).json()["url"]
        name = url.rsplit("/", 1)[-1]
        other = make_org("Other H", owner=make_user("other-h@example.com"))
        assert client.get(f"/uploads/{other.id}/{name}").status_code == 404
        assert client.get(f"/uploads/{world.org.id}/{'0' * 32}_{'0' * 16}.png").status_code == 404
        assert client.get(f"/uploads/{world.org.id}/{name[:-4]}.svg").status_code == 404


class _Resp:
    def __init__(self, status=200, headers=None, chunks=(b"data",)):
        self.status_code = status
        self.headers = headers or {}
        self._chunks = chunks
        self.is_redirect = status in (301, 302, 303, 307, 308)
        self.is_permanent_redirect = status in (301, 308)

    def iter_content(self, chunk_size=0):
        yield from self._chunks

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def close(self):
        pass


class TestSsrfSafeFetch:
    def _requests(self, monkeypatch, plan):
        calls = []

        def fake_get(url, **kwargs):
            calls.append((url, kwargs))
            return plan.pop(0)

        monkeypatch.setattr("requests.get", fake_get)
        return calls

    def test_public_url_downloads_with_redirects_disabled_in_the_library(self, monkeypatch):
        calls = self._requests(monkeypatch, [_Resp(chunks=(b"ab", b"cd"))])
        sink = io.BytesIO()
        storage.fetch_public_to_file("https://cdn.example.com/x.png", sink, max_bytes=100)
        assert sink.getvalue() == b"abcd"
        assert calls[0][1]["allow_redirects"] is False and calls[0][1]["stream"] is True

    def test_a_redirect_to_an_internal_address_is_refused_before_it_is_requested(self, monkeypatch):
        calls = self._requests(monkeypatch, [_Resp(302, {"Location": "http://169.254.169.254/latest/meta-data"}), _Resp()])
        with pytest.raises(ValueError, match="non-public"):
            storage.fetch_public_to_file("https://cdn.example.com/x.png", io.BytesIO(), max_bytes=100)
        assert [c[0] for c in calls] == ["https://cdn.example.com/x.png"]  # the internal hop was never fetched

    def test_a_redirect_to_a_hostname_that_resolves_privately_is_refused(self, monkeypatch):
        self._requests(monkeypatch, [_Resp(301, {"Location": "https://rebind.example.com/y"}), _Resp()])
        monkeypatch.setattr(storage, "_resolve_host", lambda host: ["10.0.0.7"] if host == "rebind.example.com" else ["93.184.216.34"])
        with pytest.raises(ValueError, match="non-public"):
            storage.fetch_public_to_file("https://cdn.example.com/x.png", io.BytesIO(), max_bytes=100)

    def test_relative_redirects_are_followed_within_the_limit_and_loops_stop(self, monkeypatch):
        loop = [_Resp(302, {"Location": "/again"}) for _ in range(10)]
        calls = self._requests(monkeypatch, loop)
        with pytest.raises(ValueError, match="Too many redirects"):
            storage.fetch_public_to_file("https://cdn.example.com/x", io.BytesIO(), max_bytes=100)
        assert len(calls) == storage.MAX_FETCH_REDIRECTS + 1

    def test_size_cap_is_enforced_while_streaming(self, monkeypatch):
        self._requests(monkeypatch, [_Resp(chunks=(b"x" * 60, b"x" * 60))])
        with pytest.raises(ValueError, match="too large"):
            storage.fetch_public_to_file("https://cdn.example.com/big", io.BytesIO(), max_bytes=100)

    def test_internal_addresses_are_refused_up_front(self, monkeypatch):
        calls = self._requests(monkeypatch, [_Resp()])
        for url in ("http://127.0.0.1/x", "http://10.1.1.1/x", "http://[::1]/x", "http://169.254.169.254/x", "file:///etc/passwd"):
            with pytest.raises(ValueError):
                storage.fetch_public_to_file(url, io.BytesIO(), max_bytes=100)
        assert calls == []

    @pytest.mark.anyio
    async def test_async_fetch_has_the_same_guarantees(self, monkeypatch, anyio_backend):
        seen = []

        def handler(request: httpx.Request):
            seen.append(str(request.url))
            if request.url.host == "cdn.example.com" and request.url.path == "/hop":
                return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})
            if request.url.path == "/big":
                return httpx.Response(200, content=b"x" * 500)
            return httpx.Response(200, content=b"ok")

        real = httpx.AsyncClient
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
        assert await storage.fetch_public_bytes("https://cdn.example.com/fine", max_bytes=100) == b"ok"
        with pytest.raises(ValueError, match="non-public"):
            await storage.fetch_public_bytes("https://cdn.example.com/hop", max_bytes=100)
        assert not any("127.0.0.1" in u for u in seen)
        with pytest.raises(ValueError, match="too large"):
            await storage.fetch_public_bytes("https://cdn.example.com/big", max_bytes=100)

    @pytest.mark.anyio
    async def test_generated_image_download_never_touches_an_internal_address(self, monkeypatch, anyio_backend):
        from app.api.v1 import generated_ads
        monkeypatch.setattr(generated_ads, "save_local", MagicMock())
        url = "http://169.254.169.254/latest/meta-data"
        # falls back to the original string (existing behaviour) and stores nothing
        assert await generated_ads.download_and_save_image(url, "00000000-0000-0000-0000-000000000000") == url
        generated_ads.save_local.assert_not_called()

    def test_the_media_reference_guard_is_the_one_used_by_the_ad_platform_upload_endpoints(self, client, world, mock_facebook_service):
        r = client.post("/api/v1/facebook/upload-image", headers=world.h, json={"image_url": "http://169.254.169.254/x"})
        assert r.status_code == 400
        mock_facebook_service.upload_image.assert_not_called()
