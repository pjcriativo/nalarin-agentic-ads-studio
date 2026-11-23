"""AI agents API (etapa 02.3). Agents READ and plan only: no endpoint here (and no tool behind it) can publish a
campaign or spend money.

RBAC: ``agents:read`` (analyst+) lists agents/tasks/results; ``agents:run`` (manager+) starts and cancels tasks;
``agents:manage`` (admin+) enables/disables agents and tunes their capped limits. Every query is filtered by the
organization of the request on top of PostgreSQL RLS.

A task runs after the response (FastAPI background task) through the AI Gateway of the organization: its own
credentials, routing, fallbacks and budgets. Progress is polled with ``GET /tasks/{id}``.
"""
import json
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agents import executor
from app.agents.future_tools import FUTURE_TOOL_CONTRACTS
from app.agents.registry import AGENTS, HARD_MAX_STEPS, HARD_MAX_TIMEOUT_SECONDS, delegatable_agents
from app.agents.security import redact_secrets, safe_json
from app.agents.supervisor import effective_limits
from app.agents.tools import TOOLS
from app.ai.pricing import micros_to_usd
from app.core.audit import audit_event
from app.core.config import settings
from app.core.tenancy import OrgContext, require_org_permission
from app.database import get_db
from app.models import AgentRun, AgentSetting, AgentStep, AgentTask, AiModel, AiRouting

router = APIRouter()
_KEY_RE = re.compile(r"^[A-Za-z0-9._:\-]{1,64}$")
_FINAL = ("completed", "failed", "cancelled")


# ------------------------------------------------------------------------------------ agents & tools
def _route_view(db: Session, org_id: str, operation: str) -> Dict[str, Any]:
    route = db.query(AiRouting).filter(AiRouting.organization_id == org_id, AiRouting.operation == operation).first()
    inherited = False
    if route is None:
        route = db.query(AiRouting).filter(AiRouting.organization_id == org_id, AiRouting.operation == "default").first()
        inherited = route is not None
    model = db.query(AiModel).filter(AiModel.organization_id == org_id, AiModel.id == route.primary_model_id).first() if route and route.primary_model_id else None
    return {"operation": operation, "has_route": route is not None, "inherited_from_default": inherited,
            "primary_model": {"provider": model.provider, "model_id": model.model_id} if model else None,
            "fallbacks": len(route.fallback_model_ids or []) if route else 0}


@router.get("")
def list_agents(db: Session = Depends(get_db), ctx: OrgContext = Depends(require_org_permission("agents:read"))):
    settings_by_key = executor.organization_settings(db, ctx.org.id)
    out = []
    for key, defn in delegatable_agents().items():
        cfg = settings_by_key.get(key)
        limits = effective_limits(defn, getattr(cfg, "max_steps", None), getattr(cfg, "timeout_seconds", None))
        out.append({
            "key": key, "name": defn.name, "description": defn.description, "version": defn.version,
            "instructions_hash": defn.instructions_hash, "enabled": cfg.enabled if cfg else True,
            "allowed_tools": list(defn.allowed_tools), "readiness": defn.readiness, "limitations": list(defn.limitations),
            "limits": {"defaults": defn.limits.__dict__, "effective": limits.__dict__},
            "routing": _route_view(db, ctx.org.id, defn.operation),
            "output_schema": defn.output_model.model_json_schema(),
        })
    return out


@router.get("/tools")
def list_tools(ctx: OrgContext = Depends(require_org_permission("agents:read"))):
    """Tools that can run today, and the Google Ads tool CONTRACTS (planned: they cannot run and return no data)."""
    available = [{"name": t.name, "description": t.description, "side_effect": t.side_effect, "required_permission": t.required_permission,
                  "status": "available", "arguments_schema": t.spec_parameters()} for t in TOOLS.values()]
    return {"available": available, "planned": [c.describe() for c in FUTURE_TOOL_CONTRACTS.values()]}


class AgentSettingsIn(BaseModel):
    enabled: bool = True
    max_steps: Optional[int] = Field(default=None, ge=1, le=HARD_MAX_STEPS)
    timeout_seconds: Optional[int] = Field(default=None, ge=10, le=HARD_MAX_TIMEOUT_SECONDS)


