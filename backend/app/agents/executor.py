"""Task lifecycle: create -> claim -> execute -> finish, plus cancellation and recovery of interrupted work.

State machine (``agent_tasks.status``)::

    pending --claim--> running --> completed | failed | cancelled
       ^                  |
       +---- recovery ----+   (lease expired: the executor died; re-queued up to ``max_attempts`` times, else failed)

* CLAIM is one atomic ``UPDATE ... WHERE status = 'pending'``: two executors can never both run a task.
* The lease is renewed by every model turn and by a heartbeat coroutine; every write is fenced by ``lease_owner``
  (see store.py), so an executor that lost the task stops at its next write.
* CANCEL of a pending task is immediate; of a running task it sets a flag read before each model turn (a model call
  already in flight finishes, then the task stops and nothing further is started).
* RECOVERY (``recover_interrupted_tasks``) runs with the signed platform-admin context, which a single extra policy on
  ``agent_tasks`` honours; it only changes status/lease columns and never reads tenant content.
"""
import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.agents import errors as AE
from app.agents.errors import AgentError, LostLease
from app.agents.registry import AGENTS, delegatable_agents
from app.agents.security import redact_secrets, truncate
from app.agents.store import TaskStore, utcnow
from app.agents.supervisor import ExecContext, effective_limits, run_single, run_supervised
from app.core.audit import audit_event
from app.core.config import settings
from app.core.tenancy import ROLE_PERMISSIONS
from app.database import SessionLocal, bind_context
from app.models import AgentSetting, AgentTask, OrganizationMember

logger = logging.getLogger(__name__)

# Test seams: a callable (db, org_id, user_id) -> gateway, and an async page fetcher.
GATEWAY_FACTORY: Optional[Callable[..., Any]] = None
FETCH_PAGE: Optional[Callable[[str], Any]] = None
_ACTIVE: Set[str] = set()
MAX_CONTEXT_CHARS = 12000


class ActiveTaskLimit(Exception):
    pass


class UnknownAgent(Exception):
    pass


class AgentDisabledError(Exception):
    pass


def _session(org_id: Optional[str]) -> Session:
    db = SessionLocal()
    bind_context(db, org_id)
    return db


def _micros(usd: float) -> int:
    return int(round(usd * 1_000_000))


def organization_settings(db: Session, org_id: str) -> Dict[str, AgentSetting]:
    return {s.agent_key: s for s in db.query(AgentSetting).filter(AgentSetting.organization_id == org_id).all()}


def enabled_agent_keys(db: Session, org_id: str) -> frozenset:
    configured = organization_settings(db, org_id)
    return frozenset(k for k in delegatable_agents() if configured.get(k) is None or configured[k].enabled)


# ------------------------------------------------------------------------------------ creation
def create_task(db: Session, *, org_id: str, user_id: str, objective: str, agent_key: Optional[str], context: Optional[dict],
                idempotency_key: Optional[str], max_cost_usd: Optional[float]) -> Tuple[AgentTask, bool]:
    """Insert a PENDING task. Returns (task, created); an existing task with the same idempotency key is returned as is.
    Serialized per organization so the active-task cap cannot be exceeded by concurrent requests."""
    if agent_key is not None:
        if agent_key not in delegatable_agents():
            raise UnknownAgent(agent_key)
        if agent_key not in enabled_agent_keys(db, org_id):
            raise AgentDisabledError(agent_key)
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:org))"), {"org": f"agent-tasks:{org_id}"})
    if idempotency_key:
        existing = db.query(AgentTask).filter(AgentTask.organization_id == org_id, AgentTask.idempotency_key == idempotency_key).first()
        if existing is not None:
            db.rollback()
            return existing, False
    active = db.query(AgentTask).filter(AgentTask.organization_id == org_id, AgentTask.status.in_(("pending", "running"))).count()
    if active >= settings.AGENT_MAX_ACTIVE_TASKS_PER_ORG:
        db.rollback()
        raise ActiveTaskLimit()
    cost_cap = max_cost_usd if max_cost_usd is not None else settings.AGENT_DEFAULT_MAX_COST_USD
    task = AgentTask(
        organization_id=org_id, created_by_user_id=user_id, agent_key=agent_key, objective=objective.strip(),
        input_context=context or None, status="pending", max_attempts=settings.AGENT_MAX_ATTEMPTS, idempotency_key=idempotency_key,
        max_cost_micros=min(_micros(cost_cap), _micros(settings.AGENT_DEFAULT_MAX_COST_USD * 10)),
        limits={"task_timeout_seconds": settings.AGENT_TASK_TIMEOUT_SECONDS})
    db.add(task)
    audit_event(db, action="agent.task.created", organization_id=org_id, actor_user_id=user_id, target=task.id,
                detail={"agent": agent_key or "supervisor"})
    db.commit()
    db.refresh(task)
    return task, True


