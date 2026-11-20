"""Add the optional choice of where the saved time went."""

import sqlalchemy as sa
from alembic import op

revision = "7c2e9b41d6a3"
down_revision = "615aac89e122"
branch_labels = None
depends_on = None

kind = sa.Enum(
    "client_consult",
    "new_client",
    "left_early",
    "other",
    name="returnedkind",
    native_enum=False,
    create_constraint=True,
    length=20,
)


def upgrade() -> None:
    # Existing rows stay empty. Nothing is backfilled.
    with op.batch_alter_table("statements") as batch:
        batch.add_column(sa.Column("returned_kind", kind, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("statements") as batch:
        batch.drop_constraint("returnedkind", type_="check")
        batch.drop_column("returned_kind")
