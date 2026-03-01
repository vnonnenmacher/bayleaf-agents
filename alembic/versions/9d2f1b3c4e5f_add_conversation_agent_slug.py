"""add conversation agent_slug

Revision ID: 9d2f1b3c4e5f
Revises: 6f1c9d2e8aab
Create Date: 2026-03-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "9d2f1b3c4e5f"
down_revision = "6f1c9d2e8aab"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("agent_slug", sa.String(length=100), nullable=True))
    op.create_index(op.f("ix_conversations_agent_slug"), "conversations", ["agent_slug"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_conversations_agent_slug"), table_name="conversations")
    op.drop_column("conversations", "agent_slug")
