"""drop client_id from user_metadata

Revision ID: 7c4a2d9e1b6f
Revises: 3f5e7a9c1d2b
Create Date: 2026-03-07 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "7c4a2d9e1b6f"
down_revision = "3f5e7a9c1d2b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("user_metadata")}

    if "client_id" in columns:
        # Keep the most recently updated row per owner_id before making owner_id unique.
        op.execute(
            """
            DELETE FROM user_metadata
            WHERE ctid IN (
                SELECT ctid
                FROM (
                    SELECT
                        ctid,
                        ROW_NUMBER() OVER (
                            PARTITION BY owner_id
                            ORDER BY updated_at DESC, created_at DESC, id DESC
                        ) AS rn
                    FROM user_metadata
                ) ranked
                WHERE ranked.rn > 1
            )
            """
        )

        op.drop_index(op.f("ix_user_metadata_client_id"), table_name="user_metadata")
        op.drop_constraint("uq_user_metadata_owner_client", "user_metadata", type_="unique")
        op.drop_column("user_metadata", "client_id")

    existing_uniques = {u["name"] for u in inspector.get_unique_constraints("user_metadata")}
    if "uq_user_metadata_owner" not in existing_uniques:
        op.create_unique_constraint("uq_user_metadata_owner", "user_metadata", ["owner_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("user_metadata")}
    existing_uniques = {u["name"] for u in inspector.get_unique_constraints("user_metadata")}

    if "uq_user_metadata_owner" in existing_uniques:
        op.drop_constraint("uq_user_metadata_owner", "user_metadata", type_="unique")

    if "client_id" not in columns:
        op.add_column(
            "user_metadata",
            sa.Column("client_id", sa.String(length=100), nullable=False, server_default="default"),
        )
        op.alter_column("user_metadata", "client_id", server_default=None)
        op.create_index(op.f("ix_user_metadata_client_id"), "user_metadata", ["client_id"], unique=False)

    refreshed_uniques = {u["name"] for u in inspector.get_unique_constraints("user_metadata")}
    if "uq_user_metadata_owner_client" not in refreshed_uniques:
        op.create_unique_constraint(
            "uq_user_metadata_owner_client",
            "user_metadata",
            ["owner_id", "client_id"],
        )
