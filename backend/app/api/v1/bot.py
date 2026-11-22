"""Machine-to-machine surface for the dedicated Ads Studio Hermes bot.

This is intentionally REST rather than a second custom agent protocol. The bot
API key can only carry `ads:read` (or a future `ads:draft`) scopes; no endpoint
here can create, enable, pause, publish, or spend against an ad platform.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.deps import require_api_key_scope
from app.database import get_db
from app.models import ApiKey, GoogleAdsConnection, TikTokAdsConnection
from app.api.v1.overview import build_overview

router = APIRouter()


def _require_key_owner(api_key: ApiKey) -> str:
    """Every bot read is scoped to the user that issued the key; an unbound key
    must never see anyone's data."""
    if not api_key.created_by_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This API key is not bound to an owner.",
        )
    return api_key.created_by_user_id


@router.get("/connections")
def list_connections(
    api_key: ApiKey = Depends(require_api_key_scope("ads:read")),
    db: Session = Depends(get_db),
):
    """Return connection metadata only, for the user who owns this API key.
    OAuth token material never leaves the backend."""
    owner_id = _require_key_owner(api_key)
    google = (
        db.query(GoogleAdsConnection)
        .filter(GoogleAdsConnection.user_id == owner_id, GoogleAdsConnection.is_active.is_(True))
        .all()
    )
    tiktok = (
        db.query(TikTokAdsConnection)
        .filter(TikTokAdsConnection.user_id == owner_id, TikTokAdsConnection.is_active.is_(True))
        .all()
    )
    return {
        "google_ads": [
            {"customer_id": connection.customer_id, "account_name": connection.account_name, "connected_at": connection.created_at}
            for connection in google
        ],
        "tiktok_ads": [
            {"advertiser_id": connection.advertiser_id, "account_name": connection.account_name, "connected_at": connection.created_at}
            for connection in tiktok
        ],
        "capabilities": ["ads:read"],
        "writes_enabled": False,
    }


@router.get("/safety")
def safety_policy(_api_key: ApiKey = Depends(require_api_key_scope("ads:read"))):
    """A machine-readable declaration that the bot cannot spend or publish."""
    return {
        "can_read_performance": True,
        "can_draft_recommendations": False,
        "can_create_campaigns": False,
        "can_pause_campaigns": False,
        "can_enable_campaigns": False,
        "can_spend": False,
        "operator_action_required": "Use the Ads Studio dashboard for any write action after human review.",
    }


@router.get("/spend")
async def read_spend(
    date_preset: str = "last_7d",
    api_key: ApiKey = Depends(require_api_key_scope("ads:read")),
    db: Session = Depends(get_db),
):
    """Get aggregated total spend across all connected platforms for the bot."""
    owner_id = _require_key_owner(api_key)

    # build_overview returns {"campaigns": [...], "errors": {...}} -- iterate the
    # campaign rows, not the dict (which would yield its keys).
    overview = await build_overview(db, owner_id, date_preset)
    campaigns = overview.get("campaigns", [])

    total_spend = sum(float(campaign.get("spend", 0.0) or 0.0) for campaign in campaigns)

    return {
        "date_preset": date_preset,
        "total_spend": round(total_spend, 2),
        "currency": "USD",
        "active_campaigns": len([c for c in campaigns if str(c.get("status", "")).upper() == "ENABLED"]),
        "platform_errors": overview.get("errors", {}),
    }


@router.get("/overview")
async def read_overview(
    date_preset: str = "last_30d",
    api_key: ApiKey = Depends(require_api_key_scope("ads:read")),
    db: Session = Depends(get_db),
):
    """Read performance associated with the user who issued the bot key."""
    owner_id = _require_key_owner(api_key)
    return await build_overview(db, owner_id, date_preset)
