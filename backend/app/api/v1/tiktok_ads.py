"""TikTok Marketing API OAuth, reporting, and guarded campaign creation.

All data is scoped to the caller's ORGANIZATION: connections, advertiser links and
tokens belong to the organization that ran the OAuth flow, never to the user.
"""
from datetime import date, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.oauth_state import clear_oauth_state_cookie
from app.core.tenancy import OrgContext, require_org_permission
from app.core.token_encryption import encrypt_token
from app.database import get_db
from app.models import TikTokAdsConnection
from app.services import integrations
from app.services.tiktok_ads_service import (
    TikTokAdsApiError,
    TikTokAdsNotConfigured,
    build_oauth_url,
    create_campaign as create_tiktok_campaign,
    exchange_code_for_tokens,
    get_campaign_performance,
    get_valid_access_token,
)

router = APIRouter()
PROVIDER = "tiktok-ads"  # OAuth state provider
PLATFORM = "tiktok"      # account-link provider


class CreateCampaignRequest(BaseModel):
    name: str = Field(min_length=1, max_length=512)
    daily_budget: float = Field(gt=0)
    confirm: bool = False


class SelectAdvertiserRequest(BaseModel):
    advertiser_id: str


def _require_configured() -> None:
    if not settings.tiktok_ads_enabled:
        raise HTTPException(status_code=503, detail="TikTok Ads is not configured. Complete TikTok developer app onboarding first.")


def _require_confirmed(confirm: bool) -> None:
    if not confirm:
        raise HTTPException(status_code=400, detail="This action requires explicit confirmation. Set confirm=true after showing a preview.")


def _active_connection(db: Session, ctx: OrgContext) -> TikTokAdsConnection:
    """The organization's authorized, selected advertiser (404 otherwise)."""
    return integrations.selected_connection(db, ctx.org.id, PLATFORM)


def _date_range(preset: str) -> tuple[str, str]:
    end = date.today()
    if preset == "last_7d":
        start = end - timedelta(days=6)
    elif preset == "last_14d":
        start = end - timedelta(days=13)
    elif preset == "this_month":
        start = end.replace(day=1)
    elif preset == "last_month":
        start = end.replace(day=1) - timedelta(days=1)
        start = start.replace(day=1)
        end = end.replace(day=1) - timedelta(days=1)
    else:
        start = end - timedelta(days=29)
    return start.isoformat(), end.isoformat()


@router.get("/oauth/start")
async def oauth_start(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("connections:manage")),
):
    _require_configured()
    state = integrations.begin_oauth(db, request, response, ctx, PROVIDER)
    return {"oauth_url": build_oauth_url(settings.TIKTOK_ADS_OAUTH_REDIRECT_URI, state)}


@router.get("/oauth/callback")
async def oauth_callback(request: Request, auth_code: Optional[str] = None, code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None, db: Session = Depends(get_db)):
    if error:
        raise HTTPException(status_code=400, detail=f"TikTok OAuth error: {error}")
    authorization_code = auth_code or code
    if not authorization_code:
        raise HTTPException(status_code=400, detail="Missing TikTok OAuth authorization code or state")
    oauth = integrations.finish_oauth(db, request, state, PROVIDER)  # consumes the single-use state
    try:
        tokens = await exchange_code_for_tokens(authorization_code)
    except TikTokAdsApiError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    advertiser_ids = tokens.get("advertiser_ids") or tokens.get("advertiser_id") or []
    if isinstance(advertiser_ids, str):
        advertiser_ids = [advertiser_ids]
    advertiser_ids = [str(a) for a in advertiser_ids if str(a)]
    if not advertiser_ids or not tokens.get("refresh_token") or not tokens.get("access_token"):
        raise HTTPException(status_code=400, detail="TikTok OAuth did not return an advertiser and renewable tokens.")
    # Every advertiser of the grant becomes a candidate of THIS organization; a
    # single-advertiser grant is activated, otherwise a member picks one via
    # /connection/select right after the redirect.
    select_required = integrations.store_oauth_grant(
        db, oauth.organization_id, oauth.user_id, PLATFORM,
        [{"id": a} for a in advertiser_ids],
        {"encrypted_refresh_token": encrypt_token(tokens["refresh_token"]),
         "encrypted_access_token": encrypt_token(tokens["access_token"])},
    )
    db.commit()
    query = "select=1" if select_required else "connected=1"
    redirect = RedirectResponse(url=f"{settings.FRONTEND_URL.rstrip('/')}/tiktok-ads?{query}")
    clear_oauth_state_cookie(redirect)
    return redirect


@router.get("/connection")
def connection_status(db: Session = Depends(get_db), ctx: OrgContext = Depends(require_org_permission("connections:read"))):
    try:
        connection = _active_connection(db, ctx)
    except HTTPException:
        return {"connected": False}
    return {"connected": True, "advertiser_id": connection.advertiser_id, "account_name": connection.account_name,
            "connected_at": connection.created_at.isoformat() if connection.created_at else None}


@router.get("/connections")
def list_connections(db: Session = Depends(get_db), ctx: OrgContext = Depends(require_org_permission("connections:read"))) -> dict:
    """All TikTok advertisers authorized by THIS organization."""
    return {"connections": [integrations.public_view(PLATFORM, c) for c in integrations.list_connections(db, ctx.org.id, PLATFORM)]}


@router.post("/connection/select")
def select_connection(
    body: SelectAdvertiserRequest,
    request: Request,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("connections:manage")),
) -> dict:
    connection = integrations.select_account(db, ctx, PLATFORM, body.advertiser_id, request)
    return {
        "connected": True,
        "advertiser_id": connection.advertiser_id,
        "account_name": connection.account_name,
        "connected_at": connection.created_at.isoformat() if connection.created_at else None,
    }


@router.delete("/connection")
def disconnect_connection(request: Request, db: Session = Depends(get_db), ctx: OrgContext = Depends(require_org_permission("connections:manage"))):
    integrations.disconnect(db, ctx, PLATFORM, request)
    return {"message": "Disconnected"}


@router.get("/campaigns")
async def campaigns(date_preset: str = "last_30d", db: Session = Depends(get_db), ctx: OrgContext = Depends(require_org_permission("ads:read"))):
    _require_configured()
    connection = _active_connection(db, ctx)
    try:
        token = await get_valid_access_token(db, connection)
        start, end = _date_range(date_preset)
        rows = await get_campaign_performance(token, connection.advertiser_id, start, end)
    except (TikTokAdsApiError, TikTokAdsNotConfigured) as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"advertiser_id": connection.advertiser_id, "campaigns": rows}


@router.post("/campaigns", status_code=status.HTTP_201_CREATED)
async def create_campaign(body: CreateCampaignRequest, db: Session = Depends(get_db), ctx: OrgContext = Depends(require_org_permission("ads:draft"))):
    """Creates the campaign DISABLED (``operation_status = DISABLE`` in the service):
    a draft that cannot spend. Enabling it is not exposed here."""
    _require_configured()
    _require_confirmed(body.confirm)
    connection = _active_connection(db, ctx)
    try:
        token = await get_valid_access_token(db, connection)
        return await create_tiktok_campaign(token, connection.advertiser_id, body.name, body.daily_budget)
    except (TikTokAdsApiError, TikTokAdsNotConfigured) as exc:
        raise HTTPException(status_code=502, detail=str(exc))
