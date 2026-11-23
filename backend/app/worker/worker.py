"""The agent worker: a process independent of the FastAPI app that executes agent tasks.

    python -m app.worker              # run until SIGTERM / SIGINT
    python -m app.worker --once       # recover, drain the claimable queue, exit (jobs, diagnostics, tests)
    python -m app.worker --healthcheck  # exit 0 when the local worker answers /healthz (Docker HEALTHCHECK)

Responsibilities, all built on the existing executor / supervisor / agents / AI Gateway (nothing is duplicated here):

* POLL the PostgreSQL queue and claim tasks atomically with global and per-organization limits (app.agents.queue);
* run up to ``AGENT_WORKER_CONCURRENCY`` tasks at once in this process (each under its own organization's RLS context);
* every running task renews its lease (heartbeat + each model turn); every write is fenced by the lease owner;
* RECOVER periodically: tasks of dead workers (expired lease) are re-queued, or failed after ``max_attempts`` (idempotent
  and safe to run from every worker at once);
* RETRY transient failures with exponential back-off (decided inside ``executor.execute_task``);
* SHUT DOWN gracefully: stop claiming, let running tasks finish for ``AGENT_SHUTDOWN_GRACE_SECONDS``, then hand the rest back
  to the queue WITHOUT burning an attempt, so another worker resumes them at once (a hard kill is covered by the lease);
* expose ``/healthz`` and log structured JSON without secrets or prompts (app.worker.logs).
"""
import argparse
import asyncio
import json
import logging
import os
import signal
import socket
import sys
import time
import urllib.request
import uuid
from typing import Dict, List, Optional

from app.agents import executor, liveness, queue
from app.core.audit import audit_event
from app.core.config import settings
from app.worker.logs import configure_logging, log_event

logger = logging.getLogger("agent_worker")


def default_worker_id() -> str:
    return os.getenv("AGENT_WORKER_ID") or f"{socket.gethostname()[:30]}-{os.getpid()}-{uuid.uuid4().hex[:6]}"


