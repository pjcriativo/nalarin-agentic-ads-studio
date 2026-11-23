"""API additions of the agents dashboard (etapa 02.6): the supervisor card and the worker liveness / queue status."""
import asyncio
import json

import pytest
from sqlalchemy import text

from app.agents import liveness
from app.core.config import settings
from app.worker.worker import AgentWorker
from tests.unit.agents_support import create_task_row, world_of

P = "/api/v1/agents"


@pytest.fixture
def w(make_user, make_org, add_member, login_headers):
    return world_of(make_user, make_org, add_member, login_headers)


@pytest.fixture(autouse=True)
def roomy(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_MAX_ACTIVE_TASKS_PER_ORG", 100)


class TestSupervisorCard:
    def test_it_describes_the_supervisor_honestly(self, client, w):
        r = client.get(f"{P}/supervisor", headers=w.h["analyst"])
        assert r.status_code == 200
        card = r.json()
        assert card["key"] == "supervisor" and card["role"] == "supervisor" and card["enabled"] is True
        assert card["allowed_tools"] == [] and card["output_schema"] is None and card["limitations"] and card["readiness"].startswith("operational")
        assert card["routing"]["operation"] == "agent.supervisor"
        assert "instructions" not in card and "SECURITY RULES" not in r.text                       # prompts are never served

    def test_it_is_not_in_the_specialist_list_and_cannot_be_configured_as_one(self, client, w):
        assert "supervisor" not in {a["key"] for a in client.get(P, headers=w.h["analyst"]).json()}
        assert client.put(f"{P}/supervisor/settings", headers=w.h["admin"], json={"enabled": False}).status_code == 404

    @pytest.mark.parametrize("role,allowed", [("owner", True), ("admin", True), ("manager", True), ("analyst", True), ("viewer", False)])
    def test_rbac(self, client, w, role, allowed):
        assert (client.get(f"{P}/supervisor", headers=w.h[role]).status_code == 200) is allowed

    def test_it_needs_authentication(self, client):
        assert client.get(f"{P}/supervisor").status_code in (401, 403)


class TestWorkerStatus:
    def test_without_any_worker_it_says_offline_and_counts_only_my_queue(self, client, w):
        create_task_row(w.org, w.users["manager"], agent_key="product_analyst")
        create_task_row(w.other, w.other_owner, agent_key="product_analyst")
        create_task_row(w.other, w.other_owner, agent_key="product_analyst")
        body = client.get(f"{P}/worker/status", headers=w.h["analyst"]).json()
        assert body["online"] is False and body["workers"] == 0
        assert body["queue"]["pending"] == 1 and body["queue"]["running"] == 0 and isinstance(body["queue"]["oldest_pending_seconds"], int)
        assert client.get(f"{P}/worker/status", headers=w.other_h).json()["queue"]["pending"] == 2

    def test_a_recent_beat_means_online_and_staleness_or_a_stop_means_offline(self, client, w, db_session):
        liveness.beat("worker-a", active=1, concurrency=2)
        body = client.get(f"{P}/worker/status", headers=w.h["analyst"]).json()
        assert body["online"] is True and body["workers"] == 1 and body["online_window_seconds"] == settings.AGENT_WORKER_ONLINE_SECONDS
        db_session.execute(text("UPDATE agent_workers SET last_seen_at = now() - interval '10 minutes'"))
        db_session.commit()
        assert client.get(f"{P}/worker/status", headers=w.h["analyst"]).json()["online"] is False
        liveness.beat("worker-a", active=0, concurrency=2)
        liveness.beat("worker-b", active=0, concurrency=1, status="stopped")
        body = client.get(f"{P}/worker/status", headers=w.h["analyst"]).json()
        assert body["online"] is True and body["workers"] == 1                                       # the stopped one does not count

    def test_no_worker_identity_or_task_content_is_exposed(self, client, w):
        liveness.beat("worker-secret-host-123", active=0, concurrency=1)
        create_task_row(w.org, w.users["manager"], agent_key="product_analyst", objective="CONFIDENTIAL launch plan for the widget")
        raw = client.get(f"{P}/worker/status", headers=w.h["analyst"]).text
        assert "worker-secret-host-123" not in raw and "CONFIDENTIAL" not in raw

    @pytest.mark.parametrize("role,allowed", [("owner", True), ("admin", True), ("manager", True), ("analyst", True), ("viewer", False)])
    def test_rbac(self, client, w, role, allowed):
        assert (client.get(f"{P}/worker/status", headers=w.h[role]).status_code == 200) is allowed

    def test_a_real_worker_loop_reports_running_while_alive_and_stopped_after_a_graceful_stop(self, client, w, db_session, monkeypatch):
        monkeypatch.setattr(settings, "AGENT_WORKER_BEAT_SECONDS", 0.05)
        seen = {}

        async def go():
            worker = AgentWorker(worker_id="loop-worker", once=False, health_port=0, handle_signals=False, poll_interval=0.02, concurrency=3)
            run = asyncio.get_running_loop().create_task(worker.run())
            await asyncio.sleep(0.4)
            seen["during"] = await asyncio.to_thread(lambda: client.get(f"{P}/worker/status", headers=w.h["analyst"]).json())
            worker.stop()
            await run
        asyncio.run(go())
        assert seen["during"]["online"] is True
        row = db_session.execute(text("SELECT status, concurrency FROM agent_workers WHERE worker_id = 'loop-worker'")).one()
        assert tuple(row) == ("stopped", 3)
        assert client.get(f"{P}/worker/status", headers=w.h["analyst"]).json()["online"] is False

    def test_the_liveness_table_holds_no_tenant_data(self, db_session):
        cols = {r[0] for r in db_session.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'agent_workers'"))}
        assert cols == {"worker_id", "status", "started_at", "last_seen_at", "active_tasks", "concurrency"}
