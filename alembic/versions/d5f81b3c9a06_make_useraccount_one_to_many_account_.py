"""make UserAccount one-to-many, account_number unique per bank

Revision ID: d5f81b3c9a06
Revises: c9e2b6a41f70
Create Date: 2026-09-30 17:04:58.394837

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5f81b3c9a06'
down_revision: Union[str, Sequence[str], None] = 'c9e2b6a41f70'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # A client may now link more than one bank account: drop the one-per-user
    # constraint. account_number alone isn't unique across banks - the same
    # number could coincidentally appear at two different banks - so the
    # replacement is unique per (bank_name, account_number) together.
    op.drop_constraint(op.f('UserAccount_user_id_key'), 'UserAccount', type_='unique')
    op.create_unique_constraint(
        'uq_UserAccount_bank_account_number', 'UserAccount', ['bank_name', 'account_number']
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('uq_UserAccount_bank_account_number', 'UserAccount', type_='unique')
    op.create_unique_constraint(op.f('UserAccount_user_id_key'), 'UserAccount', ['user_id'])
