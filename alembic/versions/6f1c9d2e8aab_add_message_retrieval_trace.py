"""add message retrieval trace

Revision ID: 6f1c9d2e8aab
Revises: 0bf7c2b9a749
Create Date: 2026-02-24 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "6f1c9d2e8aab"
down_revision = "0bf7c2b9a749"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("retrieval_trace", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "retrieval_trace")