@router.put("/{agent_key}/settings")
def update_agent_settings(agent_key: str, body: AgentSettingsIn, request: Request, db: Session = Depends(get_db),
                          ctx: OrgContext = Depends(require_org_permission("agents:manage"))):
    if agent_key not in delegatable_agents():
        raise HTTPException(status_code=404, detail="Agent not found")
    row = db.query(AgentSetting).filter(AgentSetting.organization_id == ctx.org.id, AgentSetting.agent_key == agent_key).first()
    if row is None:
        row = AgentSetting(organization_id=ctx.org.id, agent_key=agent_key)
        db.add(row)
    row.enabled, row.max_steps, row.timeout_seconds, row.updated_by_user_id = body.enabled, body.max_steps, body.timeout_seconds, ctx.user.id
    audit_event(db, action="agent.settings.updated", organization_id=ctx.org.id, actor_user_id=ctx.user.id, target=agent_key,
                detail={"enabled": body.enabled, "max_steps": body.max_steps, "timeout_seconds": body.timeout_seconds}, request=request)
    db.commit()
    return {"agent_key": agent_key, "enabled": row.enabled, "max_steps": row.max_steps, "timeout_seconds": row.timeout_seconds}


# ------------------------------------------------------------------------------------ tasks
class TaskIn(BaseModel):
    objective: str = Field(min_length=5)
    agent_key: Optional[str] = Field(default=None, max_length=40)
    context: Optional[Dict[str, Any]] = None
    idempotency_key: Optional[str] = None
    max_cost_usd: Optional[float] = Field(default=None, gt=0)


def _task_view(task: AgentTask) -> Dict[str, Any]:
    plan_steps = (task.plan or {}).get("steps") or []
    return {
        "id": task.id, "status": task.status, "agent_key": task.agent_key, "kind": "single" if task.agent_key else "supervised",
        "objective": task.objective, "created_by_user_id": task.created_by_user_id, "created_at": task.created_at,
        "started_at": task.started_at, "finished_at": task.finished_at, "attempts": task.attempts, "max_attempts": task.max_attempts,
        "cancel_requested": task.cancel_requested, "step_count": task.step_count,
        "usage": {"input_tokens": task.total_input_tokens, "output_tokens": task.total_output_tokens,
                  "cost_usd": micros_to_usd(task.total_cost_micros), "max_cost_usd": micros_to_usd(task.max_cost_micros)},
        "error": {"kind": task.error_kind, "message": task.error_message} if task.error_kind else None,
        "plan": {"summary": (task.plan or {}).get("summary"), "steps": [{"id": s["id"], "agent": s["agent"], "objective": s["objective"],
                                                                         "depends_on": s.get("depends_on", [])} for s in plan_steps]} if task.plan else None,
    }


def _run_view(run: AgentRun) -> Dict[str, Any]:
    return {"id": run.id, "sequence": run.sequence, "plan_step_id": run.plan_step_id, "agent_key": run.agent_key,
            "agent_version": run.agent_version, "instructions_hash": run.instructions_hash, "status": run.status,
            "step_count": run.step_count, "cost_usd": micros_to_usd(run.total_cost_micros),
            "error": {"kind": run.error_kind, "message": run.error_message} if run.error_kind else None,
            "started_at": run.started_at, "finished_at": run.finished_at}


def _own_task(db: Session, ctx: OrgContext, task_id: str) -> AgentTask:
    task = db.query(AgentTask).filter(AgentTask.organization_id == ctx.org.id, AgentTask.id == task_id).first()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.post("/tasks", status_code=status.HTTP_201_CREATED)
async def start_task(body: TaskIn, request: Request, background: BackgroundTasks, db: Session = Depends(get_db),
                     ctx: OrgContext = Depends(require_org_permission("agents:run"))):
    objective = body.objective.strip()
    if len(objective) < 5 or len(objective) > settings.AGENT_MAX_OBJECTIVE_CHARS:
        raise HTTPException(status_code=422, detail=f"The objective must have 5 to {settings.AGENT_MAX_OBJECTIVE_CHARS} characters")
    if body.idempotency_key is not None and not _KEY_RE.match(body.idempotency_key):
        raise HTTPException(status_code=422, detail="Invalid idempotency key")
    context = None
    if body.context:
        if len(json.dumps(body.context, default=str)) > executor.MAX_CONTEXT_CHARS:
            raise HTTPException(status_code=422, detail="The context is too large")
        context = safe_json(body.context, executor.MAX_CONTEXT_CHARS)  # secrets pasted by mistake never reach storage or a model
    try:
        task, created = executor.create_task(
            db, org_id=ctx.org.id, user_id=ctx.user.id, objective=redact_secrets(objective), agent_key=body.agent_key,
            context=context, idempotency_key=body.idempotency_key, max_cost_usd=body.max_cost_usd)
    except executor.UnknownAgent:
        raise HTTPException(status_code=404, detail="Agent not found")
    except executor.AgentDisabledError:
        raise HTTPException(status_code=409, detail="This agent is disabled for the organization")
    except executor.ActiveTaskLimit:
        raise HTTPException(status_code=429, detail="Too many tasks are running for this organization; wait for one to finish")
    if created:
        background.add_task(executor.execute_task, task.id, ctx.org.id)
    return _task_view(task)


