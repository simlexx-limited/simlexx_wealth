"""add GoalRecommendations table

Revision ID: c9e2b6a41f70
Revises: b7d4f813a2c6
Create Date: 2026-09-27 18:33:22.017544

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c9e2b6a41f70'
down_revision: Union[str, Sequence[str], None] = 'b7d4f813a2c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CURRENCY_VALUES = (
    'DZD', 'AOA', 'XOF', 'BWP', 'BIF', 'CVE', 'XAF', 'KMF', 'CDF', 'DJF', 'EGP',
    'ERN', 'SZL', 'ETB', 'GMD', 'GHS', 'GNF', 'KES', 'LSL', 'LRD', 'LYD', 'MGA',
    'MWK', 'MRU', 'MUR', 'MAD', 'MZN', 'NAD', 'NGN', 'RWF', 'STN', 'SCR', 'SLE',
    'SOS', 'ZAR', 'SSP', 'SDG', 'TZS', 'TND', 'UGX', 'ZMW', 'ZWG', 'EUR', 'CAD', 'USD',
)


def upgrade() -> None:
    """Upgrade schema."""
    # `create_table` (unlike a standalone `add_column`) does create a native
    # enum's type on its own - but currencytype already exists (Products,
    # UserAccount, UserGoals all use it), so this column must reuse it rather
    # than try to create it again. savingfrequency is brand new here, so it's
    # left to create itself as usual.
    op.create_table('GoalRecommendations',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('user_id', sa.String(), nullable=False),
    sa.Column('goal_id', sa.String(), nullable=True),
    sa.Column('saving_amount_recommended', sa.Numeric(precision=30, scale=8), nullable=False),
    sa.Column('currency', postgresql.ENUM(*CURRENCY_VALUES, name='currencytype', create_type=False), nullable=False),
    sa.Column('frequency', sa.Enum('DAILY', 'WEEKLY', 'MONTHLY', name='savingfrequency'), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('alternatives', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['goal_id'], ['UserGoals.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['Users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('goal_id')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('GoalRecommendations')
    # dropping the table leaves its own native enum type behind; currencytype
    # is shared with other tables and must not be dropped here
    op.execute("DROP TYPE IF EXISTS savingfrequency")
