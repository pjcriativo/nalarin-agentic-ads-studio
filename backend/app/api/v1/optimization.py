"""
Optimization & Agentic Mutation Endpoints

Policy evaluation and signed approval tokens for ad-account mutations. This is
the *guardrail* layer only: nothing here (or anywhere else yet) executes
autonomous changes -- see ``AUTONOMOUS_EXECUTION_IMPLEMENTED``.

Trust rules enforced server-side:
* callers are active users (inactive accounts are rejected);
* spend / action counters are never taken from the request body -- the action
  count comes from the audit log and spend is "unverifiable" (fails closed when
  a daily-spend ceiling is configured) until a trusted metrics source exists;
* an approval token is only signed for an ad account the caller has connected.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional
import time

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.deps import get_current_active_user, get_db
from app.core.executor import (
    AUTONOMOUS_EXECUTION_IMPLEMENTED,
    AutoModePolicy,
    ProposalTokenService,
    canonical_payload_hash,
    is_kill_switch_active,
    MUTATION_ACTIONS,
)
from app.models import (
    GoogleAdsConnection,
    MetaAdsConnection,
    MutationAudit,
    TikTokAdsConnection,
    User,
)

# The "/api/v1/optimization" prefix is applied once, in app.main.
router = APIRouter(tags=["optimization"])

_PROVIDER_ALIASES = {
    "google_ads": "google_ads",
    "google": "google_ads",
    "meta": "meta",
    "facebook": "meta",
    "tiktok": "tiktok",
    "tiktok_ads": "tiktok",
}


class EvaluateMutationRequest(BaseModel):
    provider: str
    account_id: str
    action: str
    payload: Dict[str, Any]
    state: Optional[Dict[str, Any]] = Field(default_factory=dict)
    # Accepted for backward compatibility but IGNORED: client-supplied counters
    # cannot be trusted, so they never influence the verdict.
    today_spend_micros: Optional[int] = Field(default=None, deprecated=True)
    today_action_count: Optional[int] = Field(default=None, deprecated=True)


class ProposalSignRequest(BaseModel):
    provider: str
    account_id: str
    action: str
    payload: Dict[str, Any]
    state: Optional[Dict[str, Any]] = Field(default_factory=dict)


def _normalize_provider(provider: str) -> str:
    normalized = _PROVIDER_ALIASES.get((provider or "").strip().lower())
    if normalized is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported provider: {provider}. Allowed: {sorted(set(_PROVIDER_ALIASES.values()))}",
        )
    return normalized


def _digits(value: str) -> str:
    return "".join(ch for ch in (value or "") if ch.isdigit())


def _user_owns_account(db: Session, user_id: str, provider: str, account_id: str) -> bool:
    """True only if the user has this ad account connected (active or not)."""
    if provider == "google_ads":
        wanted = _digits(account_id)
        rows = db.query(GoogleAdsConnection.customer_id).filter(GoogleAdsConnection.user_id == user_id).all()
        return bool(wanted) and any(_digits(r[0]) == wanted for r in rows)
    if provider == "meta":
        wanted = account_id.replace("act_", "")
        rows = db.query(MetaAdsConnection.ad_account_id).filter(MetaAdsConnection.user_id == user_id).all()
        return any((r[0] or "").replace("act_", "") == wanted for r in rows)
    if provider == "tiktok":
        rows = db.query(TikTokAdsConnection.advertiser_id).filter(TikTokAdsConnection.user_id == user_id).all()
        return any(str(r[0]) == str(account_id) for r in rows)
    return False


def _todays_auto_action_count(db: Session, user_id: str) -> int:
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        db.query(MutationAudit)
        .filter(
            MutationAudit.user_id == user_id,
            MutationAudit.mode == "auto",
            MutationAudit.executed.is_(True),
            MutationAudit.created_at >= start,
        )
        .count()
    )


@router.get("/status")
def get_optimization_status(current_user: User = Depends(get_current_active_user)):
    """Current guardrail policy and kill-switch status."""
    policy = AutoModePolicy.from_env()
    return {
        "kill_switch_active": is_kill_switch_active(),
        # False means: nothing applies auto-mode verdicts yet. The UI must say so.
        "engine_active": AUTONOMOUS_EXECUTION_IMPLEMENTED,
        "auto_enabled": policy.auto_enabled,
        "allowed_actions": policy.allowed_actions,
        "max_budget_micros": policy.max_budget_micros,
        "max_daily_spend_micros": policy.max_daily_spend_micros,
        "max_actions_per_day": policy.max_actions_per_day,
        "operating_hours": {
            "start": policy.operating_hour_start,
            "end": policy.operating_hour_end,
            "timezone": policy.timezone_name,
        },
    }


@router.post("/evaluate")
def evaluate_mutation(
    req: EvaluateMutationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Dry-run: would this action be allowed to run automatically? Advisory only
    -- nothing is executed and the response says so."""
    if is_kill_switch_active():
        return {"ok": False, "mode": "manual", "reason": "kill-switch-engaged", "executes": False}

    if req.action not in MUTATION_ACTIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported action: {req.action}. Allowed: {MUTATION_ACTIONS}",
        )
    _normalize_provider(req.provider)

    policy = AutoModePolicy.from_env()
    verdict = policy.evaluate(
        action=req.action,
        payload=req.payload,
        today_spend_micros=None,  # no trusted spend source yet -> fail closed if a ceiling is set
        today_action_count=_todays_auto_action_count(db, current_user.id),
    )
    verdict["executes"] = AUTONOMOUS_EXECUTION_IMPLEMENTED
    return verdict


@router.post("/propose")
def create_proposal_token(
    req: ProposalSignRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Sign an approval token for a mutation on an account the caller owns."""
    if is_kill_switch_active():
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="Mutation kill-switch is engaged; approvals are suspended.",
        )
    if req.action not in MUTATION_ACTIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported action: {req.action}",
        )
    provider = _normalize_provider(req.provider)
    if not _user_owns_account(db, current_user.id, provider, req.account_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This ad account is not connected to your user.",
        )

    payload_hash = canonical_payload_hash(req.payload)
    state_hash = canonical_payload_hash(req.state or {})
    issued_at = int(time.time())

    token = ProposalTokenService.sign(
        actor=current_user.email,
        provider=provider,
        account_id=req.account_id,
        action=req.action,
        payload_hash=payload_hash,
        state_hash=state_hash,
        issued_at=issued_at,
    )

    if not token:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate proposal token (security key unconfigured)",
        )

    db.add(
        MutationAudit(
            user_id=current_user.id,
            provider=provider,
            account_id=req.account_id,
            action=req.action,
            campaign_id=str(req.payload.get("campaign_id")) if req.payload.get("campaign_id") else None,
            payload_hash=payload_hash,
            mode="manual",
            verdict_reason="proposal-signed",
            executed=False,
        )
    )
    db.commit()

    return {
        "ok": True,
        "token": token,
        "actor": current_user.email,
        "payload_hash": payload_hash,
        "state_hash": state_hash,
        "issued_at": issued_at,
    }
