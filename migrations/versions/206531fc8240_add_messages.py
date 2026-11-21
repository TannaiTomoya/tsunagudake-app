"""Add per-engagement messages between the office and the assigned talent."""

import sqlalchemy as sa
from alembic import op

revision = "206531fc8240"
down_revision = "8529c341b6df"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("engagement_id", sa.Integer(), nullable=False),
        sa.Column("sender_user_id", sa.Integer(), nullable=False),
        sa.Column("from_office", sa.Boolean(), nullable=False),
        sa.Column("template", sa.String(length=20), nullable=True),
        sa.Column("body", sa.String(length=400), nullable=False),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["engagement_id"], ["engagements.id"]),
        sa.ForeignKeyConstraint(["sender_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_messages_engagement_id_id", "messages", ["engagement_id", "id"]
    )


def downgrade() -> None:
    # 索引は、表と一緒に消える（MySQL では、外部キーが使う索引を先に消せない）。
    op.drop_table("messages")
