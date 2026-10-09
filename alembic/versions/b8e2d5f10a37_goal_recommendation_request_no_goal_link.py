"""store the goal request on GoalRecommendations and drop the goal link

Revision ID: b8e2d5f10a37
Revises: f6a9c2e81d35
Create Date: 2026-10-03 09:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'b8e2d5f10a37'
down_revision: Union[str, Sequence[str], None] = 'f6a9c2e81d35'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('GoalRecommendations', sa.Column('request', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.execute("UPDATE \"GoalRecommendations\" SET request = '{}'::jsonb WHERE request IS NULL")
    op.alter_column('GoalRecommendations', 'request', nullable=False)
    op.drop_column('GoalRecommendations', 'goal_id')


def downgrade() -> None:
    op.add_column('GoalRecommendations', sa.Column('goal_id', sa.String(), nullable=True))
    op.create_foreign_key(
        'GoalRecommendations_goal_id_fkey', 'GoalRecommendations', 'UserGoals', ['goal_id'], ['id']
    )
    op.create_unique_constraint('GoalRecommendations_goal_id_key', 'GoalRecommendations', ['goal_id'])
    op.drop_column('GoalRecommendations', 'request')
