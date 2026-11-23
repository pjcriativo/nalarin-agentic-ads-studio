"""Persisted human approval + budget scope + idempotency for spending operations.

An operation that can spend money (activating a campaign, creating an ACTIVE
object on a platform...) may run only against an ``approved`` row of
``spend_approvals`` that

* belongs to the caller's organization,
* names exactly this provider / ad account / action / payload hash,
* was approved by a HUMAN member holding ``ads:spend`` (never an API key),
* carries a budget scope (``max_budget_micros``) at least as large as what the
  operation will really commit,
* has not expired and has not been used.

Using it is one atomic ``UPDATE ... WHERE status = 'approved' ... RETURNING``
(consume): two concurrent executions cannot both pass. The outcome is stored on
the row, so repeating the same request (same approval / idempotency key) returns
the stored result instead of spending twice. If the platform call fails AFTER the
approval was consumed, the approval stays consumed (fail closed: a new approval
is needed) -- an ambiguous outcome must never be retried blindly.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import audit_event
from app.core.executor import canonical_payload_hash
from app.core.tenancy import OrgContext
from app.models import MutationAudit, SpendApproval

APPROVAL_TTL_MINUTES = 30
SPEND_ACTIONS = ("campaign.enable", "campaign.create.active", "budget.update")
IDEMPOTENCY_KEY_MAX = 128


def _now():
    return datetime.now(timezone.utc)


def _clean_key(key: Optional[str]) -> str:
    key = (key or "").strip()
    if not key or len(key) > IDEMPOTENCY_KEY_MAX:
        raise HTTPException(status_code=400, detail="An idempotency key (1-128 chars) is required")
    return key


def _audit(db: Session, ctx: OrgContext, approval: SpendApproval, reason: str, executed: bool = False,
           mode: str = "manual") -> None:
    db.add(MutationAudit(
        organization_id=ctx.org.id, user_id=ctx.user.id if ctx.user else None,
        provider=approval.provider, account_id=approval.account_id, action=approval.action,
        payload_hash=approval.payload_hash, mode=mode, verdict_reason=reason, executed=executed,
        approval_id=approval.id, api_key_id=approval.requested_by_api_key_id,
    ))


def request_approval(db: Session, ctx: OrgContext, *, provider: str, account_id: str, action: str,
                     payload: Dict[str, Any], budget_micros: Optional[int], idempotency_key: Optional[str],
                     api_key_id: Optional[str] = None) -> SpendApproval:
    """Create (or return, when repeated with the same key and content) a PENDING approval."""
    key = _clean_key(idempotency_key)
    payload_hash = canonical_payload_hash(payload)
    existing = db.query(SpendApproval).filter(
        SpendApproval.organization_id == ctx.org.id, SpendApproval.idempotency_key == key).first()
    if existing is not None:
        same = (existing.provider, existing.account_id, existing.action, existing.payload_hash) == \
               (provider, account_id, action, payload_hash)
        if not same:
            raise HTTPException(status_code=409, detail="Idempotency key already used for a different operation")
        return existing
    approval = SpendApproval(
        organization_id=ctx.org.id, provider=provider, account_id=account_id, action=action,
        payload_hash=payload_hash, requested_budget_micros=budget_micros, idempotency_key=key,
        status="pending", requested_by_user_id=None if api_key_id else ctx.user.id,
        requested_by_api_key_id=api_key_id, expires_at=_now() + timedelta(minutes=APPROVAL_TTL_MINUTES),
    )
    db.add(approval)
    try:
        db.flush()
    except IntegrityError:  # concurrent request with the same key
        db.rollback()
        raise HTTPException(status_code=409, detail="Idempotency key already in use")
    _audit(db, ctx, approval, "approval-requested")
    db.commit()
    return approval


def get_approval(db: Session, ctx: OrgContext, approval_id: str) -> SpendApproval:
    approval = db.query(SpendApproval).filter(
        SpendApproval.organization_id == ctx.org.id, SpendApproval.id == approval_id).first()
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    return approval


def decide(db: Session, ctx: OrgContext, approval_id: str, approve: bool,
           max_budget_micros: Optional[int], reason: Optional[str] = None) -> SpendApproval:
    """Human decision. The caller must be a user (not an API key) with ``ads:spend``."""
    approval = get_approval(db, ctx, approval_id)
    if approval.status != "pending":
        raise HTTPException(status_code=409, detail=f"Approval is already {approval.status}")
    if approval.expires_at <= _now():
        approval.status = "expired"
        db.commit()
        raise HTTPException(status_code=409, detail="Approval expired")
    if approve:
        if not max_budget_micros or max_budget_micros <= 0:
            raise HTTPException(status_code=422, detail="max_budget_micros (the budget scope) is required to approve")
        if approval.requested_budget_micros is not None and max_budget_micros < approval.requested_budget_micros:
            raise HTTPException(status_code=422, detail="The budget scope is smaller than the requested budget")
        approval.max_budget_micros = max_budget_micros
    approval.status = "approved" if approve else "rejected"
    approval.approved_by_user_id = ctx.user.id
    approval.decided_at = _now()
    _audit(db, ctx, approval, "approved" if approve else f"rejected:{(reason or '')[:80]}")
    audit_event(db, action="spend.approved" if approve else "spend.rejected", organization_id=ctx.org.id,
                actor_user_id=ctx.user.id, target=approval.id,
                detail={"provider": approval.provider, "action": approval.action,
                        "max_budget_micros": approval.max_budget_micros})
    db.commit()
    return approval


def consume(db: Session, ctx: OrgContext, *, approval_id: Optional[str], provider: str, account_id: str,
            action: str, payload: Dict[str, Any], budget_micros: int) -> SpendApproval:
    """Atomically spend the approval, or fail closed. Never executes anything itself."""
    if not approval_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="This operation can spend money and requires a human-approved approval_id")
    payload_hash = canonical_payload_hash(payload)
    row = db.execute(text(
        "UPDATE spend_approvals SET status = 'consumed', consumed_at = now() "
        "WHERE id = :id AND organization_id = :org AND status = 'approved' AND expires_at > now() "
        "AND provider = :provider AND account_id = :account AND action = :action AND payload_hash = :hash "
        "AND approved_by_user_id IS NOT NULL AND max_budget_micros IS NOT NULL AND max_budget_micros >= :budget "
        "RETURNING id"
    ), {"id": approval_id, "org": ctx.org.id, "provider": provider, "account": account_id, "action": action,
        "hash": payload_hash, "budget": int(budget_micros)}).first()
    if row is None:
        db.rollback()
        approval = db.query(SpendApproval).filter(
            SpendApproval.organization_id == ctx.org.id, SpendApproval.id == approval_id).first()
        reason = "unknown approval" if approval is None else (
            f"approval is {approval.status}" if approval.status != "approved" else
            "approval does not match this operation, account, payload or budget scope")
        db.add(MutationAudit(
            organization_id=ctx.org.id, user_id=ctx.user.id if ctx.user else None, provider=provider,
            account_id=account_id, action=action, payload_hash=payload_hash, mode="rejected",
            verdict_reason=f"approval-refused:{reason}"[:200], executed=False, approval_id=approval_id))
        db.commit()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Spend refused: {reason}")
    db.commit()
    return db.query(SpendApproval).filter(SpendApproval.id == approval_id).one()


def stored_result(db: Session, ctx: OrgContext, approval_id: Optional[str], provider: str, account_id: str,
                  action: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Idempotent replay: the stored outcome of an approval that already ran the
    SAME operation. ``None`` when there is nothing to replay."""
    if not approval_id:
        return None
    approval = db.query(SpendApproval).filter(
        SpendApproval.organization_id == ctx.org.id, SpendApproval.id == approval_id).first()
    if approval is None or approval.status != "consumed":
        return None
    same = (approval.provider, approval.account_id, approval.action, approval.payload_hash) == \
           (provider, account_id, action, canonical_payload_hash(payload))
    if not same:
        return None
    if approval.result is None:
        raise HTTPException(status_code=409, detail="This approval was used and its outcome is unknown; "
                                                    "check the platform and request a new approval")
    return {**approval.result, "idempotent_replay": True}


def record_result(db: Session, ctx: OrgContext, approval: SpendApproval, result: Dict[str, Any]) -> None:
    approval.result = result
    _audit(db, ctx, approval, "executed", executed=True)
    db.commit()
