"""Persistence of one executing task, with FENCING.

Every write goes through ``WHERE lease_owner = :owner AND status = 'running'``. When a recovery job re-queued the
task (lease expired) or someone cancelled/finished it, the old executor's next write matches no row and raises
``LostLease``: it stops, and two executors can never both advance one task. Each statement commits, so a crash
loses at most the step in flight; nothing already stored is rewritten on resume.
"""
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.agents.errors import LostLease
from app.agents.security import redact_secrets, safe_json, truncate
from app.core.config import settings
from app.models import AgentStep


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _json(value: Any) -> Optional[str]:
    return None if value is None else json.dumps(value, ensure_ascii=False, default=str)


class TaskStore:
    def __init__(self, db: Session, org_id: str, task_id: str, owner: str):
        self.db, self.org_id, self.task_id, self.owner = db, org_id, task_id, owner

    def _params(self, **extra) -> Dict[str, Any]:
        return {"tid": self.task_id, "org": self.org_id, "owner": self.owner, **extra}

    # ------------------------------------------------------------------ lease / cancellation
    def checkpoint(self, now: Optional[datetime] = None) -> bool:
        """Heartbeat + read the cancel flag in ONE statement. Raises LostLease when this executor no longer owns the task."""
        now = now or utcnow()
        row = self.db.execute(text(
            "UPDATE agent_tasks SET heartbeat_at = :now, lease_expires_at = :exp "
            "WHERE id = :tid AND organization_id = :org AND lease_owner = :owner AND status = 'running' "
            "RETURNING cancel_requested"), self._params(now=now, exp=now + timedelta(seconds=settings.AGENT_LEASE_SECONDS))).first()
        self.db.commit()
        if row is None:
            raise LostLease()
        return bool(row[0])

    # ------------------------------------------------------------------ steps
    def add_step(self, *, kind: str, run_id: Optional[str] = None, name: Optional[str] = None, status: str = "ok",
                 error_kind: Optional[str] = None, input: Any = None, output: Any = None, injection: bool = False,
                 request_id: Optional[str] = None, provider: Optional[str] = None, model_id: Optional[str] = None,
                 input_tokens: int = 0, output_tokens: int = 0, cost_micros: Optional[int] = None,
                 latency_ms: Optional[int] = None) -> Tuple[int, int]:
        """Append a step and update the task totals atomically. Returns (sequence, task total cost in micros)."""
        row = self.db.execute(text(
            "UPDATE agent_tasks SET step_count = step_count + 1, total_cost_micros = total_cost_micros + :cost, "
            "total_input_tokens = total_input_tokens + :tin, total_output_tokens = total_output_tokens + :tout "
            "WHERE id = :tid AND organization_id = :org AND lease_owner = :owner AND status = 'running' "
            "RETURNING step_count, total_cost_micros"),
            self._params(cost=int(cost_micros or 0), tin=int(input_tokens), tout=int(output_tokens))).first()
        if row is None:
            self.db.rollback()
            raise LostLease()
        self.db.add(AgentStep(
            organization_id=self.org_id, task_id=self.task_id, run_id=run_id, sequence=row[0], kind=kind, name=name,
            status=status, error_kind=error_kind, input=safe_json(input), output=safe_json(output),
            injection_suspected=injection, gateway_request_id=request_id, provider=provider, model_id=model_id,
            input_tokens=input_tokens, output_tokens=output_tokens, cost_micros=cost_micros, latency_ms=latency_ms))
        self.db.commit()
        return row[0], row[1]

    # ------------------------------------------------------------------ runs
    def create_run(self, *, agent_key: str, agent_version: str, instructions_hash: str, objective: str,
                   plan_step_id: Optional[str] = None) -> str:
        run_id = self.db.execute(text(
            "INSERT INTO agent_runs (id, organization_id, task_id, sequence, plan_step_id, agent_key, agent_version, "
            "instructions_hash, objective, status, started_at) "
            "SELECT gen_random_uuid()::text, :org, :tid, coalesce(max(sequence), 0) + 1, :psid, :ak, :av, :ih, :obj, 'running', now() "
            "FROM agent_runs WHERE task_id = :tid "
            "AND EXISTS (SELECT 1 FROM agent_tasks t WHERE t.id = :tid AND t.organization_id = :org AND t.lease_owner = :owner AND t.status = 'running') "
            "RETURNING id"),
            self._params(psid=plan_step_id, ak=agent_key, av=agent_version, ih=instructions_hash,
                         obj=truncate(redact_secrets(objective), 4000))).scalar()
        self.db.commit()
        if run_id is None:
            raise LostLease()
        return run_id

    def finish_run(self, run_id: str, *, status: str, output: Any = None, error_kind: Optional[str] = None,
                   error_message: Optional[str] = None, step_count: int = 0, cost_micros: int = 0,
                   input_tokens: int = 0, output_tokens: int = 0) -> None:
        updated = self.db.execute(text(
            "UPDATE agent_runs SET status = :status, output = CAST(:output AS json), error_kind = :ek, error_message = :em, "
            "step_count = :steps, total_cost_micros = :cost, input_tokens = :tin, output_tokens = :tout, finished_at = now() "
            "WHERE id = :rid AND organization_id = :org AND EXISTS (SELECT 1 FROM agent_tasks t WHERE t.id = :tid "
            "AND t.organization_id = :org AND t.lease_owner = :owner AND t.status = 'running')"),
            self._params(rid=run_id, status=status, output=_json(safe_json(output, 20000)) if output is not None else None,
                         ek=error_kind, em=truncate(redact_secrets(error_message or ""), 500) or None,
                         steps=step_count, cost=cost_micros, tin=input_tokens, tout=output_tokens)).rowcount
        self.db.commit()
        if not updated:
            raise LostLease()

    def completed_outputs(self) -> Dict[str, Any]:
        """plan_step_id -> output of the runs already completed (what a resumed task must NOT redo)."""
        rows = self.db.execute(text(
            "SELECT plan_step_id, output FROM agent_runs WHERE task_id = :tid AND organization_id = :org "
            "AND status = 'completed' AND plan_step_id IS NOT NULL ORDER BY sequence"), {"tid": self.task_id, "org": self.org_id}).all()
        return {r[0]: r[1] for r in rows}

    # ------------------------------------------------------------------ task
    def save_plan(self, plan: Dict[str, Any]) -> None:
        updated = self.db.execute(text(
            "UPDATE agent_tasks SET plan = CAST(:plan AS json) WHERE id = :tid AND organization_id = :org "
            "AND lease_owner = :owner AND status = 'running'"), self._params(plan=_json(plan))).rowcount
        self.db.commit()
        if not updated:
            raise LostLease()

    def fail_or_retry(self, *, error_kind: str, error_message: str, retry: bool, result: Any = None) -> Tuple[str, int]:
        """End the CURRENT attempt with an error, in one fenced statement. Returns (resulting status, attempts used).

        * cancellation was requested  -> ``cancelled``;
        * ``retry`` and attempts are left -> back to ``pending``, not claimable before an exponential back-off
          (``AGENT_RETRY_BASE_SECONDS`` * 2^(attempt-1), capped), lease dropped; the plan, the completed runs, the steps and
          the cost totals are kept, so the next attempt RESUMES instead of redoing (and cannot reset the cost cap);
        * otherwise -> ``failed`` (with the partial result, if any).
        Only the current lease holder can do it (``LostLease`` otherwise)."""
        more = "(CAST(:retry AS boolean) AND attempts < max_attempts)"
        row = self.db.execute(text(
            f"UPDATE agent_tasks SET "
            f"status = CASE WHEN cancel_requested THEN 'cancelled' WHEN {more} THEN 'pending' ELSE 'failed' END, "
            f"error_kind = CASE WHEN cancel_requested THEN 'cancelled' ELSE :ek END, error_message = :em, "
            f"result = CASE WHEN cancel_requested OR NOT {more} THEN CAST(:result AS json) ELSE result END, "
            f"available_at = CASE WHEN NOT cancel_requested AND {more} "
            f"THEN now() + least(CAST(:cap AS double precision), CAST(:base AS double precision) * power(2, greatest(attempts - 1, 0))) * interval '1 second' "
            f"ELSE NULL END, "
            f"finished_at = CASE WHEN cancel_requested OR NOT {more} THEN now() ELSE NULL END, "
            f"lease_owner = NULL, lease_expires_at = NULL, worker_id = NULL "
            f"WHERE id = :tid AND organization_id = :org AND lease_owner = :owner AND status = 'running' RETURNING status, attempts"),
            self._params(retry=retry, ek=error_kind, em=truncate(redact_secrets(error_message or ""), 500) or None,
                         result=_json(safe_json(result, 100000)) if result is not None else None,
                         cap=settings.AGENT_RETRY_MAX_SECONDS, base=settings.AGENT_RETRY_BASE_SECONDS)).first()
        self.db.commit()
        if row is None:
            raise LostLease()
        return row[0], row[1]

    def release(self) -> bool:
        """Graceful shutdown: hand the task back WITHOUT burning an attempt, immediately claimable by another worker.
        Returns False when this executor no longer owned it (nothing to release)."""
        updated = self.db.execute(text(
            "UPDATE agent_tasks SET status = 'pending', attempts = greatest(attempts - 1, 0), available_at = NULL, "
            "lease_owner = NULL, lease_expires_at = NULL, worker_id = NULL "
            "WHERE id = :tid AND organization_id = :org AND lease_owner = :owner AND status = 'running'"), self._params()).rowcount
        self.db.commit()
        return bool(updated)

    def finish_task(self, *, status: str, result: Any = None, error_kind: Optional[str] = None,
                    error_message: Optional[str] = None) -> None:
        updated = self.db.execute(text(
            "UPDATE agent_tasks SET status = :status, result = CAST(:result AS json), error_kind = :ek, error_message = :em, "
            "finished_at = now(), lease_owner = NULL, lease_expires_at = NULL "
            "WHERE id = :tid AND organization_id = :org AND lease_owner = :owner AND status = 'running'"),
            self._params(status=status, result=_json(safe_json(result, 100000)) if result is not None else None, ek=error_kind,
                         em=truncate(redact_secrets(error_message or ""), 500) or None)).rowcount
        self.db.commit()
        if not updated:
            raise LostLease()
