"""Add the document collection board, keyed by client numbers only."""

import sqlalchemy as sa
from alembic import op

revision = "2ee05ca894bc"
down_revision = "94dcf484d7ae"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "collection_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("office_id", sa.Integer(), nullable=False),
        sa.Column("month", sa.String(length=7), nullable=False),
        sa.Column("client_no", sa.Integer(), nullable=False),
        sa.Column("is_ready", sa.Boolean(), nullable=False),
        sa.Column("received_count", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "client_no BETWEEN 1 AND 9999", name="collection_client_no_range"
        ),
        sa.CheckConstraint(
            "received_count BETWEEN 0 AND 9999", name="collection_received_range"
        ),
        sa.ForeignKeyConstraint(["office_id"], ["offices.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("office_id", "month", "client_no"),
    )
    with op.batch_alter_table("offices", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("collection_token_hash", sa.String(length=64), nullable=True)
        )
        batch_op.create_unique_constraint(
            "uq_offices_collection_token_hash", ["collection_token_hash"]
        )


def downgrade() -> None:
    with op.batch_alter_table("offices", schema=None) as batch_op:
        batch_op.drop_constraint("uq_offices_collection_token_hash", type_="unique")
        batch_op.drop_column("collection_token_hash")
    op.drop_table("collection_items")
