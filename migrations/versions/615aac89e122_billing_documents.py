"""Freeze offer and invoice details when they are issued."""
import sqlalchemy as sa
from alembic import op

revision = "615aac89e122"
down_revision = "56ae758d49a1"
branch_labels = None
depends_on = None

fields = {
    "match_candidates": {"estimate_hours": sa.Integer(), "estimate_hourly_rate": sa.Integer(), "estimate_total": sa.Integer()},
    "talents": {"payout_cipher": sa.Text(), "invoice_registration": sa.String(14)},
    "statements": {"payout_cipher": sa.Text(), "invoice_registration": sa.String(14), "due_on": sa.Date()},
}


def upgrade():
    for table, columns in fields.items():
        for name, kind in columns.items():
            op.add_column(table, sa.Column(name, kind))


def downgrade():
    for table, columns in fields.items():
        with op.batch_alter_table(table) as batch:
            for name in columns:
                batch.drop_column(name)
