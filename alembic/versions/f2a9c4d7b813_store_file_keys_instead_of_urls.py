"""store object keys instead of public urls for uploaded files

Revision ID: f2a9c4d7b813
Revises: e5b3a70d8c14
Create Date: 2026-09-26 15:30:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'f2a9c4d7b813'
down_revision: Union[str, Sequence[str], None] = 'e5b3a70d8c14'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Alembic can't detect a rename (it would drop and re-add), so both are explicit.
    op.alter_column('MoreAboutUsers', 'document_link', new_column_name='document_key')
    op.alter_column('MoreAboutUsers', 'profile_picture', new_column_name='profile_picture_key')

    # Rows saved before this change hold a full public URL; keep only the object
    # key, which always starts at "users/". Rows that already hold a key are left alone.
    for column in ('document_key', 'profile_picture_key'):
        op.execute(
            f"""UPDATE "MoreAboutUsers"
                SET {column} = regexp_replace({column}, '^https?://.*?(users/.+)$', '\\1')
                WHERE {column} LIKE 'http%'"""
        )


def downgrade() -> None:
    """Downgrade schema."""
    # The stored values stay keys (the old public base URL isn't kept anywhere).
    op.alter_column('MoreAboutUsers', 'profile_picture_key', new_column_name='profile_picture')
    op.alter_column('MoreAboutUsers', 'document_key', new_column_name='document_link')