def request_cancel(db: Session, org_id: str, task_id: str) -> Optional[str]:
    """Cancel a pending task now, or flag a running one. Returns the resulting status (None when not cancellable)."""
    row = db.execute(text(
        "UPDATE agent_tasks SET cancel_requested = true, "
        "status = CASE WHEN status = 'pending' THEN 'cancelled' ELSE status END, "
        "finished_at = CASE WHEN status = 'pending' THEN now() ELSE finished_at END, "
        "error_kind = CASE WHEN status = 'pending' THEN 'cancelled' ELSE error_kind END "
        "WHERE id = :tid AND organization_id = :org AND status IN ('pending', 'running') RETURNING status"),
        {"tid": task_id, "org": org_id}).first()
    db.commit()
    return row[0] if row else None


# ------------------------------------------------------------------------------------ execution
def _claim(db: Session, task_id: str, org_id: str, owner: str, now: datetime) -> bool:
    row = db.execute(text(
        "UPDATE agent_tasks SET status = 'running', lease_owner = :owner, lease_expires_at = :exp, heartbeat_at = :now, "
        "started_at = coalesce(started_at, :now), attempts = attempts + 1, error_kind = NULL, error_message = NULL "
        "WHERE id = :tid AND organization_id = :org AND status = 'pending' AND NOT cancel_requested RETURNING id"),
        {"tid": task_id, "org": org_id, "owner": owner, "now": now, "exp": now + timedelta(seconds=settings.AGENT_LEASE_SECONDS)}).first()
    if row is None:  # a pending task that was flagged for cancellation never starts
        db.execute(text(
            "UPDATE agent_tasks SET status = 'cancelled', finished_at = now(), error_kind = 'cancelled' "
            "WHERE id = :tid AND organization_id = :org AND status = 'pending' AND cancel_requested"), {"tid": task_id, "org": org_id})
    db.commit()
    return row is not None


def _permissions_of(db: Session, org_id: str, user_id: Optional[str]) -> frozenset:
    if not user_id:
        return frozenset()
    member = db.query(OrganizationMember).filter(
        OrganizationMember.organization_id == org_id, OrganizationMember.user_id == user_id,
        OrganizationMember.status == "active").first()
    return ROLE_PERMISSIONS.get(member.role, frozenset()) if member else frozenset()


async def _heartbeat(org_id: str, task_id: str, owner: str) -> None:
    """Renews the lease while a long model call blocks the loop (separate session: never shares a transaction)."""
    while True:
        await asyncio.sleep(settings.AGENT_HEARTBEAT_SECONDS)
        db = _session(org_id)
        try:
            now = utcnow()
            db.execute(text("UPDATE agent_tasks SET heartbeat_at = :now, lease_expires_at = :exp WHERE id = :tid "
                            "AND organization_id = :org AND lease_owner = :owner AND status = 'running'"),
                       {"now": now, "exp": now + timedelta(seconds=settings.AGENT_LEASE_SECONDS), "tid": task_id, "org": org_id, "owner": owner})
            db.commit()
        except Exception:  # a failed heartbeat must never kill the task; the lease just ages
            db.rollback()
        finally:
            db.close()


