"""Record bank transfer reports and receipts separately from confirmed fees."""

from alembic import op
import sqlalchemy as sa

revision = "31d45ed580b1"
down_revision = "d7559c7e73d9"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("statements", sa.Column("transfer_reported_at", sa.DateTime(), nullable=True))
    op.add_column("statements", sa.Column("receipt_confirmed_at", sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table("statements") as batch:
        batch.drop_column("receipt_confirmed_at")
        batch.drop_column("transfer_reported_at")
