"""make document_type a native enum

Revision ID: d03dd18f13cc
Revises: 3f100d665953
Create Date: 2026-09-21 17:49:20.008956

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd03dd18f13cc'
down_revision: Union[str, Sequence[str], None] = '3f100d665953'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DOCUMENT_TYPE_VALUES = ('NATIONAL_ID', 'PASSPORT', 'DRIVING_PERMIT')


def upgrade() -> None:
    """Upgrade schema."""
    # Autogenerate doesn't emit CREATE TYPE for native enums, and Postgres
    # can't implicitly cast VARCHAR to the new enum, so both are explicit.
    # upper() maps any existing lowercase strings ('passport') to enum names.
    document_type = postgresql.ENUM(*DOCUMENT_TYPE_VALUES, name='documenttype')
    document_type.create(op.get_bind(), checkfirst=True)

    op.alter_column('MoreAboutUsers', 'document_type',
               existing_type=sa.VARCHAR(),
               type_=sa.Enum(*DOCUMENT_TYPE_VALUES, name='documenttype'),
               existing_nullable=False,
               postgresql_using='upper(document_type)::documenttype')


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column('MoreAboutUsers', 'document_type',
               existing_type=sa.Enum(*DOCUMENT_TYPE_VALUES, name='documenttype'),
               type_=sa.VARCHAR(),
               existing_nullable=False,
               postgresql_using='document_type::text')

    postgresql.ENUM(name='documenttype').drop(op.get_bind(), checkfirst=True)