def _default_gateway(db: Session, org_id: str, user_id: Optional[str]):
    from app.ai.gateway import AIGateway
    return AIGateway(db, org_id, user_id)


async def _default_fetch(url: str) -> bytes:
    from app.agents.tools import MAX_PAGE_BYTES
    from app.core import storage
    return await storage.fetch_public_bytes(url, MAX_PAGE_BYTES, timeout=15.0)


async def execute_task(task_id: str, org_id: str) -> str:
    """Run one task to a final state. Returns the final status, or 'not_claimed' / 'lost_lease'."""
    owner = str(uuid.uuid4())
    db = _session(org_id)
    heartbeat: Optional[asyncio.Task] = None
    try:
        now = utcnow()
        if not _claim(db, task_id, org_id, owner, now):
            return "not_claimed"
        task = db.query(AgentTask).filter(AgentTask.id == task_id, AgentTask.organization_id == org_id).one()
        store = TaskStore(db, org_id, task_id, owner)
        # runs left open by the previous (dead) executor are closed as interrupted; completed ones are kept
        db.execute(text("UPDATE agent_runs SET status = 'failed', error_kind = 'interrupted', finished_at = now() "
                        "WHERE task_id = :tid AND organization_id = :org AND status IN ('pending', 'running')"), {"tid": task_id, "org": org_id})
        timeout = int((task.limits or {}).get("task_timeout_seconds") or settings.AGENT_TASK_TIMEOUT_SECONDS)
        db.execute(text("UPDATE agent_tasks SET deadline_at = :d WHERE id = :tid AND organization_id = :org"),
                   {"d": now + timedelta(seconds=timeout), "tid": task_id, "org": org_id})
        db.commit()
        db.refresh(task)

        def emit(action: str, detail: Dict[str, Any]) -> None:
            audit_event(db, action=action, organization_id=org_id, actor_user_id=task.created_by_user_id, target=task_id, detail=detail)
            db.commit()
        emit("agent.task.started", {"attempt": task.attempts, "agent": task.agent_key or "supervisor"})

        permissions = _permissions_of(db, org_id, task.created_by_user_id)
        try:
            if "agents:run" not in permissions:
                raise AgentError(AE.AUTHORIZATION, "the requesting user can no longer run agents in this organization")
            configured = organization_settings(db, org_id)
            ec = ExecContext(
                db=db, gateway=(GATEWAY_FACTORY or _default_gateway)(db, org_id, task.created_by_user_id), store=store, org_id=org_id,
                user_id=task.created_by_user_id, permissions=permissions, task_id=task_id, objective=task.objective,
                input_context=task.input_context or {}, agent_key=task.agent_key, deadline=task.deadline_at,
                max_cost_micros=task.max_cost_micros, enabled=enabled_agent_keys(db, org_id), existing_plan=task.plan,
                limits_for=lambda d: effective_limits(d, getattr(configured.get(d.key), "max_steps", None), getattr(configured.get(d.key), "timeout_seconds", None)),
                fetch_page=FETCH_PAGE or _default_fetch, on_event=emit)
            heartbeat = asyncio.get_running_loop().create_task(_heartbeat(org_id, task_id, owner))
            result = await (run_single(ec) if task.agent_key else run_supervised(ec))
            store.finish_task(status="completed", result=result)
            emit("agent.task.completed", {"steps": task.step_count})
            return "completed"
        except AgentError as exc:
            status = "cancelled" if exc.kind == AE.CANCELLED else "failed"
            partial = {"partial": ec.partial} if "ec" in locals() and ec.partial else None
            store.finish_task(status=status, result=partial, error_kind=exc.kind, error_message=exc.message)
            emit(f"agent.task.{status}", {"error": exc.kind})
            return status
    except LostLease:
        logger.warning("agent task %s: lease lost, another executor owns it now", task_id)
        return "lost_lease"
    except Exception:
        logger.exception("agent task %s crashed", task_id)
        try:
            db.rollback()
            db.execute(text("UPDATE agent_runs SET status = 'failed', error_kind = 'internal', finished_at = now() "
                            "WHERE task_id = :tid AND organization_id = :org AND status IN ('pending', 'running') AND EXISTS "
                            "(SELECT 1 FROM agent_tasks t WHERE t.id = :tid AND t.organization_id = :org AND t.lease_owner = :owner)"),
                       {"tid": task_id, "org": org_id, "owner": owner})
            db.commit()
            TaskStore(db, org_id, task_id, owner).finish_task(status="failed", error_kind=AE.INTERNAL, error_message="internal error")
        except Exception:
            db.rollback()
        return "failed"
    finally:
        if heartbeat is not None:
            heartbeat.cancel()
        db.close()


