"""Add the one-line improvement idea to visits, and the office's "try it" mark."""

import sqlalchemy as sa
from alembic import op

revision = "8529c341b6df"
down_revision = "fa359b20c743"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("visits", schema=None) as batch_op:
        batch_op.add_column(sa.Column("idea", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("idea_adopted_by", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("idea_adopted_at", sa.DateTime(), nullable=True))
        batch_op.create_foreign_key(
            "fk_visits_idea_adopted_by_users", "users", ["idea_adopted_by"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("visits", schema=None) as batch_op:
        batch_op.drop_constraint("fk_visits_idea_adopted_by_users", type_="foreignkey")
        batch_op.drop_column("idea_adopted_at")
        batch_op.drop_column("idea_adopted_by")
        batch_op.drop_column("idea")
