"""
Optimization & Agentic Mutation Endpoints

Policy evaluation and signed approval tokens for ad-account mutations. This is
the *guardrail* layer only: nothing here (or anywhere else yet) executes
autonomous changes -- see ``AUTONOMOUS_EXECUTION_IMPLEMENTED``.

Trust rules enforced server-side:
* every endpoint needs an ACTIVE membership in an organization and the matching
  organization permission (ads:read / ads:draft / ads:spend); audits, approvals
  and accounts are all scoped to that organization (RLS + explicit filters);
* spend / action counters are never taken from the request body -- the action
  count comes from the audit log and spend is "unverifiable" (fails closed when
  a daily-spend ceiling is configured) until a trusted metrics source exists;
* an approval token is only signed for an ad account that is LINKED to the
  caller's organization (an explicit, active ad_account_links row);
* anything that can spend needs a persisted human approval with a budget scope
  (see app.services.approvals): requested by ads:draft, decided by a human with
  ads:spend, consumed atomically and idempotently by the executing endpoint.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional
import time

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.core.tenancy import OrgContext, require_org_permission
from app.services import approvals, integrations
from app.core.executor import (
    AUTONOMOUS_EXECUTION_IMPLEMENTED,
    AutoModePolicy,
    ProposalTokenService,
    canonical_payload_hash,
    is_kill_switch_active,
    MUTATION_ACTIONS,
)
from app.models import MutationAudit, SpendApproval

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


def _todays_auto_action_count(db: Session, org_id: str) -> int:
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        db.query(MutationAudit)
        .filter(
            MutationAudit.organization_id == org_id,
            MutationAudit.mode == "auto",
            MutationAudit.executed.is_(True),
            MutationAudit.created_at >= start,
        )
        .count()
    )


@router.get("/status")
def get_optimization_status(ctx: OrgContext = Depends(require_org_permission("ads:read"))):
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
    ctx: OrgContext = Depends(require_org_permission("ads:read")),
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
        today_action_count=_todays_auto_action_count(db, ctx.org.id),
    )
    verdict["executes"] = AUTONOMOUS_EXECUTION_IMPLEMENTED
    return verdict


@router.post("/propose")
def create_proposal_token(
    req: ProposalSignRequest,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("ads:draft")),
):
    """Sign an approval token for a mutation on an account linked to the caller's organization."""
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
    if not integrations.account_is_authorized(db, ctx.org.id, provider, req.account_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This ad account is not linked to your organization.",
        )

    payload_hash = canonical_payload_hash(req.payload)
    state_hash = canonical_payload_hash(req.state or {})
    issued_at = int(time.time())

    token = ProposalTokenService.sign(
        actor=ctx.user.email,
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
            organization_id=ctx.org.id,
            user_id=ctx.user.id,
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
        "actor": ctx.user.email,
        "payload_hash": payload_hash,
        "state_hash": state_hash,
        "issued_at": issued_at,
    }


# ----------------------------------------------------------------------------
# Spend approvals (human-in-the-loop, budget scope, idempotency)
# ----------------------------------------------------------------------------
class ApprovalRequest(BaseModel):
    provider: str
    account_id: str
    action: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    budget_micros: Optional[int] = Field(default=None, ge=0)
    idempotency_key: str


class ApprovalDecision(BaseModel):
    max_budget_micros: Optional[int] = Field(default=None, gt=0)
    reason: Optional[str] = Field(default=None, max_length=200)


def _approval_out(a: SpendApproval) -> Dict[str, Any]:
    return {
        "id": a.id, "provider": a.provider, "account_id": a.account_id, "action": a.action,
        "status": a.status, "requested_budget_micros": a.requested_budget_micros,
        "max_budget_micros": a.max_budget_micros, "expires_at": a.expires_at, "created_at": a.created_at,
        "requested_by_user_id": a.requested_by_user_id, "requested_by_api_key_id": a.requested_by_api_key_id,
        "approved_by_user_id": a.approved_by_user_id, "decided_at": a.decided_at, "consumed_at": a.consumed_at,
    }


@router.post("/approvals", status_code=status.HTTP_201_CREATED)
def request_approval(
    req: ApprovalRequest,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("ads:draft")),
):
    """Ask for approval of an operation that can spend. Does not execute anything."""
    from app.services.approvals import SPEND_ACTIONS
    if is_kill_switch_active():
        raise HTTPException(status_code=status.HTTP_423_LOCKED, detail="Mutation kill-switch is engaged; approvals are suspended.")
    provider = _normalize_provider(req.provider)
    if req.action not in MUTATION_ACTIONS and req.action not in SPEND_ACTIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported action: {req.action}")
    if not integrations.account_is_authorized(db, ctx.org.id, provider, req.account_id):
        raise HTTPException(status_code=403, detail="This ad account is not linked to your organization.")
    approval = approvals.request_approval(
        db, ctx, provider=provider, account_id=integrations.normalize_account(provider, req.account_id),
        action=req.action, payload=req.payload, budget_micros=req.budget_micros,
        idempotency_key=req.idempotency_key)
    return _approval_out(approval)


@router.get("/approvals")
def list_approvals(
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("ads:read")),
):
    rows = (db.query(SpendApproval).filter(SpendApproval.organization_id == ctx.org.id)
            .order_by(SpendApproval.created_at.desc()).limit(200).all())
    return [_approval_out(a) for a in rows]


@router.get("/approvals/{approval_id}")
def read_approval(
    approval_id: str,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("ads:read")),
):
    return _approval_out(approvals.get_approval(db, ctx, approval_id))


@router.post("/approvals/{approval_id}/approve")
def approve(
    approval_id: str,
    body: ApprovalDecision,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("ads:spend")),
):
    """HUMAN decision (a logged-in user with ads:spend; API keys cannot reach this
    route because it requires a user session). Must state the budget scope."""
    return _approval_out(approvals.decide(db, ctx, approval_id, True, body.max_budget_micros))


@router.post("/approvals/{approval_id}/reject")
def reject(
    approval_id: str,
    body: ApprovalDecision,
    db: Session = Depends(get_db),
    ctx: OrgContext = Depends(require_org_permission("ads:spend")),
):
    return _approval_out(approvals.decide(db, ctx, approval_id, False, None, body.reason))
