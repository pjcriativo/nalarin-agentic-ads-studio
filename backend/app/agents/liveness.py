"""Worker liveness (etapa 02.6): workers report in, the API reads "is any worker alive?". Kept apart from the claim queue
(app.agents.queue) on purpose: HTTP handlers may import THIS module, never the claim/execute side. Non-tenant table
``agent_workers``: worker id, timestamps and two counters, no tenant data."""
from sqlalchemy import text

from app.core.config import settings
from app.database import SessionLocal


def beat(worker_id: str, *, active: int, concurrency: int, status: str = "running") -> None:
    """Upsert this worker's liveness row (non-tenant table: worker id, timestamps and two counters only)."""
    db = SessionLocal()
    try:
        db.execute(text(
            "INSERT INTO agent_workers (worker_id, status, started_at, last_seen_at, active_tasks, concurrency) "
            "VALUES (:wid, :status, now(), now(), :active, :conc) "
            "ON CONFLICT (worker_id) DO UPDATE SET status = :status, last_seen_at = now(), active_tasks = :active, concurrency = :conc"),
            {"wid": worker_id, "status": status, "active": active, "conc": concurrency})
        # rows of workers that vanished long ago are not kept forever
        db.execute(text("DELETE FROM agent_workers WHERE last_seen_at < now() - interval '1 day'"))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def online_workers(now=None) -> int:
    """How many workers reported in recently enough to be considered alive (a stopped one never counts)."""
    db = SessionLocal()
    try:
        return int(db.execute(text(
            "SELECT count(*) FROM agent_workers WHERE status = 'running' "
            "AND last_seen_at > now() - make_interval(secs => :secs)"), {"secs": settings.AGENT_WORKER_ONLINE_SECONDS}).scalar() or 0)
    finally:
        db.rollback()
        db.close()
