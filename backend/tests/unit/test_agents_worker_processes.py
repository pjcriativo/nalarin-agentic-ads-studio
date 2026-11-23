"""Agent worker as REAL separate operating-system processes (etapa 02.4).

* the FastAPI app runs under uvicorn in one process, the worker(s) in others, all against the disposable PostgreSQL;
* several workers compete for one queue; a worker is killed mid-task and another process resumes it; SIGTERM hands work back.

The workers are the real ``app.worker`` CLI started through ``tests/unit/worker_launcher.py``, which only swaps the AI Gateway
for a scripted one: no provider is contacted. Everything is torn down by the fixtures even when a test fails."""
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

from app.core.config import settings
from tests.unit.agents_support import create_task_row, world_of

BACKEND = Path(__file__).resolve().parents[2]
PROCESS_TIMEOUT = 90


@pytest.fixture
def w(make_user, make_org, add_member, login_headers):
    return world_of(make_user, make_org, add_member, login_headers)


@pytest.fixture(autouse=True)
def roomy_cap(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_MAX_ACTIVE_TASKS_PER_ORG", 100)


@pytest.fixture
def procs(tmp_path):
    started = []

    def spawn(module_args, name, **env):
        out = open(tmp_path / f"{name}.log", "wb")
        environment = {**os.environ, "PYTHONUNBUFFERED": "1", "AGENT_WORKER_HEALTH_PORT": "0", "AGENT_POLL_INTERVAL_SECONDS": "0.1",
                       "AGENT_RECOVERY_INTERVAL_SECONDS": "0.5", **env}
        proc = subprocess.Popen([sys.executable, *module_args], cwd=BACKEND, env=environment, stdout=out, stderr=subprocess.STDOUT)
        proc.log_path = tmp_path / f"{name}.log"
        started.append((proc, out))
        return proc

    def worker(name, *flags, **env):
        return spawn(["-m", "tests.unit.worker_launcher", *flags], name, **env)

    spawn.worker = worker
    yield spawn
    for proc, out in started:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=20)
        out.close()


