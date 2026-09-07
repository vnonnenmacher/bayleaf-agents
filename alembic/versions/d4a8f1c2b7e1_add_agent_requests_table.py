"""add agent requests table

Revision ID: d4a8f1c2b7e1
Revises: c9e4a7b1d3f2
Create Date: 2026-09-07 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "d4a8f1c2b7e1"
down_revision = "c9e4a7b1d3f2"
branch_labels = None
depends_on = None


agent_request_state = postgresql.ENUM(
    "waiting",
    "processing",
    "succeeded",
    "failed",
    "cancelled",
    name="agentrequeststate",
    create_type=False,
)


def upgrade() -> None:
    agent_request_state.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "agent_requests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=100), nullable=False),
        sa.Column("agent_slug", sa.String(length=100), nullable=False),
        sa.Column("channel", sa.String(length=40), nullable=False),
        sa.Column("state", agent_request_state, nullable=False),
        sa.Column("user_message", sa.Text(), nullable=False),
        sa.Column("lang", sa.String(length=20), nullable=False),
        sa.Column("group_context", sa.JSON(), nullable=True),
        sa.Column("forced_document_ids", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("celery_task_id", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_agent_requests_conversation_id"), "agent_requests", ["conversation_id"], unique=False)
    op.create_index(op.f("ix_agent_requests_user_id"), "agent_requests", ["user_id"], unique=False)
    op.create_index(op.f("ix_agent_requests_agent_slug"), "agent_requests", ["agent_slug"], unique=False)
    op.create_index(op.f("ix_agent_requests_channel"), "agent_requests", ["channel"], unique=False)
    op.create_index(op.f("ix_agent_requests_state"), "agent_requests", ["state"], unique=False)

    op.add_column("messages", sa.Column("agent_request_id", sa.String(length=36), nullable=True))
    op.create_index(op.f("ix_messages_agent_request_id"), "messages", ["agent_request_id"], unique=False)
    op.create_foreign_key(
        "fk_messages_agent_request_id",
        "messages",
        "agent_requests",
        ["agent_request_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_messages_agent_request_id", "messages", type_="foreignkey")
    op.drop_index(op.f("ix_messages_agent_request_id"), table_name="messages")
    op.drop_column("messages", "agent_request_id")

    op.drop_index(op.f("ix_agent_requests_state"), table_name="agent_requests")
    op.drop_index(op.f("ix_agent_requests_channel"), table_name="agent_requests")
    op.drop_index(op.f("ix_agent_requests_agent_slug"), table_name="agent_requests")
    op.drop_index(op.f("ix_agent_requests_user_id"), table_name="agent_requests")
    op.drop_index(op.f("ix_agent_requests_conversation_id"), table_name="agent_requests")
    op.drop_table("agent_requests")

    agent_request_state.drop(op.get_bind(), checkfirst=True)
