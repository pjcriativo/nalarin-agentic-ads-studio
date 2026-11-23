"""Organization-scoped ad-platform connections and explicit account links.

Model
-----
* A *connection* row holds the (encrypted) OAuth tokens and belongs to ONE
  organization (``organization_id``, RLS). The same person authorizing the same
  account from two organizations creates two independent connections: nothing is
  shared implicitly.
* An *ad account link* (``ad_account_links``) is the explicit authorization of an
  account for the organization. OAuth only yields ``pending`` candidates; a member
  with ``connections:manage`` activates the account (single account grants are
  activated by that same explicit act). Operations require an ``active`` link
  AND the connection selected (``is_active``) -- both inside the caller's
  organization.
* Tokens are never returned by any function here.
"""
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from fastapi import HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.core.audit import audit_denied, audit_event
from app.core.oauth_state import (
    OAuthContext,
    clear_oauth_state_cookie,
    consume_oauth_state,
    create_oauth_state,
    get_oauth_state_cookie_name,
    set_oauth_state_cookie,
)
from app.core.tenancy import ROLE_PERMISSIONS, OrgContext
from app.database import bind_context
from app.models import (
    AdAccountLink,
    GoogleAdsConnection,
    MetaAdsConnection,
    Organization,
    OrganizationMember,
    TikTokAdsConnection,
)

PROVIDERS: Dict[str, Dict[str, Any]] = {
    "google_ads": {"model": GoogleAdsConnection, "account_col": "customer_id", "label": "Google Ads"},
    "meta": {"model": MetaAdsConnection, "account_col": "ad_account_id", "label": "Meta Ads"},
    "tiktok": {"model": TikTokAdsConnection, "account_col": "advertiser_id", "label": "TikTok Ads"},
}


def normalize_account(provider: str, value: str) -> str:
    value = str(value or "").strip()
    if provider == "google_ads":
        return "".join(ch for ch in value if ch.isdigit())
    if provider == "meta":
        return value[4:] if value.startswith("act_") else value
    return value


def storage_account(provider: str, value: str) -> str:
    """Form kept in the connection column (Meta keeps its ``act_`` prefix)."""
    normalized = normalize_account(provider, value)
    return f"act_{normalized}" if provider == "meta" and normalized else normalized


def _model(provider: str):
    return PROVIDERS[provider]["model"]


def _account_attr(provider: str):
    return getattr(_model(provider), PROVIDERS[provider]["account_col"])


def _account_value(provider: str, connection) -> str:
    return normalize_account(provider, getattr(connection, PROVIDERS[provider]["account_col"]))


def _now():
    return datetime.now(timezone.utc)


# ----------------------------------------------------------------------------
# links
# ----------------------------------------------------------------------------
def _link(db: Session, org_id: str, provider: str, account: str) -> Optional[AdAccountLink]:
    return db.query(AdAccountLink).filter(
        AdAccountLink.organization_id == org_id,
        AdAccountLink.provider == provider,
        AdAccountLink.external_account_id == account,
    ).first()


def activate_link(db: Session, org_id: str, provider: str, account: str, user_id: str,
                  name: Optional[str] = None, login_customer_id: Optional[str] = None) -> AdAccountLink:
    link = _link(db, org_id, provider, account)
    if link is None:
        link = AdAccountLink(organization_id=org_id, provider=provider, external_account_id=account)
        db.add(link)
    link.status = "active"
    link.authorized_by_user_id = user_id
    link.authorized_at = _now()
    link.revoked_at = None
    if name:
        link.account_name = name
    if login_customer_id is not None:
        link.login_customer_id = login_customer_id
    return link


def account_is_authorized(db: Session, org_id: str, provider: str, account_id: str) -> bool:
    """True only when the organization has an ACTIVE link to this account."""
    account = normalize_account(provider, account_id)
    if not account:
        return False
    link = _link(db, org_id, provider, account)
    return link is not None and link.status == "active"


def authorized_accounts(db: Session, org_id: str, provider: str) -> List[str]:
    rows = db.query(AdAccountLink.external_account_id).filter(
        AdAccountLink.organization_id == org_id, AdAccountLink.provider == provider,
        AdAccountLink.status == "active").all()
    return [r[0] for r in rows]


def require_authorized_account(db: Session, ctx: OrgContext, provider: str, account_id: Optional[str]) -> str:
    """404 (same answer as 'does not exist') unless the account is linked to the
    caller's organization. Returns the normalized account id."""
    if not account_authorized_or_none(db, ctx.org.id, provider, account_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Ad account is not linked to this organization.")
    return normalize_account(provider, account_id)


def account_authorized_or_none(db, org_id, provider, account_id):
    return account_id if account_is_authorized(db, org_id, provider, account_id) else None


# ----------------------------------------------------------------------------
# connections
# ----------------------------------------------------------------------------
def list_connections(db: Session, org_id: str, provider: str):
    model = _model(provider)
    return db.query(model).filter(model.organization_id == org_id).order_by(_account_attr(provider)).all()


def selected_connection(db: Session, org_id: str, provider: str):
    """The connection the organization currently operates -- or 404 (fail closed)
    when none is selected, or the selected account has no ACTIVE link."""
    model = _model(provider)
    connection = db.query(model).filter(model.organization_id == org_id, model.is_active.is_(True)).first()
    if connection is None or not account_is_authorized(db, org_id, provider, _account_value(provider, connection)):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No connected {PROVIDERS[provider]['label']} account. Connect and select one first.",
        )
    return connection


