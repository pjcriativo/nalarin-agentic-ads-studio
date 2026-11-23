"""Google Ads OAuth connect flow + read-only campaign/ad performance routes."""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from google.ads.googleads.errors import GoogleAdsException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.oauth_state import clear_oauth_state_cookie
from app.core.tenancy import OrgContext, require_org_permission
from app.core.token_encryption import encrypt_token, decrypt_token
from app.database import get_db
from app.models import GoogleAdsConnection
from app.services import approvals, integrations
from app.services.google_ads_oauth import (
    build_oauth_url,
    exchange_code_for_tokens,
    normalize_customer_id,
    GoogleOAuthError,
)
from app.services.google_ads_service import (
    list_accessible_customers,
    get_campaign_performance,
    get_ad_performance,
    get_valid_access_token,
    create_campaign as create_google_campaign,
    pause_campaign as pause_google_campaign,
    enable_campaign as enable_google_campaign,
    add_negative_keywords as add_google_negative_keywords,
    get_campaign_budget_micros,
    use_login_customer,
    GoogleAdsNotConfigured,
    GoogleAdsConnectionError,
)

router = APIRouter()

PROVIDER = "google-ads"  # OAuth state provider
PLATFORM = "google_ads"  # account-link provider


class CreateCampaignRequest(BaseModel):
    name: str
    daily_budget_micros: int
    keywords: Optional[List[str]] = None
    confirm: bool = False


class NegativeKeywordsRequest(BaseModel):
    keywords: List[str]
    confirm: bool = False


class ConfirmOnlyRequest(BaseModel):
    confirm: bool = False


class SelectAccountRequest(BaseModel):
    customer_id: str
    login_customer_id: Optional[str] = None  # the organization's MCC, if the account is managed


class McCRequest(BaseModel):
    login_customer_id: Optional[str] = None  # None clears it


class EnableCampaignRequest(BaseModel):
    confirm: bool = False
    approval_id: Optional[str] = None


def _require_confirmed(confirm: bool) -> None:
    if not confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This action requires explicit confirmation. Set confirm=true after showing the user a preview.",
        )


def _require_configured():
    if not settings.google_ads_enabled:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Google Ads is not configured on this server (missing client ID/secret/developer token).",
        )


@router.get("/oauth/start")
async def start_oauth(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("connections:manage")),
):
    """Return the Google consent-screen URL for the frontend to navigate to.

    The state is single use and bound to THIS user, THIS organization and this
    browser (see app.core.oauth_state); the tokens obtained at the callback can
    only ever be stored for that organization.
    """
    _require_configured()
    state = integrations.begin_oauth(db, request, response, ctx, PROVIDER)
    return {
        "oauth_url": build_oauth_url(
            client_id=settings.GOOGLE_ADS_CLIENT_ID,
            redirect_uri=settings.GOOGLE_ADS_OAUTH_REDIRECT_URI,
            state=state,
        )
    }


@router.get("/oauth/callback")
async def oauth_callback(
    request: Request,
    code: str = None,
    state: str = None,
    error: str = None,
    db: Session = Depends(get_db),
):
    """Public callback (Google redirects the browser here; no Authorization header).

    The state is CONSUMED first (replay/expired/forged/other-browser -> 400), the
    user is re-authorized for the organization recorded in it, and only then is
    the code exchanged. Accounts are stored as candidates of THAT organization.
    """
    if error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Google OAuth error: {error}")
    if not code:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing authorization code")
    oauth = integrations.finish_oauth(db, request, state, PROVIDER)

    try:
        tokens = await exchange_code_for_tokens(
            code=code,
            client_id=settings.GOOGLE_ADS_CLIENT_ID,
            client_secret=settings.GOOGLE_ADS_CLIENT_SECRET,
            redirect_uri=settings.GOOGLE_ADS_OAUTH_REDIRECT_URI,
        )
    except GoogleOAuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        # Google only returns a refresh_token on the FIRST consent; if the user
        # already granted access before, they must revoke it at
        # myaccount.google.com/permissions and reconnect to get a new one.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google did not return a refresh token. Revoke prior access at "
            "https://myaccount.google.com/permissions and try connecting again.",
        )

    use_login_customer(None)  # discovery never goes through an MCC
    try:
        customer_ids = await list_accessible_customers(refresh_token)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Failed to list Google Ads accounts: {exc}")

    if not customer_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No accessible Google Ads accounts found for this login.")

    normalized_customer_ids = list(dict.fromkeys(normalize_customer_id(value) for value in customer_ids))
    select_required = integrations.store_oauth_grant(
        db, oauth.organization_id, oauth.user_id, PLATFORM,
        [{"id": cid} for cid in normalized_customer_ids],
        {"encrypted_refresh_token": encrypt_token(refresh_token),
         "encrypted_access_token": encrypt_token(tokens["access_token"])},
    )
    db.commit()

    frontend_url = settings.FRONTEND_URL.rstrip("/")
    redirect = RedirectResponse(
        url=f"{frontend_url}/google-ads?{'select=1' if select_required else 'connected=1'}"
    )
    clear_oauth_state_cookie(redirect)
    return redirect