def wait_until(predicate, timeout=PROCESS_TIMEOUT, interval=0.05, message="condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError(f"timed out waiting for {message}")


def exit_code(proc, timeout=PROCESS_TIMEOUT):
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        pytest.fail(f"process did not exit; log:\n{proc.log_path.read_text(errors='replace')[-2000:]}")


def calls(path):
    """Model calls recorded for ``path`` (one small file per call, see worker_launcher), oldest first."""
    return [f.read_text() for f in sorted(path.parent.glob(path.name + ".*"))]


def tasks(db_session, *ids):
    db_session.rollback()
    return {r.id: r for r in db_session.execute(text(
        "SELECT id, organization_id, status, attempts, worker_id, lease_owner, error_kind, total_cost_micros FROM agent_tasks WHERE id = ANY(:ids)"),
        {"ids": list(ids)}).all()}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def log_lines(proc):
    return [json.loads(line) for line in proc.log_path.read_text(errors="replace").splitlines() if line.startswith("{")]


class TestApiAndWorkerAreSeparateProcesses:
    def test_the_api_process_only_enqueues_and_a_worker_process_executes_and_publishes_the_result(self, w, procs, tmp_path, db_session):
        port = free_port()
        api = procs(["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)], "api")
        base = f"http://127.0.0.1:{port}/api/v1/agents"
        wait_until(lambda: api.poll() is None and _up(port), message="the API to answer /health")
        calls_file = tmp_path / "calls.txt"
        with httpx.Client(timeout=20) as http:
            created = http.post(f"{base}/tasks", headers=w.h["manager"],
                                json={"objective": "Analyze the widget product for search ads", "agent_key": "product_analyst"})
            assert created.status_code == 201 and created.json()["status"] == "pending"
            task_id = created.json()["id"]
            time.sleep(2.0)                                                      # the API process alone never executes it
            view = http.get(f"{base}/tasks/{task_id}", headers=w.h["analyst"]).json()
            assert view["status"] == "pending" and view["attempts"] == 0 and view["step_count"] == 0 and not calls(calls_file)
            assert http.get(f"{base}/tasks/{task_id}/result", headers=w.h["analyst"]).status_code == 409

            worker = procs.worker("worker", "--once", ECHO_DELAY="0", CALLS_FILE=str(calls_file))
            assert exit_code(worker) == 0
            done = http.get(f"{base}/tasks/{task_id}", headers=w.h["analyst"]).json()
            assert done["status"] == "completed" and done["attempts"] == 1 and done["progress"]["final"] is True
            result = http.get(f"{base}/tasks/{task_id}/result", headers=w.h["analyst"]).json()
            assert result["result"]["output"]["summary"] == "A concise analysis."
            assert http.get(f"{base}/tasks/{task_id}", headers=w.other_h).status_code == 404         # still invisible to the other tenant
        assert calls(calls_file) == [f"{w.org.id}|agent.product_analyst"]                            # one model call, made by the worker
        assert api.poll() is None                                                                    # the API process kept running throughout
        events = [line["event"] for line in log_lines(worker)]
        assert events[0] == "worker.started" and "task.claimed" in events and events[-1] == "worker.stopped"
        row = tasks(db_session, task_id)[task_id]
        assert row.worker_id and row.lease_owner is None

    def test_the_api_stays_responsive_while_a_worker_is_busy(self, w, procs, tmp_path):
        port = free_port()
        api = procs(["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)], "api")
        wait_until(lambda: api.poll() is None and _up(port), message="the API")
        calls_file = tmp_path / "calls.txt"
        with httpx.Client(timeout=20) as http:
            task_id = http.post(f"http://127.0.0.1:{port}/api/v1/agents/tasks", headers=w.h["manager"],
                                json={"objective": "Analyze the widget product", "agent_key": "product_analyst"}).json()["id"]
            procs.worker("slow", WORKER_SCRIPT=json.dumps([{"sleep": 4, "then": "product"}]), CALLS_FILE=str(calls_file))
            wait_until(lambda: calls(calls_file), message="the worker to start the model call")
            started = time.monotonic()
            for _ in range(5):
                assert http.get(f"http://127.0.0.1:{port}/api/v1/agents/tasks/{task_id}", headers=w.h["analyst"]).json()["status"] == "running"
            assert time.monotonic() - started < 3                                # polling a running task is not blocked by the worker


def _up(port):
    try:
        return httpx.get(f"http://127.0.0.1:{port}/health", timeout=1).status_code == 200
    except httpx.HTTPError:
        return False


class TestSeveralWorkers:
    def test_three_workers_share_one_queue_exactly_once_and_respect_the_global_and_organization_limits(self, w, procs, tmp_path, db_session):
        mine = [create_task_row(w.org, w.users["manager"], agent_key="product_analyst", objective=f"Analyze product {i} for ads") for i in range(9)]
        theirs = [create_task_row(w.other, w.other_owner, agent_key="product_analyst", objective=f"Analyze item {i} for ads") for i in range(6)]
        everything = mine + theirs
        calls_file = tmp_path / "calls.txt"
        limits = dict(AGENT_GLOBAL_MAX_RUNNING="3", AGENT_ORG_MAX_RUNNING="2", AGENT_WORKER_CONCURRENCY="2",
                      ECHO_DELAY="1.2", CALLS_FILE=str(calls_file))
        workers = [procs.worker(f"w{i}", "--once", **limits) for i in range(3)]

        peak_global = peak_org = 0
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            db_session.rollback()
            rows = db_session.execute(text("SELECT organization_id, status FROM agent_tasks WHERE id = ANY(:ids)"), {"ids": everything}).all()
            running = [r.organization_id for r in rows if r.status == "running"]
            peak_global = max(peak_global, len(running))
            for org in (w.org.id, w.other.id):
                peak_org = max(peak_org, running.count(org))
            if all(r.status in ("completed", "failed", "cancelled") for r in rows):
                break
            time.sleep(0.03)
        assert [exit_code(p) for p in workers] == [0, 0, 0]

        final = tasks(db_session, *everything)
        assert {r.status for r in final.values()} == {"completed"}
        assert all(r.attempts == 1 for r in final.values())                                       # nothing ran twice
        runs = db_session.execute(text("SELECT task_id, count(*) FROM agent_runs WHERE task_id = ANY(:ids) GROUP BY task_id"), {"ids": everything}).all()
        assert len(runs) == 15 and all(n == 1 for _t, n in runs)
        lines = calls(calls_file)
        assert len(lines) == 15 and lines.count(f"{w.org.id}|agent.product_analyst") == 9 and lines.count(f"{w.other.id}|agent.product_analyst") == 6
        assert 2 <= peak_global <= 3 and 1 <= peak_org <= 2                                       # limits held, and it really ran in parallel
        assert len({r.worker_id for r in final.values()}) >= 2                                    # more than one process did the work
        for worker in workers:
            started = next(line for line in log_lines(worker) if line["event"] == "worker.started")
            assert started["global_max"] == 3 and started["org_max"] == 2

    def test_two_organizations_in_the_queue_each_keep_their_own_results(self, w, procs, tmp_path, db_session):
        a = create_task_row(w.org, w.users["manager"], agent_key="product_analyst", objective="Analyze the alpha product for ads")
        b = create_task_row(w.other, w.other_owner, agent_key="product_analyst", objective="Analyze the beta product for ads")
        procs.worker("w1", "--once", ECHO_DELAY="0.2", CALLS_FILE=str(tmp_path / "c.txt"))
        wait_until(lambda: {r.status for r in tasks(db_session, a, b).values()} == {"completed"}, message="both tasks")
        db_session.rollback()
        for task_id, org in ((a, w.org), (b, w.other)):
            owners = db_session.execute(text("SELECT DISTINCT organization_id FROM agent_steps WHERE task_id = :t "
                                             "UNION SELECT DISTINCT organization_id FROM agent_runs WHERE task_id = :t "
                                             "UNION SELECT DISTINCT organization_id FROM audit_logs WHERE target = :t"), {"t": task_id}).scalars().all()
            assert owners == [org.id]


class TestKillAndRestart:
    def test_a_killed_worker_leaves_the_task_to_be_resumed_by_another_process_without_redoing_finished_steps(self, w, procs, tmp_path, db_session):
        task_id = create_task_row(w.org, w.users["manager"], objective="Launch widgets on Google Search")
        first_calls = tmp_path / "first.txt"
        victim = procs.worker("victim", WORKER_SCRIPT=json.dumps(["plan", "product", {"sleep": 300, "then": "copy"}]), CALLS_FILE=str(first_calls))
        wait_until(lambda: len(calls(first_calls)) == 3, message="the worker to reach the copywriter")
        assert calls(first_calls) == [f"{w.org.id}|agent.supervisor", f"{w.org.id}|agent.product_analyst", f"{w.org.id}|agent.ad_copywriter"]
        victim.kill()                                                            # SIGKILL: no handler, no release, no finalization
        victim.wait(timeout=20)
        row = tasks(db_session, task_id)[task_id]
        assert row.status == "running" and row.lease_owner and row.attempts == 1
        done = db_session.execute(text("SELECT plan_step_id, status FROM agent_runs WHERE task_id = :t ORDER BY sequence"), {"t": task_id}).all()
        assert [tuple(r) for r in done] == [("s1", "completed"), ("s2", "running")]

        bystander = procs.worker("bystander", "--once", ECHO_DELAY="0", CALLS_FILE=str(tmp_path / "bystander.txt"))
        assert exit_code(bystander) == 0
        assert tasks(db_session, task_id)[task_id].status == "running" and not calls(tmp_path / "bystander.txt")   # a live lease is NOT stolen
        db_session.execute(text("UPDATE agent_tasks SET lease_expires_at = now() - interval '1 minute' WHERE id = :t"), {"t": task_id})
        db_session.commit()                                                      # ...until it expires: what 240 s without a heartbeat looks like
        second_calls = tmp_path / "second.txt"
        rescuer = procs.worker("rescuer", "--once", WORKER_SCRIPT=json.dumps(["copy", "report"]), CALLS_FILE=str(second_calls))
        assert exit_code(rescuer) == 0

        assert calls(second_calls) == [f"{w.org.id}|agent.ad_copywriter", f"{w.org.id}|agent.supervisor"]   # no new plan, no repeated analyst
        row = tasks(db_session, task_id)[task_id]
        assert row.status == "completed" and row.attempts == 2 and row.lease_owner is None
        db_session.rollback()
        runs = db_session.execute(text("SELECT plan_step_id, status, error_kind FROM agent_runs WHERE task_id = :t ORDER BY sequence"), {"t": task_id}).all()
        assert [tuple(r) for r in runs] == [("s1", "completed", None), ("s2", "failed", "interrupted"), ("s2", "completed", None)]
        assert db_session.execute(text("SELECT count(*) FROM agent_steps WHERE task_id = :t AND kind = 'llm'"), {"t": task_id}).scalar() == 4
        assert row.total_cost_micros == 4000                                      # each paid call counted exactly once
        actions = db_session.execute(text("SELECT action FROM audit_logs WHERE target = :t ORDER BY created_at"), {"t": task_id}).scalars().all()
        assert "agent.task.recovered" in actions and actions.count("agent.task.started") == 2 and actions[-1] == "agent.task.completed"
        assert any(line["event"] == "worker.recovered" for line in log_lines(rescuer))

    def test_two_live_workers_cannot_both_run_a_task_even_when_recovery_races_with_the_owner(self, w, procs, tmp_path, db_session):
        task_id = create_task_row(w.org, w.users["manager"], agent_key="product_analyst", objective="Analyze the widget product for ads")
        calls_file = tmp_path / "calls.txt"
        owner = procs.worker("owner", WORKER_SCRIPT=json.dumps([{"sleep": 3, "then": "product"}]), CALLS_FILE=str(calls_file),
                             AGENT_RECOVERY_INTERVAL_SECONDS="0.2")
        wait_until(lambda: calls(calls_file), message="the owner to start")
        rival = procs.worker("rival", "--once", ECHO_DELAY="0", CALLS_FILE=str(calls_file), AGENT_RECOVERY_INTERVAL_SECONDS="0.2")
        assert exit_code(rival) == 0                                              # nothing claimable and the lease is live: it leaves
        wait_until(lambda: tasks(db_session, task_id)[task_id].status == "completed", message="the owner to finish")
        assert len(calls(calls_file)) == 1 and tasks(db_session, task_id)[task_id].attempts == 1
        owner.kill()


@pytest.mark.skipif(os.name == "nt", reason="POSIX signals: Windows has no SIGTERM delivery to a child (the in-process shutdown tests cover the logic)")
class TestSigterm:
    def test_sigterm_hands_the_running_task_back_without_burning_an_attempt_and_another_worker_finishes_it(self, w, procs, tmp_path, db_session):
        task_id = create_task_row(w.org, w.users["manager"], agent_key="product_analyst", objective="Analyze the widget product for ads")
        calls_file = tmp_path / "calls.txt"
        worker = procs.worker("term", WORKER_SCRIPT=json.dumps([{"sleep": 300, "then": "product"}]), CALLS_FILE=str(calls_file),
                              AGENT_SHUTDOWN_GRACE_SECONDS="1")
        wait_until(lambda: calls(calls_file), message="the worker to start the model call")
        worker.send_signal(signal.SIGTERM)
        assert exit_code(worker, 30) == 0                                         # a clean exit, well before the 300 s model call
        row = tasks(db_session, task_id)[task_id]
        assert row.status == "pending" and row.attempts == 0 and row.lease_owner is None
        assert "agent.task.released" in db_session.execute(text("SELECT action FROM audit_logs WHERE target = :t"), {"t": task_id}).scalars().all()
        events = [line["event"] for line in log_lines(worker)]
        assert "worker.stop_requested" in events and "worker.draining" in events and events[-1] == "worker.stopped"
        finisher = procs.worker("finish", "--once", ECHO_DELAY="0", CALLS_FILE=str(tmp_path / "again.txt"))
        assert exit_code(finisher) == 0
        row = tasks(db_session, task_id)[task_id]
        assert row.status == "completed" and row.attempts == 1