@router.get("/tasks")
def list_tasks(status_filter: Optional[str] = Query(None, alias="status"), limit: int = Query(25, ge=1, le=100),
               db: Session = Depends(get_db), ctx: OrgContext = Depends(require_org_permission("agents:read"))):
    if status_filter is not None and status_filter not in ("pending", "running", "completed", "failed", "cancelled"):
        raise HTTPException(status_code=422, detail="Unknown status")
    query = db.query(AgentTask).filter(AgentTask.organization_id == ctx.org.id)
    if status_filter:
        query = query.filter(AgentTask.status == status_filter)
    return [_task_view(t) for t in query.order_by(AgentTask.created_at.desc()).limit(limit).all()]


@router.get("/tasks/{task_id}")
def task_progress(task_id: str, db: Session = Depends(get_db), ctx: OrgContext = Depends(require_org_permission("agents:read"))):
    task = _own_task(db, ctx, task_id)
    runs = db.query(AgentRun).filter(AgentRun.organization_id == ctx.org.id, AgentRun.task_id == task.id).order_by(AgentRun.sequence).all()
    view = _task_view(task)
    total = len((task.plan or {}).get("steps") or []) or (1 if task.agent_key else None)
    completed = len({r.plan_step_id or r.id for r in runs if r.status == "completed"})
    view["runs"] = [_run_view(r) for r in runs]
    view["progress"] = {"completed_steps": completed, "total_steps": total, "final": task.status in _FINAL}
    return view


@router.get("/tasks/{task_id}/steps")
def task_steps(task_id: str, after: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db),
               ctx: OrgContext = Depends(require_org_permission("agents:read"))):
    task = _own_task(db, ctx, task_id)
    rows = (db.query(AgentStep).filter(AgentStep.organization_id == ctx.org.id, AgentStep.task_id == task.id, AgentStep.sequence > after)
            .order_by(AgentStep.sequence).limit(limit).all())
    return [{"sequence": s.sequence, "run_id": s.run_id, "kind": s.kind, "name": s.name, "status": s.status, "error_kind": s.error_kind,
             "input": s.input, "output": s.output, "injection_suspected": s.injection_suspected, "provider": s.provider,
             "model_id": s.model_id, "input_tokens": s.input_tokens, "output_tokens": s.output_tokens,
             "cost_usd": micros_to_usd(s.cost_micros), "latency_ms": s.latency_ms, "created_at": s.created_at} for s in rows]


@router.get("/tasks/{task_id}/result")
def task_result(task_id: str, db: Session = Depends(get_db), ctx: OrgContext = Depends(require_org_permission("agents:read"))):
    task = _own_task(db, ctx, task_id)
    if task.status not in _FINAL:
        raise HTTPException(status_code=409, detail="The task has not finished yet")
    return {"id": task.id, "status": task.status, "result": task.result,
            "error": {"kind": task.error_kind, "message": task.error_message} if task.error_kind else None,
            "disclaimer": "Results are model-generated proposals for human review. Nothing was published or spent."}


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str, request: Request, db: Session = Depends(get_db),
                ctx: OrgContext = Depends(require_org_permission("agents:run"))):
    task = _own_task(db, ctx, task_id)
    if task.created_by_user_id != ctx.user.id and not ctx.can("agents:manage"):
        raise HTTPException(status_code=403, detail="Only the person who started the task or an administrator can cancel it")
    result = executor.request_cancel(db, ctx.org.id, task.id)
    if result is None:
        raise HTTPException(status_code=409, detail="The task already finished")
    audit_event(db, action="agent.task.cancel_requested", organization_id=ctx.org.id, actor_user_id=ctx.user.id, target=task.id,
                detail={"status": result}, request=request)
    db.commit()
    return {"id": task.id, "status": result, "cancel_requested": True}


@router.post("/recover")
async def recover_my_tasks(db: Session = Depends(get_db), ctx: OrgContext = Depends(require_org_permission("agents:manage"))):
    """Re-queue this organization's interrupted tasks (expired lease) and start every pending one. Other organizations are untouched."""
    outcome = executor.recover_interrupted_tasks(org_id=ctx.org.id)
    for task_id, org_id in outcome["pending"]:
        executor.schedule(task_id, org_id)
    return {"requeued_or_failed": [{"id": i, "status": s} for i, _o, s in outcome["recovered"]], "started": len(outcome["pending"])}