def _connection_payload(connection) -> dict:
    return {
        "connected": True,
        "customer_id": connection.customer_id,
        "account_name": connection.account_name,
        "login_customer_id": connection.login_customer_id,
        "connected_at": connection.created_at.isoformat() if connection.created_at else None,
    }


@router.get("/connection")
def get_connection(
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("connections:read")),
):
    """The organization's selected Google Ads account (metadata only, never tokens)."""
    try:
        return _connection_payload(integrations.selected_connection(db, ctx.org.id, PLATFORM))
    except HTTPException:
        return {"connected": False}


@router.get("/connections")
def list_connections(
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("connections:read")),
):
    return {"connections": [integrations.public_view(PLATFORM, c) for c in integrations.list_connections(db, ctx.org.id, PLATFORM)]}


@router.post("/connection/select")
def select_connection(
    body: SelectAccountRequest,
    request: Request,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("connections:manage")),
):
    customer_id = normalize_customer_id(body.customer_id)
    connection = integrations.select_account(db, ctx, PLATFORM, customer_id, request)
    if body.login_customer_id is not None:
        _set_mcc(db, ctx, connection, body.login_customer_id)
    return _connection_payload(connection)


def _set_mcc(db: Session, ctx: OrgContext, connection, login_customer_id: Optional[str]) -> None:
    """The MCC must itself be one of the accounts authorized by this organization's grant."""
    value = normalize_customer_id(login_customer_id) if login_customer_id else None
    if value:
        known = {c.customer_id for c in integrations.list_connections(db, ctx.org.id, PLATFORM)}
        if value not in known:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail="That manager account is not part of this organization's Google grant.")
    connection.login_customer_id = value
    link = integrations.selected_link(db, ctx.org.id, PLATFORM, connection)
    if link is not None:
        link.login_customer_id = value
    db.commit()


@router.put("/connection/mcc")
def set_manager_account(
    body: McCRequest,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("connections:manage")),
):
    connection = integrations.selected_connection(db, ctx.org.id, PLATFORM)
    _set_mcc(db, ctx, connection, body.login_customer_id)
    return _connection_payload(connection)


@router.delete("/connection")
def disconnect(
    request: Request,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("connections:manage")),
):
    integrations.disconnect(db, ctx, PLATFORM, request)
    return {"message": "Disconnected"}


def _get_active_connection(db: Session, ctx: OrgContext) -> GoogleAdsConnection:
    """The organization's authorized, selected account; also selects the MCC to log in through."""
    connection = integrations.selected_connection(db, ctx.org.id, PLATFORM)
    use_login_customer(connection.login_customer_id)
    return connection


def _clean_google_ads_error(exc: Exception) -> str:
    """Extract a human-readable message from a GoogleAdsException instead of
    leaking its raw repr (RPC status, debug_error_string, request_id, etc.)
    to the client."""
    try:
        failure = exc.failure  # type: ignore[attr-defined]
        messages = [e.message for e in failure.errors if e.message]
        if messages:
            return "; ".join(messages)
    except AttributeError:
        pass
    return "Google Ads API request failed."


def _google_ads_error_status(exc: Exception) -> int:
    message = _clean_google_ads_error(exc).lower()
    access_errors = (
        "developer token is only approved for use with test accounts",
        "customer account can't be accessed",
        "caller does not have permission",
    )
    if any(fragment in message for fragment in access_errors):
        return status.HTTP_403_FORBIDDEN
    return status.HTTP_502_BAD_GATEWAY


@router.get("/campaigns")
async def get_campaigns(
    date_preset: str = "last_30d",
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("ads:read")),
):
    """Campaign performance for the organization's connected account."""
    _require_configured()
    connection = _get_active_connection(db, ctx)
    try:
        refresh_token = decrypt_token(connection.encrypted_refresh_token)
        await get_valid_access_token(db, connection)  # refreshes+persists if needed
        campaigns = await get_campaign_performance(refresh_token, connection.customer_id, date_preset=date_preset)
    except GoogleAdsConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    except GoogleAdsNotConfigured as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
    except GoogleAdsException as exc:
        raise HTTPException(status_code=_google_ads_error_status(exc), detail=_clean_google_ads_error(exc))
    return {"customer_id": connection.customer_id, "campaigns": campaigns}


