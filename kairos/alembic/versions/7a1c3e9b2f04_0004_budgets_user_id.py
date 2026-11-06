"""0004 budgets user_id

Revision ID: 7a1c3e9b2f04
Revises: 062582ffaf4d
Create Date: 2026-11-01
"""
from alembic import op
import sqlalchemy as sa


revision = '7a1c3e9b2f04'
down_revision = '062582ffaf4d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('budgets', schema=None) as batch_op:
        batch_op.add_column(sa.Column('user_id', sa.Text(),
                                      nullable=False, server_default='usr_local'))
        batch_op.create_index('ix_budgets_user_id', ['user_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('budgets', schema=None) as batch_op:
        batch_op.drop_index('ix_budgets_user_id')
        batch_op.drop_column('user_id')
