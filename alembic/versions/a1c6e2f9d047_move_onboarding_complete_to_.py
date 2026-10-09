"""move onboarding_complete from UserGoals to MoreAboutUsers

Revision ID: a1c6e2f9d047
Revises: f2a9c4d7b813
Create Date: 2026-09-27 15:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1c6e2f9d047'
down_revision: Union[str, Sequence[str], None] = 'f2a9c4d7b813'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # onboarding_complete describes the user, not any one goal - it moves to
    # MoreAboutUsers (alongside profile_verification_complete) instead of
    # living on a per-goal row.
    op.add_column(
        'MoreAboutUsers',
        sa.Column('onboarding_complete', sa.Boolean(), server_default='false', nullable=False),
    )
    op.drop_column('UserGoals', 'onboarding_complete')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        'UserGoals',
        sa.Column('onboarding_complete', sa.Boolean(), server_default='false', nullable=False),
    )
    op.drop_column('MoreAboutUsers', 'onboarding_complete')
