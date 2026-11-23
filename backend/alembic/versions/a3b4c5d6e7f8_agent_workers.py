"""Agent worker liveness (etapa 02.6)

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-11-30

One small, NON-tenant table: every agent worker upserts its own row every few seconds (and marks it ``stopped`` on a graceful
shutdown). The API only reads aggregate facts from it ("is any worker alive?"), so the UI can tell "no worker is running" apart
from "the queue is busy" instead of guessing. It holds no tenant data, no task content and no secret: worker id, timestamps,
how many tasks it is running and its concurrency. Additive and reversible.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a3b4c5d6e7f8"
down_revision: Union[str, Sequence[str], None] = "f2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_workers",
        sa.Column("worker_id", sa.String(), primary_key=True),
        sa.Column("status", sa.String(), nullable=False, server_default="running"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("active_tasks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("concurrency", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint("status IN ('running','stopped')", name="ck_agent_workers_status"),
    )
    op.create_index("ix_agent_workers_last_seen", "agent_workers", ["last_seen_at"])


def downgrade() -> None:
    op.drop_index("ix_agent_workers_last_seen", table_name="agent_workers")
    op.drop_table("agent_workers")
