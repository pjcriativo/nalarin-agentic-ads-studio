"""AI Gateway administration (per organization).

RBAC: ``ai:read`` (analyst and up) sees models, routing, budgets and usage;
``ai:manage`` (admin, owner) manages credentials (BYOK), models, routing and budgets.
Credentials are NEVER returned: only provider, label, status and the last four characters.
There is deliberately no generic "send a prompt" endpoint here: AI runs through product
features (and later agents), each with its own permission and its own operation name.
"""
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai import credentials as cred
from app.ai import errors as E
from app.ai.adapters import PROVIDER_ADAPTERS, PROVIDERS
from app.ai.api_errors import to_http
from app.ai.budget import window_start
from app.ai.pricing import micros_to_usd, usd_to_micros
from app.core.audit import audit_event
from app.core.tenancy import OrgContext, require_org_permission
from app.database import get_db
from app.models import AiBudget, AiCredential, AiModel, AiRouting, AiUsageLog

router = APIRouter()
_OPERATION_RE = re.compile(r"^[a-z0-9_.\-]{1,64}$")


def _provider(provider: str) -> str:
    if provider not in PROVIDER_ADAPTERS:
        raise HTTPException(status_code=422, detail=f"Unsupported provider. Allowed: {list(PROVIDERS)}")
    return provider


# ----------------------------------------------------------------------------- providers
@router.get("/providers")
def list_providers(db: Session = Depends(get_db), ctx: OrgContext = Depends(require_org_permission("ai:read"))):
    active = {c.provider for c in db.query(AiCredential).filter(
        AiCredential.organization_id == ctx.org.id, AiCredential.status == "active")}
    return [{"provider": p, "has_active_credential": p in active} for p in PROVIDERS]


# ----------------------------------------------------------------------------- credentials
class CredentialCreate(BaseModel):
    provider: str
    api_key: str = Field(min_length=8, max_length=512, repr=False)
    label: Optional[str] = Field(default=None, max_length=80)
    verify: bool = True


class CredentialRotate(BaseModel):
    api_key: str = Field(min_length=8, max_length=512, repr=False)
    verify: bool = True


