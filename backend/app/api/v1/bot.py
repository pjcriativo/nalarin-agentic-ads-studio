"""Machine-to-machine surface for the dedicated Ads Studio Hermes bot.

REST rather than a second agent protocol. A bot API key belongs to ONE
organization (``api_keys.organization_id``) and can only carry ``ads:read`` /
``ads:draft``: it can never publish or spend. The key's organization is bound to
the database session by the authentication dependency (app.core.deps), so every
read here is confined to that tenant by RLS as well as by explicit filters. The
only write the bot can make is REQUESTING a spend approval, which a human with
``ads:spend`` must grant.
"""
from types import SimpleNamespace
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.v1.overview import build_overview
from app.core.deps import require_api_key_scope
from app.database import get_db
from app.models import ApiKey, Organization
from app.services import approvals, integrations

router = APIRouter()


def _org_id(api_key: ApiKey) -> str:
    """Every bot read is scoped to the organization that owns the key."""
    if not api_key.organization_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This API key is not bound to an organization.")
    return api_key.organization_id


@router.get("/connections")
def list_connections(
    api_key: ApiKey = Depends(require_api_key_scope("ads:read")),
    db: Session = Depends(get_db),
):
    """Connection metadata only, for the key's organization. OAuth token material
    never leaves the backend."""
    org_id = _org_id(api_key)

    def selected(provider: str, id_attr: str):
        try:
            connection = integrations.selected_connection(db, org_id, provider)
        except HTTPException:
            return []
        return [{id_attr: getattr(connection, id_attr), "account_name": connection.account_name,
                 "connected_at": connection.created_at}]

    return {
        "google_ads": selected("google_ads", "customer_id"),
        "tiktok_ads": selected("tiktok", "advertiser_id"),
        "meta_ads": selected("meta", "ad_account_id"),
        "capabilities": ["ads:read", "ads:draft"],
        "writes_enabled": False,
    }


@router.get("/safety")
def safety_policy(_api_key: ApiKey = Depends(require_api_key_scope("ads:read"))):
    """A machine-readable declaration that the bot cannot spend or publish."""
    return {
        "can_read_performance": True,
        "can_draft_recommendations": True,
        "can_request_spend_approval": True,
        "can_create_campaigns": False,
        "can_pause_campaigns": False,
        "can_enable_campaigns": False,
        "can_spend": False,
        "operator_action_required": "A human with ads:spend must approve any operation that can spend, in the Ads Studio dashboard.",
    }


@router.get("/spend")
async def read_spend(
    date_preset: str = "last_7d",
    api_key: ApiKey = Depends(require_api_key_scope("ads:read")),
    db: Session = Depends(get_db),
):
    """Aggregated total spend across the organization's connected platforms."""
    overview = await build_overview(db, _org_id(api_key), date_preset)
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
    """Performance of the organization that owns the bot key."""
    return await build_overview(db, _org_id(api_key), date_preset)


class BotApprovalRequest(BaseModel):
    provider: str
    account_id: str
    action: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    budget_micros: Optional[int] = Field(default=None, ge=0)
    idempotency_key: str


@router.post("/approvals", status_code=status.HTTP_201_CREATED)
def request_spend_approval(
    body: BotApprovalRequest,
    api_key: ApiKey = Depends(require_api_key_scope("ads:draft")),
    db: Session = Depends(get_db),
):
    """The bot asks; a human decides. Creates a PENDING approval for an ad account
    the key's organization is linked to. Never executes anything."""
    from app.core.executor import MUTATION_ACTIONS
    from app.services.approvals import SPEND_ACTIONS

    org_id = _org_id(api_key)
    provider = {"google": "google_ads", "google_ads": "google_ads", "meta": "meta", "facebook": "meta",
                "tiktok": "tiktok", "tiktok_ads": "tiktok"}.get((body.provider or "").lower())
    if provider is None:
        raise HTTPException(status_code=400, detail="Unsupported provider")
    if body.action not in MUTATION_ACTIONS and body.action not in SPEND_ACTIONS:
        raise HTTPException(status_code=400, detail="Unsupported action")
    if not integrations.account_is_authorized(db, org_id, provider, body.account_id):
        raise HTTPException(status_code=404, detail="Ad account is not linked to this organization.")
    org = db.query(Organization).filter(Organization.id == org_id).one()
    ctx = SimpleNamespace(org=org, user=None)  # machine actor: no user
    approval = approvals.request_approval(
        db, ctx, provider=provider, account_id=integrations.normalize_account(provider, body.account_id),
        action=body.action, payload=body.payload, budget_micros=body.budget_micros,
        idempotency_key=body.idempotency_key, api_key_id=api_key.id)
    return {"approval_id": approval.id, "status": approval.status, "expires_at": approval.expires_at}