class AgentWorker:
    def __init__(self, *, worker_id: Optional[str] = None, concurrency: Optional[int] = None, once: bool = False,
                 poll_interval: Optional[float] = None, recovery_interval: Optional[float] = None,
                 shutdown_grace: Optional[float] = None, health_port: Optional[int] = None, handle_signals: bool = True):
        self.worker_id = worker_id or default_worker_id()
        self.concurrency = concurrency or settings.AGENT_WORKER_CONCURRENCY
        self.once = once
        self.poll_interval = settings.AGENT_POLL_INTERVAL_SECONDS if poll_interval is None else poll_interval
        self.recovery_interval = settings.AGENT_RECOVERY_INTERVAL_SECONDS if recovery_interval is None else recovery_interval
        self.shutdown_grace = settings.AGENT_SHUTDOWN_GRACE_SECONDS if shutdown_grace is None else shutdown_grace
        self.health_port = settings.AGENT_WORKER_HEALTH_PORT if health_port is None else health_port
        self.handle_signals = handle_signals
        self.stopping: Optional[asyncio.Event] = None
        self.active: Dict[asyncio.Task, queue.Claim] = {}
        self.started_at = time.monotonic()
        self.last_poll = time.monotonic()
        self.db_ok = True
        self.processed: Dict[str, int] = {}
        self._wake: Optional[asyncio.Event] = None
        self._server: Optional[asyncio.AbstractServer] = None

    # ------------------------------------------------------------------ lifecycle
    def stop(self) -> None:
        if self.stopping is not None and not self.stopping.is_set():
            log_event(logger, "worker.stop_requested", worker=self.worker_id, active=len(self.active))
            self.stopping.set()
            if self._wake is not None:
                self._wake.set()

    def _install_signals(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self.stop)
            except (NotImplementedError, RuntimeError, ValueError):   # Windows / not the main thread
                try:
                    signal.signal(sig, lambda *_a: loop.call_soon_threadsafe(self.stop))
                except ValueError:
                    pass

    async def run(self) -> Dict[str, int]:
        self.stopping, self._wake = asyncio.Event(), asyncio.Event()
        if self.handle_signals:
            self._install_signals()
        await self._start_health()
        log_event(logger, "worker.started", worker=self.worker_id, concurrency=self.concurrency, once=self.once,
                  global_max=settings.AGENT_GLOBAL_MAX_RUNNING, org_max=settings.AGENT_ORG_MAX_RUNNING)
        next_recovery = 0.0
        next_beat = 0.0
        try:
            while not self.stopping.is_set():
                self.last_poll = time.monotonic()
                if self.last_poll >= next_recovery:
                    await self._recover()
                    next_recovery = time.monotonic() + self.recovery_interval
                if self.last_poll >= next_beat:
                    await self._beat("running")
                    next_beat = time.monotonic() + settings.AGENT_WORKER_BEAT_SECONDS
                claimed = await self._fill()
                if self.once and not self.active and not claimed:
                    break
                await self._wait(fast=bool(claimed))
        finally:
            await self._shutdown()
        return dict(self.processed)

    async def _wait(self, *, fast: bool) -> None:
        """Sleep until: stop requested, a task finished (a slot opened), or the poll interval passed."""
        self._wake.clear()
        waiters = [asyncio.ensure_future(self._wake.wait())]
        try:
            await asyncio.wait(waiters, timeout=0 if fast and len(self.active) < self.concurrency else self.poll_interval)
        finally:
            for w in waiters:
                w.cancel()

    async def _fill(self) -> int:
        claimed = 0
        while len(self.active) < self.concurrency and not self.stopping.is_set():
            try:
                claim = await asyncio.to_thread(queue.claim_next, self.worker_id)
                self.db_ok = True
            except Exception as exc:  # noqa: BLE001 - the database may be briefly down; keep polling
                self.db_ok = False
                log_event(logger, "worker.db_error", logging.ERROR, worker=self.worker_id, error_type=type(exc).__name__)
                return claimed
            if claim is None:
                break
            claimed += 1
            task = asyncio.get_running_loop().create_task(self._run_claim(claim))
            self.active[task] = claim
            task.add_done_callback(self._done)
        return claimed

    def _done(self, task: asyncio.Task) -> None:
        self.active.pop(task, None)
        if self._wake is not None:
            self._wake.set()

    async def _run_claim(self, claim: queue.Claim) -> str:
        started = time.monotonic()
        log_event(logger, "task.claimed", worker=self.worker_id, task=claim.task_id, org=claim.org_id, attempt=claim.attempt)
        status = "failed"
        try:
            status = await executor.execute_task(claim.task_id, claim.org_id, owner=claim.owner)
        except asyncio.CancelledError:
            # shutdown grace elapsed: hand the task back untouched so another worker resumes it right away
            status = "released" if await asyncio.to_thread(self._release, claim) else "lost_lease"
        except Exception as exc:  # noqa: BLE001 - execute_task handles its own errors; this is the last line of defence
            log_event(logger, "task.worker_error", logging.ERROR, worker=self.worker_id, task=claim.task_id, error_type=type(exc).__name__)
        self.processed[status] = self.processed.get(status, 0) + 1
        log_event(logger, "task.finished", worker=self.worker_id, task=claim.task_id, org=claim.org_id, attempt=claim.attempt,
                  status=status, seconds=round(time.monotonic() - started, 3))
        return status

    @staticmethod
    def _release(claim: queue.Claim) -> bool:
        released = queue.release(claim)
        if released:
            db = executor._session(claim.org_id)
            try:
                audit_event(db, action="agent.task.released", organization_id=claim.org_id, actor_user_id=None, target=claim.task_id,
                            detail={"reason": "worker_shutdown"})
                db.commit()
            except Exception:  # noqa: BLE001 - the audit must never block handing the task back
                db.rollback()
            finally:
                db.close()
        return released

    async def _beat(self, status: str) -> None:
        """Tell the API this worker is alive (or has stopped). A failure only shows up in health; it never stops the loop."""
        try:
            await asyncio.to_thread(liveness.beat, self.worker_id, active=len(self.active), concurrency=self.concurrency, status=status)
        except Exception as exc:  # noqa: BLE001
            self.db_ok = False
            log_event(logger, "worker.beat_error", logging.ERROR, worker=self.worker_id, error_type=type(exc).__name__)

    async def _recover(self) -> None:
        try:
            outcome = await asyncio.to_thread(executor.recover_interrupted_tasks)
        except Exception as exc:  # noqa: BLE001
            self.db_ok = False
            log_event(logger, "worker.recovery_error", logging.ERROR, worker=self.worker_id, error_type=type(exc).__name__)
            return
        if outcome["recovered"]:
            log_event(logger, "worker.recovered", worker=self.worker_id, tasks=len(outcome["recovered"]),
                      failed=sum(1 for r in outcome["recovered"] if r[2] == "failed"))

    async def _shutdown(self) -> None:
        self.stopping.set()
        pending: List[asyncio.Task] = list(self.active)
        if pending:
            log_event(logger, "worker.draining", worker=self.worker_id, active=len(pending), grace_seconds=self.shutdown_grace)
            _done, still = await asyncio.wait(pending, timeout=self.shutdown_grace)
            for task in still:
                task.cancel()
            if still:
                await asyncio.gather(*still, return_exceptions=True)
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        await self._beat("stopped")
        log_event(logger, "worker.stopped", worker=self.worker_id, processed=json.dumps(self.processed, sort_keys=True))

    # ------------------------------------------------------------------ health
    def health(self) -> Dict[str, object]:
        age = time.monotonic() - self.last_poll
        stopping = bool(self.stopping and self.stopping.is_set())
        healthy = self.db_ok and age < settings.AGENT_WORKER_STALE_SECONDS and not stopping
        return {"status": "ok" if healthy else ("stopping" if stopping else "unhealthy"), "worker": self.worker_id,
                "active_tasks": len(self.active), "concurrency": self.concurrency, "database": "ok" if self.db_ok else "error",
                "last_poll_seconds_ago": round(age, 1), "uptime_seconds": round(time.monotonic() - self.started_at, 1)}

    async def _start_health(self) -> None:
        if not self.health_port:
            return

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                line = await asyncio.wait_for(reader.readline(), timeout=3)
                path = line.decode("latin-1").split(" ")[1] if line else ""
                report = self.health()
                ok = path.split("?")[0] in ("/healthz", "/health", "/") and report["status"] == "ok"
                code = "200 OK" if ok else ("404 Not Found" if path.split("?")[0] not in ("/healthz", "/health", "/") else "503 Service Unavailable")
                body = json.dumps(report if code != "404 Not Found" else {"status": "not_found"}).encode()
                writer.write(f"HTTP/1.1 {code}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)
                await writer.drain()
            except Exception:  # noqa: BLE001 - a broken probe must not affect the worker
                pass
            finally:
                writer.close()

        try:
            self._server = await asyncio.start_server(handle, settings.AGENT_WORKER_HEALTH_HOST, self.health_port)
        except OSError as exc:
            log_event(logger, "worker.health_unavailable", logging.WARNING, worker=self.worker_id, error_type=type(exc).__name__)


def healthcheck() -> int:
    """Docker HEALTHCHECK: 0 when the worker answers /healthz with 200."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{settings.AGENT_WORKER_HEALTH_PORT}/healthz", timeout=4) as resp:  # nosec B310 - fixed local URL
            return 0 if resp.status == 200 else 1
    except Exception:  # noqa: BLE001
        return 1


def _preflight() -> None:
    """Same guard as the API: refuse to run when the database role could bypass row level security."""
    from sqlalchemy import text
    from app.database import engine, verify_runtime_role_is_safe
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    verify_runtime_role_is_safe()


def cli(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.worker", description="Agent task worker")
    parser.add_argument("--once", action="store_true", help="recover, drain the claimable queue and exit")
    parser.add_argument("--healthcheck", action="store_true", help="probe the local worker and exit 0/1")
    parser.add_argument("--log-level", default=os.getenv("LOG_LEVEL", "INFO"))
    args = parser.parse_args(argv)
    if args.healthcheck:
        return healthcheck()
    configure_logging(args.log_level)
    try:
        _preflight()
    except Exception as exc:  # noqa: BLE001
        log_event(logger, "worker.preflight_failed", logging.CRITICAL, error_type=type(exc).__name__)
        return 2
    asyncio.run(AgentWorker(once=args.once).run())
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
