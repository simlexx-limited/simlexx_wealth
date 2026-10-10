"""add requested_amount and requested_currency to transactions

Revision ID: e7b4c1a95d02
Revises: d9f21a6c4e83
Create Date: 2026-10-10 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'e7b4c1a95d02'
down_revision: Union[str, Sequence[str], None] = 'd9f21a6c4e83'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('transactions', sa.Column('requested_amount', sa.Numeric(precision=30, scale=8), nullable=False))
    op.add_column(
        'transactions',
        sa.Column('requested_currency', sa.Enum(name='currencytype', create_type=False), nullable=False),
    )


def downgrade() -> None:
    op.drop_column('transactions', 'requested_currency')
    op.drop_column('transactions', 'requested_amount')
