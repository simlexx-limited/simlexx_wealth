"""add review_comment to MoreAboutUsers

Revision ID: e5b3a70d8c14
Revises: c81f5d2a9e37
Create Date: 2026-09-21 23:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5b3a70d8c14'
down_revision: Union[str, Sequence[str], None] = 'c81f5d2a9e37'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('MoreAboutUsers', sa.Column('review_comment', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('MoreAboutUsers', 'review_comment')
