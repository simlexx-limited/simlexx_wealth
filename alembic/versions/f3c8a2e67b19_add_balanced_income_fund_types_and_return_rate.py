"""add balanced/income fund types to producttype and annual_return_rate to products

Revision ID: f3c8a2e67b19
Revises: e7b4c1a95d02
Create Date: 2026-10-21 09:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f3c8a2e67b19'
down_revision: Union[str, Sequence[str], None] = 'e7b4c1a95d02'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE producttype ADD VALUE IF NOT EXISTS 'BALANCED_FUND'")
    op.execute("ALTER TYPE producttype ADD VALUE IF NOT EXISTS 'INCOME_FUND'")
    op.add_column('products', sa.Column('annual_return_rate', sa.Numeric(precision=5, scale=4), nullable=True))


def downgrade() -> None:
    op.drop_column('products', 'annual_return_rate')
    # Postgres has no DROP VALUE for a native enum - removing one safely means
    # rebuilding the type and rewriting every column that uses it, and only if
    # no row still has that value. Not attempted here.
