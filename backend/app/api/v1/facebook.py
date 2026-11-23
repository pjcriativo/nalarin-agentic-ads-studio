from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from typing import Dict, Any, Optional
from app.services.facebook_service import FacebookService
from app.models import FacebookAd, FacebookAdSet, FacebookCampaign
from app.database import get_db
from app.core.config import settings
from app.core.executor import canonical_payload_hash  # noqa: F401
from app.core.oauth_state import clear_oauth_state_cookie
from app.core.tenancy import OrgContext, client_or_new_id, require_org_permission
from app.core.storage import require_own_media, resolve_media_reference
from app.core.token_encryption import encrypt_token, decrypt_token
from app.services import approvals, integrations
from app.services.meta_ads_oauth import build_oauth_url, exchange_code, list_ad_accounts, MetaOAuthError
from sqlalchemy.orm import Session

router = APIRouter()
PROVIDER = "meta-ads"  # OAuth state provider
PLATFORM = "meta"      # account-link provider


class SelectMetaAccountRequest(BaseModel):
    ad_account_id: str


def _require_meta_configured():
    if not settings.facebook_ads_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Meta Ads OAuth is not configured on this server.",
        )


def get_facebook_service(
    ad_account_id: Optional[str] = None,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("ads:read")),
):
    """Meta client for the caller's ORGANIZATION.

    Uses the organization's selected, authorized account and its own token. A
    ``ad_account_id`` sent by the caller must be linked to the organization
    (404 otherwise): the token may technically reach other accounts, the
    organization may not.
    """
    connection = integrations.selected_connection(db, ctx.org.id, PLATFORM)
    if ad_account_id:
        integrations.require_authorized_account(db, ctx, PLATFORM, ad_account_id)
    service = FacebookService(
        access_token=decrypt_token(connection.encrypted_access_token),
        ad_account_id=connection.ad_account_id,
        app_id=settings.FACEBOOK_APP_ID,
        app_secret=settings.FACEBOOK_APP_SECRET,
    )
    try:
        if not service.api:
            service.initialize()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Facebook service initialization failed: {type(e).__name__}")
    return service


