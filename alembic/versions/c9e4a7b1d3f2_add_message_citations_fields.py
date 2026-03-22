"""add message citations fields

Revision ID: c9e4a7b1d3f2
Revises: 7c4a2d9e1b6f
Create Date: 2026-03-22 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c9e4a7b1d3f2"
down_revision = "7c4a2d9e1b6f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("cited_documents", sa.JSON(), nullable=True))
    op.add_column("messages", sa.Column("citations", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "citations")
    op.drop_column("messages", "cited_documents")
