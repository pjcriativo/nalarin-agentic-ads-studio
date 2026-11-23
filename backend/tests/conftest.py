import os
from pathlib import Path
from urllib.parse import urlparse

import pytest


def _resolve_test_database_url() -> str:
    """The suite only ever runs against a DISPOSABLE PostgreSQL database.

    It requires an explicit TEST_DATABASE_URL (there is deliberately no
    fallback to DATABASE_URL, which in local dev points at the shared dev
    database and in production at Railway) and refuses any database whose
    name does not end in ``_test``. Start one with
    ``docker compose -f docker-compose.test.yml up -d`` (see scripts/test-backend.sh).
    """
    url = os.getenv("TEST_DATABASE_URL", "").strip()
    if not url:
        raise pytest.UsageError(
            "TEST_DATABASE_URL is required. The tests create/drop data and must "
            "never run against the dev or production database. Start the "
            "disposable DB: docker compose -f docker-compose.test.yml up -d"
        )
    if not url.startswith(("postgresql://", "postgres://")):
        raise pytest.UsageError("TEST_DATABASE_URL must be a PostgreSQL URL.")
    db_name = urlparse(url).path.lstrip("/")
    if not db_name.endswith("_test"):
        raise pytest.UsageError(
            f"Refusing to run: database '{db_name}' does not end in '_test'."
        )
    configured = os.getenv("DATABASE_URL", "").strip()
    if configured and configured == url:
        raise pytest.UsageError("TEST_DATABASE_URL must differ from DATABASE_URL.")
    return url


TEST_DATABASE_URL = _resolve_test_database_url()

# Make the process hermetic BEFORE the app is imported: point the app's own
# engine at the disposable DB and neutralise anything a developer's .env could
# leak in (real provider credentials, auto-mode, kill-switch...).
from cryptography.fernet import Fernet  # noqa: E402

os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["SECRET_KEY"] = "test-secret-key-0123456789-abcdefghijklmnop"
os.environ["OAUTH_TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
os.environ["GEMINI_API_KEY"] = "mock-key"
os.environ["FAL_AI_API_KEY"] = "mock-key"
for _var in (
    "KIE_AI_API_KEY", "FACEBOOK_ACCESS_TOKEN", "VITE_FACEBOOK_ACCESS_TOKEN",
    "FACEBOOK_APP_ID", "FACEBOOK_APP_SECRET", "FACEBOOK_OAUTH_REDIRECT_URI",
    "GOOGLE_ADS_CLIENT_ID", "GOOGLE_ADS_CLIENT_SECRET", "GOOGLE_ADS_DEVELOPER_TOKEN",
    "GOOGLE_ADS_LOGIN_CUSTOMER_ID", "GOOGLE_ADS_OAUTH_REDIRECT_URI",
    "TIKTOK_ADS_APP_ID", "TIKTOK_ADS_APP_SECRET", "TIKTOK_ADS_OAUTH_REDIRECT_URI",
    "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME",
    "R2_PUBLIC_URL", "ADS_AUTO_MODE", "ADS_MUTATION_KILL_SWITCH", "PROPOSAL_TOKEN_SECRET",
):
    os.environ[_var] = ""

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from unittest.mock import MagicMock, patch  # noqa: E402

from app.main import app  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.models import User, Role  # noqa: E402
from app.core.security import get_password_hash  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.rate_limit import limiter  # noqa: E402

engine = create_engine(TEST_DATABASE_URL)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="session", autouse=True)
def _migrated_test_database():
    """Build the schema of the disposable DB with the real Alembic migrations
    (not just create_all), so a broken or missing migration fails the suite."""
    from alembic import command
    from alembic.config import Config

    backend_dir = Path(__file__).resolve().parent.parent
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")
    yield


