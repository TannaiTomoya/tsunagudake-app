"""Group operational mail per recipient and scheduled day."""
import sqlalchemy as sa
from alembic import op

revision = "a86dc297c5d1"
down_revision = "cea3707c7ce6"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("outbox_mails") as batch:
        batch.add_column(sa.Column("digest_key", sa.String(48)))
        batch.add_column(sa.Column("deliver_after", sa.DateTime()))
        batch.create_unique_constraint("uq_outbox_digest_key", ["digest_key"])


def downgrade():
    with op.batch_alter_table("outbox_mails") as batch:
        batch.drop_constraint("uq_outbox_digest_key", type_="unique")
        batch.drop_column("deliver_after")
        batch.drop_column("digest_key")
