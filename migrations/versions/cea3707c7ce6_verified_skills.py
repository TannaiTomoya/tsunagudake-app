"""Record who checked each claimed skill at a partner office."""

import sqlalchemy as sa
from alembic import op

revision = "cea3707c7ce6"
down_revision = "aa61a8d7203e"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("talent_skills") as batch:
        batch.add_column(sa.Column("verified_at", sa.DateTime()))
        batch.add_column(sa.Column("verified_by", sa.Integer()))
        batch.add_column(sa.Column("verified_office_id", sa.Integer()))
        batch.create_foreign_key(
            "fk_skill_reviewer", "users", ["verified_by"], ["id"], ondelete="SET NULL"
        )
        batch.create_foreign_key(
            "fk_skill_office",
            "offices",
            ["verified_office_id"],
            ["id"],
            ondelete="SET NULL",
        )
    # The seeded example already has an engagement at the demonstration office.
    conn = op.get_bind()
    row = conn.execute(
        sa.text("""SELECT e.talent_id, e.office_id, m.user_id FROM engagements e
       JOIN talents t ON t.id=e.talent_id JOIN users u ON u.id=t.user_id
       JOIN offices o ON o.id=e.office_id JOIN office_members m ON m.office_id=o.id
       JOIN users r ON r.id=m.user_id
       WHERE u.is_demo AND o.is_demo AND r.is_demo AND u.email='talent1@tsunagu.example'
       AND r.email='office@tsunagu.example' AND m.member_role='owner' ORDER BY e.id LIMIT 1""")
    ).first()
    if row:
        conn.execute(
            sa.text("""UPDATE talent_skills SET verified_at=CURRENT_TIMESTAMP, verified_by=:reviewer, verified_office_id=:office
            WHERE talent_id=:talent AND ((kind='tool' AND tag='excel') OR (kind='strength' AND tag='collect'))"""),
            {"talent": row[0], "office": row[1], "reviewer": row[2]},
        )


def downgrade():
    with op.batch_alter_table("talent_skills") as batch:
        batch.drop_constraint("fk_skill_office", type_="foreignkey")
        batch.drop_constraint("fk_skill_reviewer", type_="foreignkey")
        batch.drop_column("verified_office_id")
        batch.drop_column("verified_by")
        batch.drop_column("verified_at")