@pytest.fixture
def google_ads_configured(monkeypatch):
    """Explicitly enable Google Ads for tests that exercise configured-server paths."""
    monkeypatch.setattr(settings, "GOOGLE_ADS_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(settings, "GOOGLE_ADS_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setattr(settings, "GOOGLE_ADS_DEVELOPER_TOKEN", "test-developer-token")
    monkeypatch.setattr(settings, "GOOGLE_ADS_OAUTH_REDIRECT_URI", "http://localhost:8000/api/v1/google-ads/oauth/callback")


@pytest.fixture(autouse=True)
def _hermetic_dns(monkeypatch):
    """The SSRF guard resolves hostnames; tests must not depend on real DNS."""
    from app.core import storage
    monkeypatch.setattr(storage, "_resolve_host", lambda host: ["93.184.216.34"])
    yield


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Reset slowapi's limiter before every test.

    All TestClient requests share one fake client IP, so rate-limited routes
    (e.g. 5/minute on login) get exhausted after only a handful of tests in
    the same session — regardless of whether they call login directly or via
    the `auth_headers` fixture. This only resets the test process's in-memory
    limiter state; it has no effect on production.
    """
    limiter.reset()
    yield


@pytest.fixture(scope="function")
def db_session():
    """Create a fresh database session for each test.

    Note: Uses PostgreSQL. Tables are not dropped after tests to preserve
    production data. Test user is cleaned up individually.
    """
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        # Clean up test data (don't drop tables in production DB)
        # Delete test user if it exists
        from sqlalchemy import text
        from app.models import (
            User, RefreshToken, MutationAudit, GoogleAdsConnection,
            MetaAdsConnection, TikTokAdsConnection, ApiKey,
            Organization,
        )
        session.rollback()
        # This session is the (superuser) seeding connection, so it bypasses RLS
        # and can wipe the tenant tables of the disposable test database.
        for table in (
            "spend_approvals", "ad_account_links", "oauth_states", "mutation_audits",
            "google_ads_connections", "meta_ads_connections", "tiktok_ads_connections", "api_keys",
            "brand_scraped_ads", "brand_scrapes", "scraped_ads", "search_logs", "api_usage_logs",
            "page_blacklist", "keyword_blacklist", "saved_searches", "facebook_pages", "verticals",
            "facebook_ads", "facebook_adsets", "facebook_campaigns", "generated_ads", "winning_ads",
            "audit_logs",
            "brand_profiles", "products", "brands", "customer_profiles",
        ):
            session.execute(text(f"DELETE FROM {table}"))
        session.commit()
        test_user = session.query(User).filter(User.email == "test@example.com").first()
        for u in session.query(User).filter(User.email.like("%@example.com")).all():
            for model in (MutationAudit, GoogleAdsConnection, MetaAdsConnection, TikTokAdsConnection):
                session.query(model).filter(model.user_id == u.id).delete()
            session.query(ApiKey).filter(ApiKey.created_by_user_id == u.id).delete()
            if u is not test_user:
                session.query(RefreshToken).filter(RefreshToken.user_id == u.id).delete()
                session.delete(u)
        session.commit()
        if test_user:
            session.query(RefreshToken).filter(RefreshToken.user_id == test_user.id).delete()
            session.delete(test_user)
            session.commit()
        session.query(Organization).delete()
        session.commit()
        session.close()


@pytest.fixture(scope="function")
def client(db_session):
    """Test client over the REAL application database layer.

    No get_db override: requests use app.database.engine, i.e. the unprivileged
    runtime role with row level security in force -- exactly like production.
    ``db_session`` (superuser, RLS bypass) is only for seeding / assertions.
    """
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def test_user(db_session):
    """Create a test user with admin role and all permissions."""
    from app.models import Permission

    # Get or create permissions
    def get_or_create_permission(name, description):
        perm = db_session.query(Permission).filter(Permission.name == name).first()
        if not perm:
            perm = Permission(name=name, description=description)
            db_session.add(perm)
        return perm

    # Create all permissions needed for tests
    all_permissions = [
        ("campaigns:write", "Write campaigns"),
        ("ads:write", "Write ads"),
        ("ads:delete", "Delete ads"),
        ("brands:write", "Write brands"),
        ("brands:delete", "Delete brands"),
        ("products:write", "Write products"),
        ("products:delete", "Delete products"),
        ("profiles:write", "Write profiles"),
        ("profiles:delete", "Delete profiles"),
    ]

    permissions = []
    for name, desc in all_permissions:
        perm = get_or_create_permission(name, desc)
        permissions.append(perm)
    db_session.commit()

    # Get or create admin role with all permissions
    admin_role = db_session.query(Role).filter(Role.name == "admin").first()
    if not admin_role:
        admin_role = Role(name="admin", description="Administrator")
        for perm in permissions:
            admin_role.permissions.append(perm)
        db_session.add(admin_role)
        db_session.commit()
    else:
        # Ensure admin role has all permissions
        for perm in permissions:
            if perm not in admin_role.permissions:
                admin_role.permissions.append(perm)
        db_session.commit()

    # Clean up any existing test user
    existing_user = db_session.query(User).filter(User.email == "test@example.com").first()
    if existing_user:
        from app.models import RefreshToken
        db_session.query(RefreshToken).filter(RefreshToken.user_id == existing_user.id).delete()
        db_session.delete(existing_user)
        db_session.commit()

    # Create test user
    user = User(
        email="test@example.com",
        hashed_password=get_password_hash("testpassword"),
        name="Test User",
        is_active=True
    )
    user.roles.append(admin_role)
    db_session.add(user)
    db_session.flush()
    from app.core.tenancy import create_personal_organization
    create_personal_organization(db_session, user)
    db_session.commit()
    db_session.refresh(user)
    return user


# ---- multi-tenant helpers ----------------------------------------------------
@pytest.fixture
def make_user(db_session):
    """Create an active user (email must end in @example.com for cleanup)."""
    def _make(email, password="testpassword", name=None, superuser=False, personal_org=False):
        assert email.endswith("@example.com")
        user = User(email=email, hashed_password=get_password_hash(password),
                    name=name or email.split("@")[0], is_active=True, is_superuser=superuser)
        db_session.add(user)
        db_session.flush()
        if personal_org:
            from app.core.tenancy import create_personal_organization
            create_personal_organization(db_session, user)
        db_session.commit()
        db_session.refresh(user)
        return user
    return _make


@pytest.fixture
def make_org(db_session):
    """Create an organization; ``owner`` (optional) becomes its owner."""
    def _make(name, owner=None):
        from app.models import Organization, OrganizationMember
        import uuid
        org = Organization(name=name, slug=f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:6]}")
        db_session.add(org)
        db_session.flush()
        if owner is not None:
            db_session.add(OrganizationMember(organization_id=org.id, user_id=owner.id, role="owner", status="active"))
        db_session.commit()
        db_session.refresh(org)
        return org
    return _make


@pytest.fixture
def add_member(db_session):
    def _add(org, user, role="viewer", status="active"):
        from app.models import OrganizationMember
        m = OrganizationMember(organization_id=org.id, user_id=user.id, role=role, status=status)
        db_session.add(m)
        db_session.commit()
        return m
    return _add


@pytest.fixture
def login_headers(client):
    """Return auth headers for a user; optionally pinned to an organization."""
    from app.core.security import create_access_token

    def _login(user, org=None):
        # Mint the JWT directly (the login endpoint is rate limited to 5/min and
        # these tests need many users); login itself is covered in test_auth.
        headers = {"Authorization": f"Bearer {create_access_token({'sub': user.id})}"}
        if org is not None:
            headers["X-Organization-Id"] = org.id
        return headers
    return _login


@pytest.fixture
def auth_headers(client, test_user):
    """Get authentication headers for test user."""
    response = client.post(
        "/api/v1/auth/login/json",
        json={"email": "test@example.com", "password": "testpassword"}
    )
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def mock_facebook_service(client):
    """Mock FacebookService for testing."""
    from app.api.v1.facebook import get_facebook_service

    service = MagicMock()
    service.api = MagicMock()

    def override_get_facebook_service():
        return service

    app.dependency_overrides[get_facebook_service] = override_get_facebook_service
    yield service
    # Clean up the override
    if get_facebook_service in app.dependency_overrides:
        del app.dependency_overrides[get_facebook_service]
import pytest

@pytest.fixture
def anyio_backend():
    return 'asyncio'


# ---- integration helpers (etapa 01.3) ----------------------------------------
@pytest.fixture
def org_id_of(db_session):
    """The personal organization id of a user created by ``test_user`` / ``make_user(personal_org=True)``."""
    def _of(user):
        from app.models import OrganizationMember
        return db_session.query(OrganizationMember).filter(
            OrganizationMember.user_id == user.id, OrganizationMember.status == "active").first().organization_id
    return _of


@pytest.fixture
def add_connection(db_session):
    """Persist an ad-platform connection for an ORGANIZATION plus its explicit
    account link (``active`` when the connection is selected, else ``pending``)."""
    def _add(org_id, provider, connection, link_status=None):
        from app.models import AdAccountLink
        from app.services.integrations import PROVIDERS, normalize_account
        connection.organization_id = org_id
        db_session.add(connection)
        account = normalize_account(provider, getattr(connection, PROVIDERS[provider]["account_col"]))
        status_value = link_status or ("active" if connection.is_active else "pending")
        db_session.add(AdAccountLink(organization_id=org_id, provider=provider, external_account_id=account,
                                     account_name=connection.account_name, status=status_value))
        db_session.commit()
        return connection
    return _add
