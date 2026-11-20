"""Require human-reviewed translation and record local vocabulary."""

import sqlalchemy as sa
from alembic import op

revision = "aa61a8d7203e"
down_revision = "31d45ed580b1"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("talents") as batch:
        batch.add_column(sa.Column("translation_verified_at", sa.DateTime()))
        batch.add_column(sa.Column("translation_verified_by", sa.Integer()))
        batch.create_foreign_key(
            "fk_translation_reviewer",
            "users",
            ["translation_verified_by"],
            ["id"],
            ondelete="SET NULL",
        )
    op.add_column("visits", sa.Column("learned_term", sa.String(100)))
    # Only restore the fictional exercise results used in the published demo.
    conn = op.get_bind()
    reviewer = conn.execute(
        sa.text(
            "SELECT id FROM users WHERE email=:email AND is_demo AND role='admin' AND NOT is_active"
        ),
        {"email": "recorder@tsunagu.example"},
    ).scalar()
    if reviewer is not None:
        for index in (1, 2, 3, 5, 6, 7, 8):
            conn.execute(
                sa.text("""UPDATE talents SET translation_verified_at=CURRENT_TIMESTAMP, translation_verified_by=:reviewer
                WHERE user_id IN (SELECT id FROM users WHERE email=:email AND display_name=:name AND is_demo AND role='tsunagite')"""),
                {
                    "reviewer": reviewer,
                    "email": f"talent{index}@tsunagu.example",
                    "name": f"架空つなぎ手 {index}",
                },
            )


def downgrade():
    with op.batch_alter_table("visits") as batch:
        batch.drop_column("learned_term")
    with op.batch_alter_table("talents") as batch:
        batch.drop_constraint("fk_translation_reviewer", type_="foreignkey")
        batch.drop_column("translation_verified_by")
        batch.drop_column("translation_verified_at")
