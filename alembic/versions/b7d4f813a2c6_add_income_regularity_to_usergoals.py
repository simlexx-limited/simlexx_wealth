"""add income_regularity to UserGoals

Revision ID: b7d4f813a2c6
Revises: a1c6e2f9d047
Create Date: 2026-09-27 18:23:50.193873

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b7d4f813a2c6'
down_revision: Union[str, Sequence[str], None] = 'a1c6e2f9d047'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


INCOME_FREQUENCY_VALUES = ('DAILY', 'WEEKLY', 'MONTHLY', 'VARIES')


def upgrade() -> None:
    """Upgrade schema."""
    # Autogenerate doesn't emit CREATE TYPE for a native enum, even a brand new
    # one, so it's explicit here (same as document_type's migration).
    income_frequency = postgresql.ENUM(*INCOME_FREQUENCY_VALUES, name='incomefrequency')
    income_frequency.create(op.get_bind(), checkfirst=True)

    op.add_column(
        'UserGoals',
        sa.Column('income_regularity', income_frequency, nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('UserGoals', 'income_regularity')
    postgresql.ENUM(name='incomefrequency').drop(op.get_bind(), checkfirst=True)
