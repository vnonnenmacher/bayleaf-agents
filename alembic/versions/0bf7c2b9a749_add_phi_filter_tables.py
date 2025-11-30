"""add phi filter tables

Revision ID: 0bf7c2b9a749
Revises: 2c14325cf5e4
Create Date: 2024-10-05 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0bf7c2b9a749'
down_revision = '2c14325cf5e4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('messages', sa.Column('redacted_content', sa.Text(), nullable=True))
    op.create_table(
        'phi_entities',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('conversation_id', sa.String(length=36), nullable=False),
        sa.Column('message_id', sa.String(length=36), nullable=False),
        sa.Column('entity_type', sa.String(length=80), nullable=False),
        sa.Column('placeholder', sa.String(length=80), nullable=False),
        sa.Column('original_text', sa.Text(), nullable=False),
        sa.Column('start', sa.Integer(), nullable=True),
        sa.Column('end', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id']),
        sa.ForeignKeyConstraint(['message_id'], ['messages.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_phi_entities_conversation_id'), 'phi_entities', ['conversation_id'], unique=False)
    op.create_index(op.f('ix_phi_entities_message_id'), 'phi_entities', ['message_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_phi_entities_message_id'), table_name='phi_entities')
    op.drop_index(op.f('ix_phi_entities_conversation_id'), table_name='phi_entities')
    op.drop_table('phi_entities')
    op.drop_column('messages', 'redacted_content')