def selected_link(db: Session, org_id: str, provider: str, connection) -> Optional[AdAccountLink]:
    return _link(db, org_id, provider, _account_value(provider, connection))


def store_oauth_grant(db: Session, org_id: str, user_id: str, provider: str,
                      accounts: Iterable[Dict[str, Optional[str]]], token_fields: Dict[str, Any]) -> bool:
    """Persist the accounts of one OAuth grant for ONE organization.

    Returns ``select_required`` (True when the grant covers several accounts and
    a member must pick one). Existing authorizations of other accounts are kept.
    """
    model = _model(provider)
    col = PROVIDERS[provider]["account_col"]
    accounts = [a for a in accounts if normalize_account(provider, a.get("id"))]
    select_required = len(accounts) > 1

    db.query(model).filter(model.organization_id == org_id).update({"is_active": False}, synchronize_session=False)
    for account in accounts:
        account_id = normalize_account(provider, account["id"])
        stored = storage_account(provider, account["id"])
        connection = db.query(model).filter(model.organization_id == org_id, getattr(model, col) == stored).first()
        if connection is None:
            connection = model(organization_id=org_id, user_id=user_id, **{col: stored})
            db.add(connection)
        for key, value in token_fields.items():
            setattr(connection, key, value)
        connection.user_id = user_id
        if account.get("name"):
            connection.account_name = account["name"]
        connection.is_active = not select_required
        if not select_required:
            activate_link(db, org_id, provider, account_id, user_id, name=account.get("name"))
        elif _link(db, org_id, provider, account_id) is None:
            db.add(AdAccountLink(organization_id=org_id, provider=provider, external_account_id=account_id,
                                 account_name=account.get("name"), status="pending"))
    return select_required


def select_account(db: Session, ctx: OrgContext, provider: str, account_id: str, request: Optional[Request] = None):
    account = normalize_account(provider, account_id)
    model = _model(provider)
    connection = db.query(model).filter(
        model.organization_id == ctx.org.id, _account_attr(provider) == storage_account(provider, account_id)).first()
    if connection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"{PROVIDERS[provider]['label']} account is not available for this organization.")
    db.query(model).filter(model.organization_id == ctx.org.id).update({"is_active": False}, synchronize_session=False)
    connection.is_active = True
    activate_link(db, ctx.org.id, provider, account, ctx.user.id, name=connection.account_name)
    audit_event(db, action="integration.account_selected", organization_id=ctx.org.id,
                actor_user_id=ctx.user.id, target=f"{provider}:{account}", request=request)
    db.commit()
    return connection


def disconnect(db: Session, ctx: OrgContext, provider: str, request: Optional[Request] = None) -> None:
    model = _model(provider)
    selected = db.query(model).filter(model.organization_id == ctx.org.id, model.is_active.is_(True)).first()
    if selected is not None:
        selected.is_active = False
        link = selected_link(db, ctx.org.id, provider, selected)
        if link is not None:
            link.status = "revoked"
            link.revoked_at = _now()
        audit_event(db, action="integration.disconnected", organization_id=ctx.org.id, actor_user_id=ctx.user.id,
                    target=f"{provider}:{_account_value(provider, selected)}", request=request)
        db.commit()


def public_view(provider: str, connection) -> Dict[str, Any]:
    """Metadata only -- never tokens."""
    return {
        PROVIDERS[provider]["account_col"]: getattr(connection, PROVIDERS[provider]["account_col"]),
        "account_name": connection.account_name,
        "selected": bool(connection.is_active),
    }


# ----------------------------------------------------------------------------
# OAuth start / callback helpers (shared by Google, Meta, TikTok)
# ----------------------------------------------------------------------------
def begin_oauth(db: Session, request: Request, response: Response, ctx: OrgContext, provider_key: str) -> str:
    """Create the single-use state for (user, organization, provider) and set the
    browser-binding cookie. Returns the state token for the provider URL."""
    token, cookie = create_oauth_state(db, ctx.user.id, ctx.org.id, provider_key)
    set_oauth_state_cookie(response, cookie, secure=request.url.scheme == "https")
    return token


def finish_oauth(db: Session, request: Request, state: Optional[str], provider_key: str) -> OAuthContext:
    """Consume the state at the public callback and re-authorize the user.

    * Replayed / expired / forged / other-provider / other-browser state -> 400.
    * The user must STILL be an active member of the (still active) organization
      with ``connections:manage``, otherwise 403 (and the denial is audited).
    On success the DB session is bound to the organization of the state.
    """
    if not state:
        raise HTTPException(status_code=400, detail="Missing OAuth state parameter")
    cookie = request.cookies.get(get_oauth_state_cookie_name())
    try:
        context = consume_oauth_state(db, state, provider_key, cookie)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    member = (
        db.query(OrganizationMember)
        .join(Organization, Organization.id == OrganizationMember.organization_id)
        .filter(OrganizationMember.user_id == context.user_id,
                OrganizationMember.organization_id == context.organization_id,
                OrganizationMember.status == "active", Organization.is_active.is_(True))
        .first()
    )
    if member is None or "connections:manage" not in ROLE_PERMISSIONS.get(member.role, frozenset()):
        audit_denied(action="oauth.callback_denied", actor_user_id=context.user_id,
                     organization_id=context.organization_id, target=provider_key,
                     detail={"reason": "membership_or_permission_lost"}, request=request)
        raise HTTPException(status_code=403, detail="You no longer have access to connect accounts in this organization")
    bind_context(db, context.organization_id)
    return context


def finish_response(redirect, request_secure: bool = True):
    clear_oauth_state_cookie(redirect)
    return redirect
