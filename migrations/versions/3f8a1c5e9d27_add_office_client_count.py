"""Add the optional number of client companies an office supports."""

import sqlalchemy as sa
from alembic import op

revision = "3f8a1c5e9d27"
down_revision = "7c2e9b41d6a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing offices stay empty ("not entered").
    with op.batch_alter_table("offices") as batch:
        batch.add_column(sa.Column("client_count", sa.Integer(), nullable=True))
        batch.create_check_constraint(
            "office_client_count_range", "client_count BETWEEN 0 AND 100000"
        )


def downgrade() -> None:
    with op.batch_alter_table("offices") as batch:
        batch.drop_constraint("office_client_count_range", type_="check")
        batch.drop_column("client_count")
