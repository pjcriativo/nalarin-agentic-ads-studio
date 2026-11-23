"""AI agents: tasks, runs, steps and per-organization settings (etapa 02.3)

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-11-28

Four organization-scoped tables under FORCE RLS ``tenant_isolation``:

* ``agent_tasks``: the request, its state machine (pending/running/completed/failed/cancelled), limits,
  totals and a FENCING lease (``lease_owner`` + ``lease_expires_at``) so a task abandoned by a dead
  process can be re-queued without two executors ever working on it.
* ``agent_runs``: one specialist execution inside a task (composite FK keeps it in the task's organization).
* ``agent_steps``: append-only history (UPDATE/DELETE/TRUNCATE revoked from the application role).
* ``agent_settings``: per-organization enable switch and capped limit overrides.

``agent_tasks`` additionally has ONE extra policy, ``platform_recovery``, that only the signed platform-admin
context can satisfy. It exists solely so the recovery job (app.agents.executor.recover_interrupted_tasks) can
find interrupted tasks of every organization; no request path ever binds that context.
"""
import os
import re
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, Sequence[str], None] = "d0e1f2a3b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ("agent_tasks", "agent_runs", "agent_steps", "agent_settings")
POLICY = "organization_id = (SELECT app_current_org())"
_ROLE_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _role() -> str:
    role = os.getenv("DB_RUNTIME_ROLE", "ads_app").strip()
    if role and not _ROLE_RE.match(role):
        raise ValueError("DB_RUNTIME_ROLE must be a plain lowercase identifier")
    return role


def _org_col() -> sa.Column:
    return sa.Column("organization_id", sa.String(), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False)


def _now(name: str = "created_at") -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.text("now()"))


def _ts(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=True)


def upgrade() -> None:
    op.create_table(
        "agent_tasks",
        sa.Column("id", sa.String(), primary_key=True), _org_col(),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.Column("agent_key", sa.String(), nullable=True),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("input_context", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("plan", sa.JSON(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error_kind", sa.String(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("lease_owner", sa.String(), nullable=True),
        _ts("lease_expires_at"), _ts("heartbeat_at"),
        sa.Column("limits", sa.JSON(), nullable=True),
        sa.Column("max_cost_micros", sa.BigInteger(), nullable=True),
        sa.Column("total_cost_micros", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_input_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_output_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("step_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("idempotency_key", sa.String(), nullable=True),
        _ts("deadline_at"), _ts("started_at"), _ts("finished_at"),
        _now(), _now("updated_at"),
        sa.UniqueConstraint("id", "organization_id", name="uq_agent_tasks_id_org"),
        sa.CheckConstraint("status IN ('pending','running','completed','failed','cancelled')", name="ck_agent_tasks_status"),
    )
    op.create_index("ix_agent_tasks_org_status", "agent_tasks", ["organization_id", "status"])
    op.create_index("ix_agent_tasks_org_created", "agent_tasks", ["organization_id", "created_at"])
    op.create_index("uq_agent_tasks_idempotency", "agent_tasks", ["organization_id", "idempotency_key"], unique=True,
                    postgresql_where=sa.text("idempotency_key IS NOT NULL"))
    op.create_index("ix_agent_tasks_running_lease", "agent_tasks", ["lease_expires_at"],
                    postgresql_where=sa.text("status = 'running'"))

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(), primary_key=True), _org_col(),
        sa.Column("task_id", sa.String(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("plan_step_id", sa.String(), nullable=True),
        sa.Column("agent_key", sa.String(), nullable=False),
        sa.Column("agent_version", sa.String(), nullable=False),
        sa.Column("instructions_hash", sa.String(), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("output", sa.JSON(), nullable=True),
        sa.Column("error_kind", sa.String(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("step_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_cost_micros", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        _ts("started_at"), _ts("finished_at"), _now(),
        sa.ForeignKeyConstraint(["task_id", "organization_id"], ["agent_tasks.id", "agent_tasks.organization_id"],
                                name="fk_agent_runs_task_org", ondelete="CASCADE"),
        sa.UniqueConstraint("id", "organization_id", name="uq_agent_runs_id_org"),
        sa.UniqueConstraint("task_id", "sequence", name="uq_agent_runs_task_sequence"),
        sa.CheckConstraint("status IN ('pending','running','completed','failed','cancelled')", name="ck_agent_runs_status"),
    )
    op.create_index("ix_agent_runs_org_task", "agent_runs", ["organization_id", "task_id"])

    op.create_table(
        "agent_steps",
        sa.Column("id", sa.String(), primary_key=True), _org_col(),
        sa.Column("task_id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="ok"),
        sa.Column("error_kind", sa.String(), nullable=True),
        sa.Column("input", sa.JSON(), nullable=True),
        sa.Column("output", sa.JSON(), nullable=True),
        sa.Column("injection_suspected", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("gateway_request_id", sa.String(), nullable=True),
        sa.Column("provider", sa.String(), nullable=True),
        sa.Column("model_id", sa.String(), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cost_micros", sa.BigInteger(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        _now(),
        sa.ForeignKeyConstraint(["task_id", "organization_id"], ["agent_tasks.id", "agent_tasks.organization_id"],
                                name="fk_agent_steps_task_org", ondelete="CASCADE"),
        sa.UniqueConstraint("task_id", "sequence", name="uq_agent_steps_task_sequence"),
        sa.CheckConstraint("status IN ('ok','error')", name="ck_agent_steps_status"),
    )
    op.create_index("ix_agent_steps_org_task", "agent_steps", ["organization_id", "task_id"])

    op.create_table(
        "agent_settings",
        sa.Column("id", sa.String(), primary_key=True), _org_col(),
        sa.Column("agent_key", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("max_steps", sa.Integer(), nullable=True),
        sa.Column("timeout_seconds", sa.Integer(), nullable=True),
        sa.Column("updated_by_user_id", sa.String(), nullable=True),
        _now("updated_at"),
        sa.UniqueConstraint("organization_id", "agent_key", name="uq_agent_settings_org_agent"),
        sa.CheckConstraint("max_steps IS NULL OR max_steps > 0", name="ck_agent_settings_steps"),
        sa.CheckConstraint("timeout_seconds IS NULL OR timeout_seconds > 0", name="ck_agent_settings_timeout"),
    )
    op.create_index("ix_agent_settings_organization_id", "agent_settings", ["organization_id"])

    for table in TABLES:
        op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"CREATE POLICY tenant_isolation ON {table} USING ({POLICY}) WITH CHECK ({POLICY})"))
    # Only the signed platform-admin context (the recovery job) can see/requeue tasks across organizations.
    op.execute(sa.text(
        "CREATE POLICY platform_recovery ON agent_tasks FOR ALL "
        "USING (app_is_platform_admin()) WITH CHECK (app_is_platform_admin())"
    ))

    role = _role()
    if role:
        op.execute(sa.text(f'REVOKE UPDATE, DELETE, TRUNCATE ON agent_steps FROM "{role}"'))


def downgrade() -> None:
    op.execute(sa.text("DROP POLICY IF EXISTS platform_recovery ON agent_tasks"))
    for table in ("agent_settings", "agent_steps", "agent_runs", "agent_tasks"):
        op.drop_table(table)
