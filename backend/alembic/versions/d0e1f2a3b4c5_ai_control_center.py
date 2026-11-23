"""AI control center: cached-token price and recoverable budget reservations (etapa 02.2)

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-11-27

* ``ai_models.cached_input_price_micros_per_mtok``: optional price for prompt tokens served
  from a provider cache. NULL = the provider/organization did not inform one, so cached
  tokens keep being charged at the normal input price (never under-counted).
* ``ai_budget_reservations``: one row per ACTIVE reservation, written in the same transaction
  as the counter update. A process that dies between "reserve" and "settle" used to leave its
  amount in ``ai_budgets.reserved_micros`` until the window rolled. Now the row lets a later
  request recognise it as abandoned and convert it to spent (never release it: the provider may
  have answered and billed), so the limit can never be exceeded by a crash.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d0e1f2a3b4c5"
down_revision: Union[str, Sequence[str], None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

POLICY = "organization_id = (SELECT app_current_org())"


def upgrade() -> None:
    op.add_column("ai_models", sa.Column("cached_input_price_micros_per_mtok", sa.BigInteger(), nullable=True))
    op.create_check_constraint(
        "ck_ai_models_cached_price", "ai_models",
        "cached_input_price_micros_per_mtok IS NULL OR cached_input_price_micros_per_mtok >= 0")

    op.create_table(
        "ai_budget_reservations",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("period", sa.String(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("amount_micros", sa.BigInteger(), nullable=False),
        sa.Column("request_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("period IN ('daily','monthly')", name="ck_ai_reservations_period"),
        sa.CheckConstraint("amount_micros >= 0", name="ck_ai_reservations_amount"),
    )
    op.create_index("ix_ai_budget_reservations_org_created", "ai_budget_reservations", ["organization_id", "created_at"])
    op.execute(sa.text("ALTER TABLE ai_budget_reservations ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE ai_budget_reservations FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"CREATE POLICY tenant_isolation ON ai_budget_reservations USING ({POLICY}) WITH CHECK ({POLICY})"))


def downgrade() -> None:
    op.drop_table("ai_budget_reservations")
    op.drop_constraint("ck_ai_models_cached_price", "ai_models", type_="check")
    op.drop_column("ai_models", "cached_input_price_micros_per_mtok")