@router.get("/campaigns/{campaign_id}/ads")
async def get_campaign_ads(
    campaign_id: str,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("ads:read")),
):
    """Ad-level performance for a single campaign."""
    _require_configured()
    connection = _get_active_connection(db, ctx)
    try:
        refresh_token = decrypt_token(connection.encrypted_refresh_token)
        await get_valid_access_token(db, connection)
        ads = await get_ad_performance(refresh_token, connection.customer_id, campaign_id)
    except GoogleAdsConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    except GoogleAdsNotConfigured as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
    except GoogleAdsException as exc:
        raise HTTPException(status_code=_google_ads_error_status(exc), detail=_clean_google_ads_error(exc))
    return {"campaign_id": campaign_id, "ads": ads}


# --------------------------------------------------------------------------
# Write actions. Every route requires an explicit confirm=true AND an org
# permission matching what the action can do:
#   create (always PAUSED)          ads:draft
#   pause / negative keywords       ads:publish
#   enable (starts real spend)      ads:spend + a persisted human approval with a
#                                   budget scope, atomically consumed (idempotent)
# --------------------------------------------------------------------------

async def _run_write(db: Session, ctx: OrgContext, call):
    """Shared plumbing: connection, token refresh, error translation."""
    _require_configured()
    connection = _get_active_connection(db, ctx)
    try:
        refresh_token = decrypt_token(connection.encrypted_refresh_token)
        await get_valid_access_token(db, connection)
        return await call(refresh_token, connection)
    except GoogleAdsConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    except GoogleAdsNotConfigured as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
    except GoogleAdsException as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=_clean_google_ads_error(exc))


@router.post("/campaigns", status_code=status.HTTP_201_CREATED)
async def create_campaign(
    body: CreateCampaignRequest,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("ads:draft")),
):
    """Create a new Search campaign. Always created PAUSED -- activating it
    is a separate, explicit action (see /campaigns/{id}/enable)."""
    _require_confirmed(body.confirm)
    return await _run_write(db, ctx, lambda token, conn: create_google_campaign(
        token, conn.customer_id, name=body.name, daily_budget_micros=body.daily_budget_micros,
        keywords=body.keywords))


@router.post("/campaigns/{campaign_id}/pause")
async def pause_campaign(
    campaign_id: str,
    body: ConfirmOnlyRequest,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("ads:publish")),
):
    _require_confirmed(body.confirm)
    return await _run_write(db, ctx, lambda token, conn: pause_google_campaign(token, conn.customer_id, campaign_id))


@router.post("/campaigns/{campaign_id}/enable")
async def enable_campaign(
    campaign_id: str,
    body: EnableCampaignRequest,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("ads:spend")),
):
    """Activate a campaign -- the write action that starts real spend.

    Fails closed unless ``approval_id`` names an approved, unexpired, unused
    approval of THIS organization for exactly this account + campaign whose budget
    scope covers the campaign's real daily budget (read from Google first).
    Repeating the request with the same approval returns the stored result.
    """
    _require_confirmed(body.confirm)
    _require_configured()
    connection = _get_active_connection(db, ctx)
    payload = {"campaign_id": str(campaign_id)}
    account = connection.customer_id

    replay = approvals.stored_result(db, ctx, body.approval_id, PLATFORM, account, "campaign.enable", payload)
    if replay is not None:
        return replay

    try:
        refresh_token = decrypt_token(connection.encrypted_refresh_token)
        await get_valid_access_token(db, connection)
        budget = await get_campaign_budget_micros(refresh_token, account, campaign_id)  # read-only
    except (GoogleAdsConnectionError, GoogleAdsNotConfigured) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Cannot verify the campaign budget: {exc}")
    except GoogleAdsException as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=_clean_google_ads_error(exc))

    approval = approvals.consume(db, ctx, approval_id=body.approval_id, provider=PLATFORM, account_id=account,
                                 action="campaign.enable", payload=payload, budget_micros=budget)
    try:
        result = await enable_google_campaign(refresh_token, account, campaign_id)
    except (GoogleAdsConnectionError, GoogleAdsNotConfigured) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    except GoogleAdsException as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=_clean_google_ads_error(exc))
    approvals.record_result(db, ctx, approval, result)
    return result


@router.post("/campaigns/{campaign_id}/negative-keywords")
async def add_negative_keywords(
    campaign_id: str,
    body: NegativeKeywordsRequest,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("ads:publish")),
):
    _require_confirmed(body.confirm)
    return await _run_write(db, ctx, lambda token, conn: add_google_negative_keywords(
        token, conn.customer_id, campaign_id, body.keywords))
