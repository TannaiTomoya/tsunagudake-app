"""Add the record of calls to the external AI (no text is stored)."""

import sqlalchemy as sa
from alembic import op

revision = "7f5826cd77ed"
down_revision = "0740adfbf00e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=False),
        sa.Column("office_id", sa.Integer(), nullable=True),
        sa.Column(
            "purpose",
            sa.Enum(
                "translate",
                "draft",
                "question",
                "summary",
                name="aipurpose",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("driver", sa.String(length=10), nullable=False),
        sa.Column("chars", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column(
            "outcome",
            sa.Enum(
                "ok",
                "empty",
                "error",
                name="aioutcome",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("result", sa.String(length=100), nullable=True),
        sa.Column("need_id", sa.Integer(), nullable=True),
        sa.Column("input_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "chars >= 0 AND input_tokens >= 0 AND output_tokens >= 0",
            name="ai_run_counts",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], name="fk_ai_runs_actor_user_id"
        ),
        sa.ForeignKeyConstraint(["need_id"], ["needs.id"], name="fk_ai_runs_need_id"),
        sa.ForeignKeyConstraint(
            ["office_id"], ["offices.id"], name="fk_ai_runs_office_id"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_runs_created_at", "ai_runs", ["created_at"])
    op.create_index("ix_ai_runs_need_id_id", "ai_runs", ["need_id", "id"])


def downgrade() -> None:
    # 索引は、表と一緒に消える（MySQL では、外部キーが使う索引を先に消せない）。
    op.drop_table("ai_runs")