def schedule(task_id: str, org_id: str) -> None:
    """Run a task in the current event loop (deduplicated per process; the claim deduplicates across processes)."""
    if task_id in _ACTIVE:
        return
    _ACTIVE.add(task_id)
    task = asyncio.get_running_loop().create_task(execute_task(task_id, org_id))
    task.add_done_callback(lambda _t: _ACTIVE.discard(task_id))


# ------------------------------------------------------------------------------------ recovery
def recover_interrupted_tasks(now: Optional[datetime] = None, org_id: Optional[str] = None) -> Dict[str, List]:
    """Re-queue tasks whose executor died (lease expired) or fail them after ``max_attempts``; list tasks to schedule.

    ``org_id=None`` is the platform job (signed platform-admin context, every organization). With ``org_id`` the work
    runs inside that organization's own RLS context and can neither see nor touch another tenant's tasks.
    Idempotent and safe to run from several processes at once (each row is updated by exactly one of them)."""
    now = now or utcnow()
    db = SessionLocal()
    if org_id:
        bind_context(db, org_id)
    else:
        bind_context(db, None, platform_admin=True)
    scope = "AND organization_id = :scope" if org_id else ""
    try:
        rows = db.execute(text(
            "UPDATE agent_tasks SET "
            "status = CASE WHEN cancel_requested THEN 'cancelled' WHEN attempts >= max_attempts THEN 'failed' ELSE 'pending' END, "
            "error_kind = CASE WHEN cancel_requested THEN 'cancelled' WHEN attempts >= max_attempts THEN 'interrupted' ELSE error_kind END, "
            "error_message = CASE WHEN cancel_requested THEN 'cancelled while interrupted' "
            "WHEN attempts >= max_attempts THEN 'the task was interrupted too many times' ELSE error_message END, "
            "finished_at = CASE WHEN cancel_requested OR attempts >= max_attempts THEN :now ELSE finished_at END, "
            "lease_owner = NULL, lease_expires_at = NULL "
            f"WHERE status = 'running' AND lease_expires_at < :now {scope} "
            "RETURNING id, organization_id, status, attempts, created_by_user_id"), {"now": now, "scope": org_id}).all()
        pending = db.execute(text(f"SELECT id, organization_id FROM agent_tasks WHERE status = 'pending' AND NOT cancel_requested {scope} "
                                  "ORDER BY created_at"), {"scope": org_id}).all()
        db.commit()
    finally:
        db.close()
    for row in rows:  # audit inside each organization's own context
        org_db = _session(row.organization_id)
        try:
            audit_event(org_db, action="agent.task.recovered", organization_id=row.organization_id, actor_user_id=None,
                        target=row.id, detail={"result": row.status, "attempts": row.attempts})
            org_db.commit()
        except Exception:
            org_db.rollback()
        finally:
            org_db.close()
    return {"recovered": [(r.id, r.organization_id, r.status) for r in rows], "pending": [(p.id, p.organization_id) for p in pending]}


async def recover_and_resume() -> int:
    """Recovery + schedule every pending task in this process. Called at startup and by the recovery endpoint."""
    outcome = await asyncio.get_running_loop().run_in_executor(None, recover_interrupted_tasks)
    for task_id, org_id in outcome["pending"]:
        schedule(task_id, org_id)
    return len(outcome["pending"])
