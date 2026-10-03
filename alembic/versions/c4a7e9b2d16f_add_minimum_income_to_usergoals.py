"""add minimum_income snapshot to UserGoals

Revision ID: c4a7e9b2d16f
Revises: b8e2d5f10a37
Create Date: 2026-10-03 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c4a7e9b2d16f'
down_revision: Union[str, Sequence[str], None] = 'b8e2d5f10a37'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('UserGoals', sa.Column('minimum_income', sa.Numeric(precision=30, scale=8), nullable=False))


def downgrade() -> None:
    op.drop_column('UserGoals', 'minimum_income')
