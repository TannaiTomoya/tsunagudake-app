"""Add arrival check-ins (no coordinates), the next visit day, the office location."""

import sqlalchemy as sa
from alembic import op

revision = "0740adfbf00e"
down_revision = "206531fc8240"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The check-in keeps only "pressed nearby" and the time. No latitude/longitude.
    op.create_table(
        "checkins",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("engagement_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("heading_at", sa.DateTime(), nullable=True),
        sa.Column("eta_at", sa.DateTime(), nullable=True),
        sa.Column("arrived_at", sa.DateTime(), nullable=True),
        sa.Column("near", sa.Boolean(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["engagement_id"], ["engagements.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "engagement_id", "day", name="uq_checkins_engagement_day"
        ),
    )
    with op.batch_alter_table("engagements", schema=None) as batch_op:
        batch_op.add_column(sa.Column("next_visit_on", sa.Date(), nullable=True))
    with op.batch_alter_table("offices", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("address", sa.String(length=200), nullable=True)
        )
        batch_op.add_column(sa.Column("lat_e6", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("lon_e6", sa.Integer(), nullable=True))
        batch_op.create_check_constraint(
            "office_lat_range", "lat_e6 BETWEEN -90000000 AND 90000000"
        )
        batch_op.create_check_constraint(
            "office_lon_range", "lon_e6 BETWEEN -180000000 AND 180000000"
        )


def downgrade() -> None:
    with op.batch_alter_table("offices", schema=None) as batch_op:
        batch_op.drop_constraint("office_lon_range", type_="check")
        batch_op.drop_constraint("office_lat_range", type_="check")
        batch_op.drop_column("lon_e6")
        batch_op.drop_column("lat_e6")
        batch_op.drop_column("address")
    with op.batch_alter_table("engagements", schema=None) as batch_op:
        batch_op.drop_column("next_visit_on")
    op.drop_table("checkins")