def _require_object_in_linked_account(service: FacebookService, db: Session, ctx: OrgContext, object_id: Optional[str]) -> None:
    """A campaign / ad set id from the request must live in an account the
    organization is linked to (the token may reach others)."""
    if not object_id:
        return
    try:
        account = service.object_account_id(object_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Object not found")
    if not integrations.account_is_authorized(db, ctx.org.id, PLATFORM, account):
        raise HTTPException(status_code=404, detail="Object not found")


def _budget_micros(data: Dict[str, Any]) -> Optional[int]:
    """Meta budgets are in minor currency units (cents): scale to micros."""
    raw = data.get("daily_budget") or data.get("dailyBudget")
    try:
        return int(float(raw)) * 10_000 if raw is not None else None
    except (TypeError, ValueError):
        return None


def _gate_creation(db: Session, ctx: OrgContext, service: FacebookService, kind: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Creating something PAUSED is a draft (``ads:draft``, already required by the
    route). Anything that would go live can spend: it needs ``ads:spend``, an
    explicit budget and a persisted human approval; ACTIVE ads/creatives are not
    creatable here at all (activate through the approved flow). Fails closed."""
    data = dict(data)
    approval_id = data.pop("approval_id", None) or data.pop("approvalId", None)
    status_value = str(data.get("status") or "PAUSED").upper()
    if status_value == "PAUSED":
        data["status"] = "PAUSED"
        return data
    if kind not in ("campaign", "adset"):
        raise HTTPException(status_code=403, detail="Ads are created PAUSED; activation needs an approved spend operation")
    if not ctx.can("ads:spend"):
        raise HTTPException(status_code=403, detail="Creating an ACTIVE object requires the ads:spend permission")
    budget = _budget_micros(data)
    if not budget or budget <= 0:
        raise HTTPException(status_code=400, detail="An explicit daily budget is required to create an ACTIVE object")
    account = integrations.normalize_account(PLATFORM, service.ad_account_id or "")
    approval = approvals.consume(db, ctx, approval_id=approval_id, provider=PLATFORM, account_id=account,
                                 action="campaign.create.active", payload={"kind": kind, **data}, budget_micros=budget)
    data["_approval"] = approval.id
    return data


def _finish_gate(db: Session, ctx: OrgContext, data: Dict[str, Any], result: Any) -> None:
    approval_id = data.get("_approval")
    if approval_id:
        approval = approvals.get_approval(db, ctx, approval_id)
        approvals.record_result(db, ctx, approval, {"id": str(dict(result).get("id", ""))})


@router.get("/oauth/start")
async def start_meta_oauth(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("connections:manage")),
):
    _require_meta_configured()
    state_token = integrations.begin_oauth(db, request, response, ctx, PROVIDER)
    return {"oauth_url": build_oauth_url(settings.FACEBOOK_APP_ID, settings.FACEBOOK_OAUTH_REDIRECT_URI, state_token)}


@router.get("/oauth/callback")
async def meta_oauth_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
    db: Session = Depends(get_db),
):
    if error:
        raise HTTPException(status_code=400, detail=error_description or f"Meta OAuth error: {error}")
    if not code:
        raise HTTPException(status_code=400, detail="Missing Meta OAuth authorization code or state")
    oauth = integrations.finish_oauth(db, request, state, PROVIDER)  # single use; re-authorizes the user
    try:
        token_data = await exchange_code(
            code,
            settings.FACEBOOK_APP_ID,
            settings.FACEBOOK_APP_SECRET,
            settings.FACEBOOK_OAUTH_REDIRECT_URI,
        )
        accounts = await list_ad_accounts(token_data["access_token"])
    except (ValueError, MetaOAuthError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not accounts:
        raise HTTPException(status_code=400, detail="No Meta ad accounts are available for this login.")

    expires_in = token_data.get("expires_in")
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in) if isinstance(expires_in, (int, float)) else None
    select_required = integrations.store_oauth_grant(
        db, oauth.organization_id, oauth.user_id, PLATFORM,
        [{"id": str(a.get("id") or ""), "name": a.get("name")} for a in accounts],
        {"encrypted_access_token": encrypt_token(token_data["access_token"]), "access_token_expires_at": expires_at},
    )
    db.commit()

    redirect = RedirectResponse(
        url=f"{settings.FRONTEND_URL.rstrip('/')}/facebook-campaigns?{'select=1' if select_required else 'connected=1'}"
    )
    clear_oauth_state_cookie(redirect)
    return redirect


@router.get("/connection")
def meta_connection_status(
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("connections:read")),
):
    try:
        connection = integrations.selected_connection(db, ctx.org.id, PLATFORM)
    except HTTPException:
        return {"connected": False}
    expires_at = connection.access_token_expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return {
        "connected": True,
        "ad_account_id": connection.ad_account_id,
        "account_name": connection.account_name,
        "connected_at": connection.created_at.isoformat() if connection.created_at else None,
        # lets the UI flag a soon/lapsed token so the operator reconnects BEFORE campaigns fail
        "token_expires_at": expires_at.isoformat() if expires_at else None,
    }


@router.get("/connections")
def meta_connections(
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("connections:read")),
):
    return {"connections": [integrations.public_view(PLATFORM, c) for c in integrations.list_connections(db, ctx.org.id, PLATFORM)]}


@router.post("/connection/select")
def select_meta_connection(
    body: SelectMetaAccountRequest,
    request: Request,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("connections:manage")),
):
    connection = integrations.select_account(db, ctx, PLATFORM, body.ad_account_id, request)
    return {"connected": True, "ad_account_id": connection.ad_account_id, "account_name": connection.account_name}


@router.delete("/connection")
def disconnect_meta_connection(
    request: Request,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("connections:manage")),
):
    integrations.disconnect(db, ctx, PLATFORM, request)
    return {"message": "Disconnected"}

@router.get("/accounts")
def get_ad_accounts(
    service: FacebookService = Depends(get_facebook_service),
    ctx: OrgContext = Depends(require_org_permission("ads:read"))
):
    try:
        return service.get_ad_accounts()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/campaigns")
def read_campaigns(
    ad_account_id: Optional[str] = None,
    service: FacebookService = Depends(get_facebook_service),
    ctx: OrgContext = Depends(require_org_permission("ads:read"))
):
    try:
        campaigns = service.get_campaigns(ad_account_id)
        # Convert FB objects to dicts
        return [dict(c) for c in campaigns]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/campaigns")
def create_campaign(
    campaign: Dict[str, Any],
    ad_account_id: Optional[str] = None,
    service: FacebookService = Depends(get_facebook_service),
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("ads:draft"))
):
    campaign = _gate_creation(db, ctx, service, "campaign", campaign)
    try:
        result = service.create_campaign({k: v for k, v in campaign.items() if k != "_approval"}, ad_account_id)
        _finish_gate(db, ctx, campaign, result)
        return dict(result)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/pixels")
def read_pixels(
    ad_account_id: Optional[str] = None,
    service: FacebookService = Depends(get_facebook_service),
    ctx: OrgContext = Depends(require_org_permission("ads:read"))
):
    try:
        pixels = service.get_pixels(ad_account_id)
        # Convert FB objects to dicts
        return [dict(p) for p in pixels]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/pages")
def read_pages(
    service: FacebookService = Depends(get_facebook_service),
    ctx: OrgContext = Depends(require_org_permission("ads:read"))
):
    try:
        pages = service.get_pages()
        return pages
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/adsets")
def read_adsets(
    ad_account_id: Optional[str] = None,
    campaign_id: Optional[str] = None,
    service: FacebookService = Depends(get_facebook_service),
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("ads:read"))
):
    _require_object_in_linked_account(service, db, ctx, campaign_id)
    try:
        adsets = service.get_adsets(ad_account_id, campaign_id)
        return [dict(a) for a in adsets]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/adsets")
def create_adset(
    adset: Dict[str, Any],
    ad_account_id: Optional[str] = None,
    service: FacebookService = Depends(get_facebook_service),
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("ads:draft"))
):
    _require_object_in_linked_account(service, db, ctx, adset.get("campaign_id") or adset.get("campaignId"))
    adset = _gate_creation(db, ctx, service, "adset", adset)
    try:
        result = service.create_adset({k: v for k, v in adset.items() if k != "_approval"}, ad_account_id)
        _finish_gate(db, ctx, adset, result)
        return dict(result)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/creatives")
def create_creative(
    creative: Dict[str, Any],
    ad_account_id: Optional[str] = None,
    service: FacebookService = Depends(get_facebook_service),
    ctx: OrgContext = Depends(require_org_permission("ads:draft"))
):
    try:
        result = service.create_creative(creative, ad_account_id)
        return dict(result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/ads")
def create_ad(
    ad: Dict[str, Any],
    ad_account_id: Optional[str] = None,
    service: FacebookService = Depends(get_facebook_service),
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("ads:draft"))
):
    _require_object_in_linked_account(service, db, ctx, ad.get("adset_id") or ad.get("adsetId"))
    ad = _gate_creation(db, ctx, service, "ad", ad)
    try:
        result = service.create_ad(ad, ad_account_id)
        return dict(result)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/ads")
def read_ads(
    adset_id: str,
    service: FacebookService = Depends(get_facebook_service),
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("ads:read"))
):
    _require_object_in_linked_account(service, db, ctx, adset_id)
    try:
        ads = service.get_ads(adset_id)
        return [dict(a) for a in ads]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/campaigns/save")
def save_campaign_locally(
    campaign_data: Dict[str, Any],
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("campaigns:write"))
):
    try:
        # Check if exists
        campaign_id = client_or_new_id(campaign_data.get('id'), loose=True)
        existing = db.query(FacebookCampaign).filter(
            FacebookCampaign.organization_id == ctx.org.id, FacebookCampaign.id == campaign_id).first()
        if existing:
            return {"message": "Campaign already exists", "id": existing.id}

        # Handle daily_budget casting
        daily_budget = campaign_data.get('dailyBudget')
        if daily_budget is not None:
            daily_budget = int(float(daily_budget))

        new_campaign = FacebookCampaign(
            id=campaign_id,
            organization_id=ctx.org.id,
            name=campaign_data.get('name'),
            objective=campaign_data.get('objective'),
            budget_type=campaign_data.get('budgetType', 'ABO'),
            daily_budget=daily_budget,
            bid_strategy=campaign_data.get('bidStrategy'),
            status=campaign_data.get('status'),
            fb_campaign_id=campaign_data.get('fbCampaignId')
        )
        db.add(new_campaign)
        db.commit()
        db.refresh(new_campaign)
        return {"message": "Campaign saved locally", "id": new_campaign.id}
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        print(f"Error saving campaign locally: {type(e).__name__}")
        raise HTTPException(status_code=500, detail="Could not save locally")

@router.post("/adsets/save")
def save_adset_locally(
    adset_data: Dict[str, Any],
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("campaigns:write"))
):
    try:
        # Check if exists
        adset_id = client_or_new_id(adset_data.get('id'), loose=True)
        existing = db.query(FacebookAdSet).filter(
            FacebookAdSet.organization_id == ctx.org.id, FacebookAdSet.id == adset_id).first()
        if existing:
            return {"message": "AdSet already exists", "id": existing.id}
            
        # Ensure campaign exists (FK check)
        campaign_id = adset_data.get('campaignId')
        if not campaign_id:
             raise HTTPException(status_code=400, detail="campaignId is required")
             
        # The campaign must belong to THIS organization (also enforced by a composite FK)
        if not db.query(FacebookCampaign.id).filter(
                FacebookCampaign.organization_id == ctx.org.id, FacebookCampaign.id == campaign_id).first():
            raise HTTPException(status_code=404, detail="Campaign not found")

        # Handle numeric fields casting
        daily_budget = adset_data.get('dailyBudget')
        if daily_budget is not None:
            daily_budget = int(float(daily_budget))
            
        bid_amount = adset_data.get('bidAmount')
        if bid_amount is not None:
            bid_amount = int(float(bid_amount))

        new_adset = FacebookAdSet(
            id=adset_id,
            organization_id=ctx.org.id,
            campaign_id=campaign_id,
            name=adset_data.get('name'),
            optimization_goal=adset_data.get('optimizationGoal'),
            daily_budget=daily_budget,
            bid_strategy=adset_data.get('bidStrategy'),
            bid_amount=bid_amount,
            targeting=adset_data.get('targeting'),
            pixel_id=adset_data.get('pixelId'),
            conversion_event=adset_data.get('conversionEvent'),
            status=adset_data.get('status'),
            fb_adset_id=adset_data.get('fbAdsetId')
        )
        db.add(new_adset)
        db.commit()
        db.refresh(new_adset)
        return {"message": "AdSet saved locally", "id": new_adset.id}
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        print(f"Error saving adset locally: {type(e).__name__}")
        raise HTTPException(status_code=500, detail="Could not save locally")

@router.post("/ads/save")
def save_ad_locally(
    ad_data: Dict[str, Any],
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("campaigns:write"))
):
    try:
        adset_ref = ad_data.get('adsetId')
        if not adset_ref or not db.query(FacebookAdSet.id).filter(
                FacebookAdSet.organization_id == ctx.org.id, FacebookAdSet.id == adset_ref).first():
            raise HTTPException(status_code=404, detail="AdSet not found")
        require_own_media(ctx.org.id, ad_data.get('imageUrl'), ad_data.get('videoUrl'), ad_data.get('thumbnailUrl'))
        ad_id = client_or_new_id(ad_data.get('id'), loose=True)
        if db.query(FacebookAd.id).filter(FacebookAd.organization_id == ctx.org.id, FacebookAd.id == ad_id).first():
            return {"message": "Ad already exists", "id": ad_id}

        new_ad = FacebookAd(
            id=ad_id,
            organization_id=ctx.org.id,
            adset_id=ad_data.get('adsetId'),
            name=ad_data.get('name'),
            creative_name=ad_data.get('creativeName'),
            image_url=ad_data.get('imageUrl'),
            # Video support fields
            media_type=ad_data.get('mediaType', 'image'),
            video_url=ad_data.get('videoUrl'),
            video_id=ad_data.get('videoId'),
            thumbnail_url=ad_data.get('thumbnailUrl'),
            bodies=ad_data.get('bodies'),
            headlines=ad_data.get('headlines'),
            description=ad_data.get('description'),
            cta=ad_data.get('cta'),
            website_url=ad_data.get('websiteUrl'),
            status=ad_data.get('status'),
            fb_ad_id=ad_data.get('fbAdId'),
            fb_creative_id=ad_data.get('fbCreativeId')
        )
        db.add(new_ad)
        db.commit()
        db.refresh(new_ad)
        return {"message": "Ad saved locally", "id": new_ad.id}
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        print(f"Error saving ad locally: {type(e).__name__}")
        raise HTTPException(status_code=500, detail="Could not save locally")

@router.post("/upload-image")
def upload_image(
    data: Dict[str, str],
    ad_account_id: Optional[str] = None,
    service: FacebookService = Depends(get_facebook_service),
    ctx: OrgContext = Depends(require_org_permission("ads:draft"))
):
    try:
        image_url = data.get("image_url")
        if not image_url:
            raise HTTPException(status_code=400, detail="image_url is required")
        image_hash = service.upload_image(resolve_media_reference(ctx.org.id, image_url), ad_account_id)
        return {"image_hash": image_hash}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/upload-video")
def upload_video(
    data: Dict[str, Any],
    ad_account_id: Optional[str] = None,
    service: FacebookService = Depends(get_facebook_service),
    ctx: OrgContext = Depends(require_org_permission("ads:draft"))
):
    """Upload a video to Facebook Ad Library.

    Request body:
        video_url: URL of the video to upload
        wait_for_ready: Whether to wait for processing (default True)
        timeout: Max seconds to wait (default 600)

    Returns:
        video_id: Facebook video ID
        status: 'processing', 'ready', or 'error'
        thumbnails: List of auto-generated thumbnail URLs (if ready)
    """
    video_url = data.get("video_url")
    if not video_url:
        raise HTTPException(status_code=400, detail="video_url is required")

    try:
        wait_for_ready = data.get("wait_for_ready", True)
        timeout = data.get("timeout", 600)

        result = service.upload_video(
            resolve_media_reference(ctx.org.id, video_url),
            ad_account_id,
            wait_for_ready=wait_for_ready,
            timeout=timeout
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/video-status/{video_id}")
def get_video_status(
    video_id: str,
    service: FacebookService = Depends(get_facebook_service),
    ctx: OrgContext = Depends(require_org_permission("ads:read"))
):
    """Check the processing status of a video.

    Returns:
        status: 'processing', 'ready', or 'error'
        video_id: The video ID
        length: Video duration in seconds (if ready)
    """
    try:
        return service.get_video_status(video_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/video-thumbnails/{video_id}")
def get_video_thumbnails(
    video_id: str,
    service: FacebookService = Depends(get_facebook_service),
    ctx: OrgContext = Depends(require_org_permission("ads:read"))
):
    """Get auto-generated thumbnails for a video.

    Returns:
        thumbnails: List of thumbnail URLs
    """
    try:
        thumbnails = service.get_video_thumbnails(video_id)
        return {"thumbnails": thumbnails}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/locations/search")
def search_locations(
    q: str,
    type: str = "city",
    limit: int = 10,
    ad_account_id: Optional[str] = None,
    service: FacebookService = Depends(get_facebook_service),
    ctx: OrgContext = Depends(require_org_permission("ads:read"))
):
    try:
        locations = service.search_locations(q, type, limit, ad_account_id)
        return [dict(loc) for loc in locations]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

