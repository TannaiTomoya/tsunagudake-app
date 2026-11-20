"""Store enrollment and one-use MFA credentials outside signed client sessions."""
import sqlalchemy as sa
from alembic import op

revision = "56ae758d49a1"
down_revision = "a86dc297c5d1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("second_factors",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("secret_cipher", sa.String(255), nullable=False),
        sa.Column("enrollment_ticket", sa.String(48)),
        sa.Column("last_step", sa.Integer()),
        sa.Column("recovery_hashes", sa.JSON(), nullable=False),
    )


def downgrade():
    op.drop_table("second_factors")
