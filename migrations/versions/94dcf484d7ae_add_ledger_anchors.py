"""Keep a daily record of the last hash of every ledger chain."""

import sqlalchemy as sa
from alembic import op

revision = "94dcf484d7ae"
down_revision = "9b4d7e2a6c15"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ledger_anchors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("anchored_on", sa.Date(), nullable=False),
        sa.Column("chains", sa.Integer(), nullable=False),
        sa.Column("entries", sa.Integer(), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("problems", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "chains >= 0 AND entries >= chains AND problems >= 0",
            name="ledger_anchor_counts",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("anchored_on"),
    )


def downgrade() -> None:
    op.drop_table("ledger_anchors")