def _own_credential(db: Session, ctx: OrgContext, credential_id: str) -> AiCredential:
    row = db.query(AiCredential).filter(AiCredential.organization_id == ctx.org.id, AiCredential.id == credential_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Credential not found")
    return row


@router.get("/credentials")
def list_credentials(db: Session = Depends(get_db), ctx: OrgContext = Depends(require_org_permission("ai:manage"))):
    rows = db.query(AiCredential).filter(AiCredential.organization_id == ctx.org.id).order_by(AiCredential.created_at).all()
    return [cred.public_view(c) for c in rows]


@router.post("/credentials", status_code=status.HTTP_201_CREATED)
async def create_credential(body: CredentialCreate, request: Request, db: Session = Depends(get_db),
                            ctx: OrgContext = Depends(require_org_permission("ai:manage"))):
    _provider(body.provider)
    try:
        credential = await cred.create(db, ctx.org.id, ctx.user.id, body.provider, body.api_key, body.label, body.verify)
    except E.AIError as exc:
        db.rollback()
        raise _credential_http(exc)
    audit_event(db, action="ai.credential.created", organization_id=ctx.org.id, actor_user_id=ctx.user.id,
                target=credential.id, detail={"provider": body.provider, "verified": bool(body.verify)}, request=request)
    db.commit()
    return cred.public_view(credential)


def _credential_http(exc: E.AIError) -> HTTPException:
    """Credential problems are the CALLER's to fix: say so precisely (still no provider body, no key)."""
    if exc.kind == E.INVALID_REQUEST:
        return HTTPException(status_code=422, detail=exc.message or "Invalid request")
    if exc.kind in (E.AUTHENTICATION, E.PERMISSION):
        return HTTPException(status_code=422, detail="The provider rejected this API key.", headers={"X-AI-Error": exc.kind})
    return to_http(exc)


@router.post("/credentials/{credential_id}/test")
async def test_credential(credential_id: str, request: Request, db: Session = Depends(get_db),
                          ctx: OrgContext = Depends(require_org_permission("ai:manage"))):
    credential = _own_credential(db, ctx, credential_id)
    if credential.status != "active":
        raise HTTPException(status_code=409, detail="Credential is revoked")
    try:
        _, key = cred.get_plain_key(db, ctx.org.id, credential.provider)
        await cred.test_connection(credential, key)
        ok, error = True, None
    except E.AIError as exc:
        ok, error = False, exc.kind
    audit_event(db, action="ai.credential.tested", organization_id=ctx.org.id, actor_user_id=ctx.user.id,
                target=credential.id, detail={"ok": ok, "error": error}, request=request)
    db.commit()
    return {"ok": ok, "error": error, "credential": cred.public_view(credential)}


@router.post("/credentials/{credential_id}/rotate")
async def rotate_credential(credential_id: str, body: CredentialRotate, request: Request, db: Session = Depends(get_db),
                            ctx: OrgContext = Depends(require_org_permission("ai:manage"))):
    credential = _own_credential(db, ctx, credential_id)
    try:
        await cred.rotate(db, credential, body.api_key, body.verify)
    except E.AIError as exc:
        db.rollback()
        raise _credential_http(exc)
    audit_event(db, action="ai.credential.rotated", organization_id=ctx.org.id, actor_user_id=ctx.user.id,
                target=credential.id, detail={"provider": credential.provider}, request=request)
    db.commit()
    return cred.public_view(credential)


@router.delete("/credentials/{credential_id}")
def revoke_credential(credential_id: str, request: Request, db: Session = Depends(get_db),
                      ctx: OrgContext = Depends(require_org_permission("ai:manage"))):
    credential = _own_credential(db, ctx, credential_id)
    if credential.status == "active":
        cred.revoke(credential)
        audit_event(db, action="ai.credential.revoked", organization_id=ctx.org.id, actor_user_id=ctx.user.id,
                    target=credential.id, detail={"provider": credential.provider}, request=request)
        db.commit()
    return cred.public_view(credential)


# ----------------------------------------------------------------------------- models
class ModelIn(BaseModel):
    provider: str
    model_id: str = Field(min_length=1, max_length=200)
    display_name: Optional[str] = Field(default=None, max_length=120)
    enabled: bool = True
    supports_tools: bool = False
    supports_vision: bool = False
    supports_json: bool = False
    context_window: Optional[int] = Field(default=None, gt=0)
    max_output_tokens: Optional[int] = Field(default=None, gt=0)
    input_price_usd_per_mtok: Optional[Decimal] = Field(default=None, ge=0)
    output_price_usd_per_mtok: Optional[Decimal] = Field(default=None, ge=0)


class ModelPatch(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=120)
    enabled: Optional[bool] = None
    supports_tools: Optional[bool] = None
    supports_vision: Optional[bool] = None
    supports_json: Optional[bool] = None
    context_window: Optional[int] = Field(default=None, gt=0)
    max_output_tokens: Optional[int] = Field(default=None, gt=0)
    input_price_usd_per_mtok: Optional[Decimal] = Field(default=None, ge=0)
    output_price_usd_per_mtok: Optional[Decimal] = Field(default=None, ge=0)


def _model_view(m: AiModel) -> dict:
    return {
        "id": m.id, "provider": m.provider, "model_id": m.model_id, "display_name": m.display_name, "enabled": m.enabled,
        "supports_tools": m.supports_tools, "supports_vision": m.supports_vision, "supports_json": m.supports_json,
        "context_window": m.context_window, "max_output_tokens": m.max_output_tokens,
        "input_price_usd_per_mtok": micros_to_usd(m.input_price_micros_per_mtok),
        "output_price_usd_per_mtok": micros_to_usd(m.output_price_micros_per_mtok),
    }


def _own_model(db: Session, ctx: OrgContext, model_id: str) -> AiModel:
    row = db.query(AiModel).filter(AiModel.organization_id == ctx.org.id, AiModel.id == model_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Model not found")
    return row


@router.get("/models")
def list_models(db: Session = Depends(get_db), ctx: OrgContext = Depends(require_org_permission("ai:read"))):
    rows = db.query(AiModel).filter(AiModel.organization_id == ctx.org.id).order_by(AiModel.provider, AiModel.model_id).all()
    return [_model_view(m) for m in rows]


@router.get("/models/discover")
async def discover_models(provider: str, db: Session = Depends(get_db),
                          ctx: OrgContext = Depends(require_org_permission("ai:manage"))):
    """Names the PROVIDER offers to this organization's key (no pricing: enter it yourself)."""
    _provider(provider)
    try:
        _, key = cred.get_plain_key(db, ctx.org.id, provider)
        found = await PROVIDER_ADAPTERS[provider].list_models(key, 30.0)
    except E.AIError as exc:
        raise to_http(exc)
    have = {m.model_id for m in db.query(AiModel).filter(AiModel.organization_id == ctx.org.id, AiModel.provider == provider)}
    return [{**item, "already_added": item["model_id"] in have} for item in found]


@router.post("/models", status_code=status.HTTP_201_CREATED)
def add_model(body: ModelIn, request: Request, db: Session = Depends(get_db),
              ctx: OrgContext = Depends(require_org_permission("ai:manage"))):
    _provider(body.provider)
    model = AiModel(
        organization_id=ctx.org.id, provider=body.provider, model_id=body.model_id.strip(), display_name=body.display_name,
        enabled=body.enabled, supports_tools=body.supports_tools, supports_vision=body.supports_vision,
        supports_json=body.supports_json, context_window=body.context_window, max_output_tokens=body.max_output_tokens,
        input_price_micros_per_mtok=None if body.input_price_usd_per_mtok is None else usd_to_micros(body.input_price_usd_per_mtok),
        output_price_micros_per_mtok=None if body.output_price_usd_per_mtok is None else usd_to_micros(body.output_price_usd_per_mtok),
    )
    db.add(model)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Model already registered for this provider")
    audit_event(db, action="ai.model.added", organization_id=ctx.org.id, actor_user_id=ctx.user.id, target=model.id,
                detail={"provider": model.provider}, request=request)
    db.commit()
    return _model_view(model)


@router.patch("/models/{model_id}")
def update_model(model_id: str, body: ModelPatch, request: Request, db: Session = Depends(get_db),
                 ctx: OrgContext = Depends(require_org_permission("ai:manage"))):
    model = _own_model(db, ctx, model_id)
    data = body.model_dump(exclude_unset=True)
    for key in ("display_name", "enabled", "supports_tools", "supports_vision", "supports_json", "context_window", "max_output_tokens"):
        if key in data:
            setattr(model, key, data[key])
    for key, column in (("input_price_usd_per_mtok", "input_price_micros_per_mtok"), ("output_price_usd_per_mtok", "output_price_micros_per_mtok")):
        if key in data:
            setattr(model, column, None if data[key] is None else usd_to_micros(data[key]))
    audit_event(db, action="ai.model.updated", organization_id=ctx.org.id, actor_user_id=ctx.user.id, target=model.id,
                detail={"fields": sorted(data)}, request=request)
    db.commit()
    return _model_view(model)


@router.delete("/models/{model_id}")
def remove_model(model_id: str, request: Request, db: Session = Depends(get_db),
                 ctx: OrgContext = Depends(require_org_permission("ai:manage"))):
    model = _own_model(db, ctx, model_id)
    # also drop it from fallback lists (the primary FK is set NULL by the database)
    for route in db.query(AiRouting).filter(AiRouting.organization_id == ctx.org.id).all():
        if model.id in (route.fallback_model_ids or []):
            route.fallback_model_ids = [i for i in route.fallback_model_ids if i != model.id]
    db.delete(model)
    audit_event(db, action="ai.model.removed", organization_id=ctx.org.id, actor_user_id=ctx.user.id, target=model_id, request=request)
    db.commit()
    return {"success": True}


# ----------------------------------------------------------------------------- routing
class RoutingIn(BaseModel):
    primary_model_id: str
    fallback_model_ids: List[str] = Field(default_factory=list, max_length=5)


def _routing_view(r: AiRouting) -> dict:
    return {"operation": r.operation, "primary_model_id": r.primary_model_id, "fallback_model_ids": list(r.fallback_model_ids or [])}


@router.get("/routing")
def list_routing(db: Session = Depends(get_db), ctx: OrgContext = Depends(require_org_permission("ai:read"))):
    rows = db.query(AiRouting).filter(AiRouting.organization_id == ctx.org.id).order_by(AiRouting.operation).all()
    return [_routing_view(r) for r in rows]


@router.put("/routing/{operation}")
def set_routing(operation: str, body: RoutingIn, request: Request, db: Session = Depends(get_db),
                ctx: OrgContext = Depends(require_org_permission("ai:manage"))):
    """``operation`` is ``default`` or a feature's operation name. Fallbacks are tried in order, ONLY
    after recoverable provider errors (rate limit, timeout, unavailable)."""
    if not _OPERATION_RE.match(operation):
        raise HTTPException(status_code=422, detail="Invalid operation name")
    ids = [body.primary_model_id, *body.fallback_model_ids]
    if len(set(ids)) != len(ids):
        raise HTTPException(status_code=422, detail="A model can appear only once in a route")
    found = {m.id for m in db.query(AiModel.id).filter(AiModel.organization_id == ctx.org.id, AiModel.id.in_(ids))}
    if found != set(ids):
        raise HTTPException(status_code=404, detail="Model not found")  # unknown OR another organization's: same answer
    route = db.query(AiRouting).filter(AiRouting.organization_id == ctx.org.id, AiRouting.operation == operation).first()
    if route is None:
        route = AiRouting(organization_id=ctx.org.id, operation=operation)
        db.add(route)
    route.primary_model_id = body.primary_model_id
    route.fallback_model_ids = list(body.fallback_model_ids)
    audit_event(db, action="ai.routing.updated", organization_id=ctx.org.id, actor_user_id=ctx.user.id, target=operation, request=request)
    db.commit()
    return _routing_view(route)


@router.delete("/routing/{operation}")
def clear_routing(operation: str, request: Request, db: Session = Depends(get_db),
                  ctx: OrgContext = Depends(require_org_permission("ai:manage"))):
    route = db.query(AiRouting).filter(AiRouting.organization_id == ctx.org.id, AiRouting.operation == operation).first()
    if route is None:
        raise HTTPException(status_code=404, detail="Route not found")
    db.delete(route)
    audit_event(db, action="ai.routing.cleared", organization_id=ctx.org.id, actor_user_id=ctx.user.id, target=operation, request=request)
    db.commit()
    return {"success": True}


# ----------------------------------------------------------------------------- budgets
class BudgetIn(BaseModel):
    limit_usd: Decimal = Field(ge=0)
    enabled: bool = True


def _budget_view(b: AiBudget) -> dict:
    current = window_start(b.period) == b.window_start
    spent = b.spent_micros if current else 0
    reserved = b.reserved_micros if current else 0
    return {
        "period": b.period, "enabled": b.enabled, "limit_usd": micros_to_usd(b.limit_micros),
        "spent_usd": micros_to_usd(spent), "reserved_usd": micros_to_usd(reserved),
        "remaining_usd": micros_to_usd(max(b.limit_micros - spent - reserved, 0)),
        "window_start": window_start(b.period),
    }


@router.get("/budgets")
def list_budgets(db: Session = Depends(get_db), ctx: OrgContext = Depends(require_org_permission("ai:read"))):
    rows = db.query(AiBudget).filter(AiBudget.organization_id == ctx.org.id).order_by(AiBudget.period).all()
    return [_budget_view(b) for b in rows]


@router.put("/budgets/{period}")
def set_budget(period: str, body: BudgetIn, request: Request, db: Session = Depends(get_db),
               ctx: OrgContext = Depends(require_org_permission("ai:manage"))):
    if period not in ("daily", "monthly"):
        raise HTTPException(status_code=422, detail="Period must be daily or monthly")
    row = db.query(AiBudget).filter(AiBudget.organization_id == ctx.org.id, AiBudget.period == period).first()
    if row is None:
        row = AiBudget(organization_id=ctx.org.id, period=period, limit_micros=0, window_start=window_start(period))
        db.add(row)
    row.limit_micros = usd_to_micros(body.limit_usd)
    row.enabled = body.enabled
    audit_event(db, action="ai.budget.updated", organization_id=ctx.org.id, actor_user_id=ctx.user.id, target=period,
                detail={"enabled": body.enabled}, request=request)
    db.commit()
    return _budget_view(row)


@router.delete("/budgets/{period}")
def remove_budget(period: str, request: Request, db: Session = Depends(get_db),
                  ctx: OrgContext = Depends(require_org_permission("ai:manage"))):
    row = db.query(AiBudget).filter(AiBudget.organization_id == ctx.org.id, AiBudget.period == period).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Budget not found")
    db.delete(row)
    audit_event(db, action="ai.budget.removed", organization_id=ctx.org.id, actor_user_id=ctx.user.id, target=period, request=request)
    db.commit()
    return {"success": True}


# ----------------------------------------------------------------------------- usage
@router.get("/usage/summary")
def usage_summary(days: int = Query(30, ge=1, le=366), db: Session = Depends(get_db),
                  ctx: OrgContext = Depends(require_org_permission("ai:read"))):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    base = db.query(AiUsageLog).filter(AiUsageLog.organization_id == ctx.org.id, AiUsageLog.created_at >= since)

    def totals(query):
        row = query.with_entities(
            func.count(AiUsageLog.id), func.coalesce(func.sum(AiUsageLog.input_tokens), 0),
            func.coalesce(func.sum(AiUsageLog.output_tokens), 0), func.coalesce(func.sum(AiUsageLog.cost_micros), 0),
            func.coalesce(func.sum(AiUsageLog.estimated_cost_micros), 0)).one()
        return {"requests": row[0], "input_tokens": int(row[1]), "output_tokens": int(row[2]),
                "cost_usd": micros_to_usd(int(row[3])), "estimated_cost_usd": micros_to_usd(int(row[4]))}

    def grouped(*columns):
        rows = base.with_entities(
            *columns, func.count(AiUsageLog.id), func.coalesce(func.sum(AiUsageLog.input_tokens), 0),
            func.coalesce(func.sum(AiUsageLog.output_tokens), 0), func.coalesce(func.sum(AiUsageLog.cost_micros), 0)
        ).group_by(*columns).order_by(func.sum(AiUsageLog.cost_micros).desc().nullslast()).all()
        keys = [c.key for c in columns]
        return [{**dict(zip(keys, r[:len(keys)])), "requests": r[len(keys)], "input_tokens": int(r[len(keys) + 1]),
                 "output_tokens": int(r[len(keys) + 2]), "cost_usd": micros_to_usd(int(r[len(keys) + 3]))} for r in rows]

    errors = base.filter(AiUsageLog.status == "error").count()
    return {
        "days": days, "totals": {**totals(base), "errors": errors},
        "by_model": grouped(AiUsageLog.provider, AiUsageLog.model_id),
        "by_operation": grouped(AiUsageLog.operation),
        "by_user": grouped(AiUsageLog.user_id),
    }


@router.get("/usage/logs")
def usage_logs(limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db),
               ctx: OrgContext = Depends(require_org_permission("ai:read"))):
    rows = (db.query(AiUsageLog).filter(AiUsageLog.organization_id == ctx.org.id)
            .order_by(AiUsageLog.created_at.desc()).limit(limit).all())
    return [{
        "id": r.id, "created_at": r.created_at, "request_id": r.request_id, "attempt": r.attempt, "operation": r.operation,
        "provider": r.provider, "model_id": r.model_id, "user_id": r.user_id, "status": r.status, "error_kind": r.error_kind,
        "input_tokens": r.input_tokens, "output_tokens": r.output_tokens, "cached_input_tokens": r.cached_input_tokens,
        "reasoning_tokens": r.reasoning_tokens, "estimated_cost_usd": micros_to_usd(r.estimated_cost_micros),
        "cost_usd": micros_to_usd(r.cost_micros), "latency_ms": r.latency_ms,
    } for r in rows]
