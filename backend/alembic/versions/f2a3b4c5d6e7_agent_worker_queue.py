"""Agent worker queue: retry scheduling and worker attribution on agent_tasks (etapa 02.4)

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-11-29

PostgreSQL stays the persistent queue (no Redis). Two nullable columns and one partial index:

* ``available_at``: a pending task is not claimable before this instant (controlled retry back-off).
* ``worker_id``: which worker process holds / last held the task (diagnostics only; fencing stays on ``lease_owner``).
* ``ix_agent_tasks_claimable``: serves the worker's "oldest claimable pending task" query.

No new table, no new policy: RLS ``tenant_isolation`` and the platform-only ``platform_recovery`` policy of
``agent_tasks`` already cover the new columns. Additive and reversible.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f2a3b4c5d6e7"
down_revision: Union[str, Sequence[str], None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("agent_tasks", sa.Column("available_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("agent_tasks", sa.Column("worker_id", sa.String(), nullable=True))
    op.create_index("ix_agent_tasks_claimable", "agent_tasks", ["available_at", "created_at"],
                    postgresql_where=sa.text("status = 'pending'"))


def downgrade() -> None:
    op.drop_index("ix_agent_tasks_claimable", table_name="agent_tasks")
    op.drop_column("agent_tasks", "worker_id")
    op.drop_column("agent_tasks", "available_at")
