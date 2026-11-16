"""SMS delivery tracking on NotificationsLogs

Revision ID: a3d7f5c2e914
Revises: f3c8a2e67b19
Create Date: 2026-11-15 10:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a3d7f5c2e914'
down_revision: Union[str, Sequence[str], None] = 'f3c8a2e67b19'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE notificationstatus ADD VALUE IF NOT EXISTS 'DELIVERED'")
    op.add_column('NotificationsLogs', sa.Column('provider', sa.String(), nullable=True))
    op.add_column('NotificationsLogs', sa.Column('provider_message_id', sa.String(), nullable=True))
    op.add_column('NotificationsLogs', sa.Column('cost', sa.String(), nullable=True))
    op.add_column('NotificationsLogs', sa.Column('failure_reason', sa.String(), nullable=True))
    op.add_column('NotificationsLogs', sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        op.f('ix_NotificationsLogs_provider_message_id'), 'NotificationsLogs', ['provider_message_id'], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_NotificationsLogs_provider_message_id'), table_name='NotificationsLogs')
    op.drop_column('NotificationsLogs', 'delivered_at')
    op.drop_column('NotificationsLogs', 'failure_reason')
    op.drop_column('NotificationsLogs', 'cost')
    op.drop_column('NotificationsLogs', 'provider_message_id')
    op.drop_column('NotificationsLogs', 'provider')
    # DELIVERED stays in the notificationstatus type - Postgres can't drop an
    # enum value without rebuilding the type (see d9f21a6c4e83).
