"""make MoreAboutUsers document/picture keys append-only arrays

Revision ID: f6a9c2e81d35
Revises: d5f81b3c9a06
Create Date: 2026-10-02 09:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'f6a9c2e81d35'
down_revision: Union[str, Sequence[str], None] = 'd5f81b3c9a06'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # An update used to overwrite document_key/profile_picture_key outright.
    # A reviewer needs to tell a genuine resubmission apart from the same file
    # sent twice, so every upload is now kept: the columns become arrays,
    # appended to on each update, never overwritten.
    op.add_column('MoreAboutUsers', sa.Column('document_keys', postgresql.ARRAY(sa.String()), nullable=True))
    op.add_column('MoreAboutUsers', sa.Column('profile_picture_keys', postgresql.ARRAY(sa.String()), nullable=True))

    # Wrap each existing single key into a one-element array - nothing is lost.
    op.execute('UPDATE "MoreAboutUsers" SET document_keys = ARRAY[document_key], '
               'profile_picture_keys = ARRAY[profile_picture_key]')

    op.alter_column('MoreAboutUsers', 'document_keys', nullable=False)
    op.alter_column('MoreAboutUsers', 'profile_picture_keys', nullable=False)
    op.drop_column('MoreAboutUsers', 'document_key')
    op.drop_column('MoreAboutUsers', 'profile_picture_key')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('MoreAboutUsers', sa.Column('document_key', sa.String(), nullable=True))
    op.add_column('MoreAboutUsers', sa.Column('profile_picture_key', sa.String(), nullable=True))

    # The most recent upload is what the single-key world considered "the" file.
    op.execute('UPDATE "MoreAboutUsers" SET document_key = document_keys[array_length(document_keys, 1)], '
               'profile_picture_key = profile_picture_keys[array_length(profile_picture_keys, 1)]')

    op.alter_column('MoreAboutUsers', 'document_key', nullable=False)
    op.alter_column('MoreAboutUsers', 'profile_picture_key', nullable=False)
    op.drop_column('MoreAboutUsers', 'document_keys')
    op.drop_column('MoreAboutUsers', 'profile_picture_keys')
