"""add conversation groups

Revision ID: 1a2b3c4d5e6f
Revises: 9d2f1b3c4e5f
Create Date: 2026-03-03 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "1a2b3c4d5e6f"
down_revision = "9d2f1b3c4e5f"
branch_labels = None
depends_on = None


conversation_group_type = postgresql.ENUM("project", "event", name="conversationgrouptype", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    conversation_group_type.create(bind, checkfirst=True)

    op.create_table(
        "conversation_groups",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=100), nullable=False),
        sa.Column("type", conversation_group_type, nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("document_uuids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_conversation_groups_owner_id"), "conversation_groups", ["owner_id"], unique=False)
    op.create_index(op.f("ix_conversation_groups_type"), "conversation_groups", ["type"], unique=False)
    op.create_index(op.f("ix_conversation_groups_is_active"), "conversation_groups", ["is_active"], unique=False)

    op.add_column("conversations", sa.Column("group_id", sa.String(length=36), nullable=True))
    op.create_index(op.f("ix_conversations_group_id"), "conversations", ["group_id"], unique=False)
    op.create_foreign_key(
        "fk_conversations_group_id",
        "conversations",
        "conversation_groups",
        ["group_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_conversations_group_id", "conversations", type_="foreignkey")
    op.drop_index(op.f("ix_conversations_group_id"), table_name="conversations")
    op.drop_column("conversations", "group_id")

    op.drop_index(op.f("ix_conversation_groups_is_active"), table_name="conversation_groups")
    op.drop_index(op.f("ix_conversation_groups_type"), table_name="conversation_groups")
    op.drop_index(op.f("ix_conversation_groups_owner_id"), table_name="conversation_groups")
    op.drop_table("conversation_groups")

    bind = op.get_bind()
    conversation_group_type.drop(bind, checkfirst=True)
