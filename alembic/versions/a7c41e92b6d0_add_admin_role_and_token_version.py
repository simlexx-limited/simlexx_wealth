"""add admin role and users.token_version

Revision ID: a7c41e92b6d0
Revises: d03dd18f13cc
Create Date: 2026-09-21 20:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7c41e92b6d0'
down_revision: Union[str, Sequence[str], None] = 'd03dd18f13cc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Autogenerate can't add a value to a native enum. The database stores the
    # enum *names* (CLIENT, AGENT, ...), so the new label is ADMIN. ADD VALUE
    # is not allowed inside a transaction block on every Postgres version, so
    # it runs in an autocommit block.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'ADMIN'")

    op.add_column(
        'Users',
        sa.Column('token_version', sa.Integer(), server_default='0', nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('Users', 'token_version')
    # Postgres can't drop a single value from an enum, so 'ADMIN' stays in the
    # userrole type. It is harmless once nothing uses it.
