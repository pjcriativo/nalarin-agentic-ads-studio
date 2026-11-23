"""add organizations and organization members (multi-tenant foundation, 1/3)

Revision ID: d4a9c1e7b2f0
Revises: a1d2e3f4b5c6
Create Date: 2026-11-23

Creates the tenant tables and gives every EXISTING user a personal
organization (they become its owner). Only the membership is created here:
no business data is assigned to any organization by this migration -- see
revision e2b3c4d5e6f7 and docs/etapa-01-01-multitenant-core.md for how legacy
records are handled.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4a9c1e7b2f0"
down_revision: Union[str, Sequence[str], None] = "a1d2e3f4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_table(
        "organization_members",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("status", sa.String(), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "user_id", name="uq_org_members_org_user"),
        sa.CheckConstraint("role IN ('owner','admin','manager','analyst','viewer')", name="ck_org_members_role"),
        sa.CheckConstraint("status IN ('active','revoked')", name="ck_org_members_status"),
    )
    op.create_index("ix_org_members_user_id", "organization_members", ["user_id"])

    # Every existing user keeps working: a personal organization, owned by them.
    op.execute(sa.text("""
        WITH new_orgs AS (
            INSERT INTO organizations (id, name, slug, is_active)
            SELECT gen_random_uuid()::text,
                   COALESCE(NULLIF(u.name, ''), u.email) || ' (personal)',
                   'personal-' || substr(replace(u.id, '-', ''), 1, 12),
                   true
            FROM users u
            WHERE NOT EXISTS (SELECT 1 FROM organization_members m WHERE m.user_id = u.id)
            RETURNING id, slug
        )
        INSERT INTO organization_members (id, organization_id, user_id, role, status)
        SELECT gen_random_uuid()::text, o.id, u.id, 'owner', 'active'
        FROM new_orgs o
        JOIN users u ON o.slug = 'personal-' || substr(replace(u.id, '-', ''), 1, 12)
    """))


def downgrade() -> None:
    op.drop_index("ix_org_members_user_id", table_name="organization_members")
    op.drop_table("organization_members")
    op.drop_table("organizations")
