from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("external_id", sa.String(length=100), nullable=True),
        sa.Column("patient_id", sa.String(length=100), nullable=False),
        sa.Column("channel", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_conv_external", "conversations", ["external_id"])
    op.create_index("ix_conv_patient", "conversations", ["patient_id"])
    op.create_index("ix_conv_channel", "conversations", ["channel"])

    op.create_table(
        "messages",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("conversation_id", sa.String(length=36), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("role", sa.Enum("system","user","assistant","tool", name="role"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tool_name", sa.String(length=100), nullable=True),
        sa.Column("tool_args", sa.JSON(), nullable=True),
        sa.Column("tool_result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_msgs_conv", "messages", ["conversation_id"])
    op.create_index("ix_msgs_created", "messages", ["created_at"])


def downgrade():
    op.drop_index("ix_msgs_created", table_name="messages")
    op.drop_index("ix_msgs_conv", table_name="messages")
    op.drop_table("messages")
    op.drop_index("ix_conv_channel", table_name="conversations")
    op.drop_index("ix_conv_patient", table_name="conversations")
    op.drop_index("ix_conv_external", table_name="conversations")
    op.drop_table("conversations")
