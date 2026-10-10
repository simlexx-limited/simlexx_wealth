"""add deposit and withdraw to otppurpose

Revision ID: d9f21a6c4e83
Revises: c4a7e9b2d16f
Create Date: 2026-10-09 09:00:00.000000
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'd9f21a6c4e83'
down_revision: Union[str, Sequence[str], None] = 'c4a7e9b2d16f'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE otppurpose ADD VALUE IF NOT EXISTS 'DEPOSIT'")
    op.execute("ALTER TYPE otppurpose ADD VALUE IF NOT EXISTS 'WITHDRAW'")


def downgrade() -> None:
    # Postgres has no DROP VALUE for a native enum - removing one safely
    # means rebuilding the type and rewriting every column that uses it, and
    # only if no row still has that value. Not attempted here.
    pass
