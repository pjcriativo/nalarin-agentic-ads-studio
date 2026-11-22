"""refresh tokens stored as sha-256 hash

Revision ID: a1d2e3f4b5c6
Revises: c4e8a21b9f30
Create Date: 2026-09-03

Existing plaintext refresh tokens cannot be converted to hashes without the
raw values (which are only held by clients), so outstanding sessions are
invalidated — users simply log in again.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1d2e3f4b5c6"
down_revision: Union[str, Sequence[str], None] = "c4e8a21b9f30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Invalidate outstanding sessions first: their stored values are
    # plaintext tokens we cannot hash without the raw client-side values,
    # and leaving them as ''-defaulted rows would violate the new unique
    # index. Users simply log in again.
    bind = op.get_bind()
    insp = sa.inspect(bind)
    indexes = [i["name"] for i in insp.get_indexes("refresh_tokens")]
    columns = [c["name"] for c in insp.get_columns("refresh_tokens")]

    op.execute("DELETE FROM refresh_tokens")
    if "ix_refresh_tokens_token" in indexes:
        op.drop_index("ix_refresh_tokens_token", table_name="refresh_tokens")
    if "token" in columns:
        op.drop_column("refresh_tokens", "token")
    if "token_hash" not in columns:
        op.add_column("refresh_tokens", sa.Column("token_hash", sa.String(), nullable=False, server_default=""))
    if "ix_refresh_tokens_token_hash" not in indexes:
        op.create_index("ix_refresh_tokens_token_hash", "refresh_tokens", ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_refresh_tokens_token_hash", table_name="refresh_tokens")
    op.drop_column("refresh_tokens", "token_hash")
    op.add_column("refresh_tokens", sa.Column("token", sa.String(), nullable=False, server_default=""))
    op.create_index("ix_refresh_tokens_token", "refresh_tokens", ["token"], unique=True)
