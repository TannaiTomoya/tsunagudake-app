"""Add the optional note on how the saved time was used."""

import sqlalchemy as sa
from alembic import op

revision = "bac8134d27c1"
down_revision = "e5fb482c595e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("statements", sa.Column("returned_to", sa.String(100), nullable=True))


def downgrade() -> None:
    op.drop_column("statements", "returned_to")
