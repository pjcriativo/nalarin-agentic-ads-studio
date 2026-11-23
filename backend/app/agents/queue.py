"""PostgreSQL as the persistent task queue (etapa 02.4): atomic claim with global and per-organization concurrency limits.

The API only INSERTs ``pending`` rows (``executor.create_task``). A worker takes the next one with ``claim_next``:

* ONE statement ``UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED)`` turns the oldest claimable pending task into
  ``running`` with a fresh lease (``lease_owner`` = a new fencing token). Two workers can never take the same row.
* The concurrency limits are counts over ``running`` rows with an unexpired lease, so they hold across ALL worker
  processes. To keep two workers from both seeing "one slot left", the check + claim run under a transaction-scoped
  advisory lock (held for a few milliseconds; execution itself is never under it).
* Tasks of one organization never exceed ``AGENT_ORG_MAX_RUNNING`` and the platform never exceeds ``AGENT_GLOBAL_MAX_RUNNING``;
  a noisy organization therefore cannot starve the others (its extra tasks simply stay pending).
* A pending task is claimable only when ``available_at`` (retry back-off) has passed and no cancellation was requested.

The claim runs in the signed platform-admin context (the single extra ``platform_recovery`` policy of ``agent_tasks``): it
selects ids and changes state/lease columns only. The task itself is then executed inside its OWN organization's RLS
context (``executor.execute_task``).
"""
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import text

from app.agents.store import TaskStore, utcnow
from app.core.config import settings
from app.database import SessionLocal, bind_context

logger = logging.getLogger(__name__)
_CLAIM_LOCK = "agent-queue-claim"


@dataclass(frozen=True)
class Claim:
    task_id: str
    org_id: str
    owner: str          # the fencing token: every write of the executor is conditioned on it
    attempt: int


def claim_next(worker_id: str, now: Optional[datetime] = None, *, global_max: Optional[int] = None,
               org_max: Optional[int] = None) -> Optional[Claim]:
    """Atomically claim the oldest claimable pending task, honouring the concurrency limits. None when nothing can start."""
    now = now or utcnow()
    global_max = settings.AGENT_GLOBAL_MAX_RUNNING if global_max is None else global_max
    org_max = settings.AGENT_ORG_MAX_RUNNING if org_max is None else org_max
    owner = str(uuid.uuid4())
    db = SessionLocal()
    bind_context(db, None, platform_admin=True)
    try:
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": _CLAIM_LOCK})
        running = db.execute(text("SELECT count(*) FROM agent_tasks WHERE status = 'running' AND lease_expires_at >= :now"),
                             {"now": now}).scalar()
        if running >= global_max:
            db.rollback()
            return None
        row = db.execute(text(
            "UPDATE agent_tasks SET status = 'running', lease_owner = :owner, lease_expires_at = :exp, heartbeat_at = :now, "
            "started_at = coalesce(started_at, :now), attempts = attempts + 1, error_kind = NULL, error_message = NULL, "
            "available_at = NULL, worker_id = :wid "
            "WHERE id = (SELECT p.id FROM agent_tasks p "
            "            WHERE p.status = 'pending' AND NOT p.cancel_requested AND (p.available_at IS NULL OR p.available_at <= :now) "
            "              AND (SELECT count(*) FROM agent_tasks r WHERE r.organization_id = p.organization_id "
            "                   AND r.status = 'running' AND r.lease_expires_at >= :now) < :org_max "
            "            ORDER BY coalesce(p.available_at, p.created_at), p.created_at LIMIT 1 FOR UPDATE SKIP LOCKED) "
            "RETURNING id, organization_id, attempts"),
            {"owner": owner, "exp": now + timedelta(seconds=settings.AGENT_LEASE_SECONDS), "now": now, "wid": worker_id,
             "org_max": org_max}).first()
        db.commit()
        return Claim(row[0], row[1], owner, row[2]) if row else None
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def release(claim: Claim) -> bool:
    """Give a claimed task back (graceful shutdown) without burning an attempt. Fenced by the claim's owner token."""
    from app.agents.executor import _session
    db = _session(claim.org_id)
    try:
        return TaskStore(db, claim.org_id, claim.task_id, claim.owner).release()
    finally:
        db.close()


def depth() -> dict:
    """Queue size for health/diagnostics (counts only; no tenant content)."""
    db = SessionLocal()
    bind_context(db, None, platform_admin=True)
    try:
        rows = db.execute(text("SELECT status, count(*) FROM agent_tasks WHERE status IN ('pending', 'running') GROUP BY status")).all()
        db.rollback()
        return {status: n for status, n in rows}
    finally:
        db.close()
