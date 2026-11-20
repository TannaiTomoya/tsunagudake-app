"""Store only the counts from the past-period comparison tool."""

import sqlalchemy as sa
from alembic import op

revision = "fa359b20c743"
down_revision = "2ee05ca894bc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "check_summaries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("engagement_id", sa.Integer(), nullable=False),
        sa.Column("month", sa.String(length=7), nullable=False),
        sa.Column("compared", sa.Integer(), nullable=False),
        sa.Column("flagged", sa.Integer(), nullable=False),
        sa.Column("uncomparable", sa.Integer(), nullable=False),
        sa.Column("recorded_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "compared BETWEEN 0 AND 9999 AND flagged BETWEEN 0 AND compared "
            "AND uncomparable BETWEEN 0 AND 9999 AND compared + uncomparable >= 1",
            name="check_summary_counts",
        ),
        sa.ForeignKeyConstraint(["engagement_id"], ["engagements.id"]),
        sa.ForeignKeyConstraint(["recorded_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_check_summaries_engagement_month",
        "check_summaries",
        ["engagement_id", "month"],
    )


def downgrade() -> None:
    op.drop_index("ix_check_summaries_engagement_month", table_name="check_summaries")
    op.drop_table("check_summaries")
