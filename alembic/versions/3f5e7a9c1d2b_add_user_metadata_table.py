"""add user metadata table

Revision ID: 3f5e7a9c1d2b
Revises: b7c4d1e9f2aa
Create Date: 2026-03-06 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "3f5e7a9c1d2b"
down_revision = "b7c4d1e9f2aa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_metadata",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=100), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", name="uq_user_metadata_owner"),
    )
    op.create_index(op.f("ix_user_metadata_owner_id"), "user_metadata", ["owner_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_user_metadata_owner_id"), table_name="user_metadata")
    op.drop_table("user_metadata")
