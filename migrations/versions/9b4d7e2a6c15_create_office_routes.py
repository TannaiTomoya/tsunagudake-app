"""Create the routes each office has confirmed by itself."""

import sqlalchemy as sa
from alembic import op

revision = "9b4d7e2a6c15"
down_revision = "3f8a1c5e9d27"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "office_routes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("office_id", sa.Integer(), nullable=False),
        sa.Column("from_municipality_id", sa.Integer(), nullable=False),
        sa.Column("minutes", sa.Integer(), nullable=False),
        sa.Column("confirmed_by", sa.Integer(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "minutes BETWEEN 1 AND 300", name="office_route_minutes_range"
        ),
        sa.ForeignKeyConstraint(["confirmed_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["from_municipality_id"], ["municipalities.id"]),
        sa.ForeignKeyConstraint(["office_id"], ["offices.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("office_id", "from_municipality_id"),
    )


def downgrade() -> None:
    op.drop_table("office_routes")
