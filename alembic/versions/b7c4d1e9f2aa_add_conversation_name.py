"""add conversation name

Revision ID: b7c4d1e9f2aa
Revises: 1a2b3c4d5e6f
Create Date: 2026-03-04 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b7c4d1e9f2aa"
down_revision = "1a2b3c4d5e6f"
branch_labels = None
depends_on = None


DEFAULT_CONVERSATION_NAME = "New conversation"


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column(
            "name",
            sa.String(length=120),
            nullable=False,
            server_default=DEFAULT_CONVERSATION_NAME,
        ),
    )
    op.alter_column("conversations", "name", server_default=None)


def downgrade() -> None:
    op.drop_column("conversations", "name")
