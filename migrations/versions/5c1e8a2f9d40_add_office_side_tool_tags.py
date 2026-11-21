"""Allow JDL, TKC and MJS as accounting software a connector can claim."""

from alembic import op

revision = "5c1e8a2f9d40"
down_revision = "7f5826cd77ed"
branch_labels = None
depends_on = None

STRENGTHS = "('collect', 'check', 'entry', 'report', 'tooling', 'teaching')"
OLD_TOOLS = "('freee', 'mf', 'yayoi', 'excel', 'sheets', 'python', 'other')"
NEW_TOOLS = "('freee', 'mf', 'yayoi', 'jdl', 'tkc', 'mjs', 'excel', 'sheets', 'python', 'other')"


def _rule(tools: str) -> str:
    return (
        f"(kind = 'tool' AND tag IN {tools}) "
        f"OR (kind = 'strength' AND tag IN {STRENGTHS})"
    )


def upgrade() -> None:
    with op.batch_alter_table("talent_skills") as batch:
        batch.drop_constraint("talent_skill_valid_tag", type_="check")
        batch.create_check_constraint("talent_skill_valid_tag", _rule(NEW_TOOLS))


def downgrade() -> None:
    # 'jdl'・'tkc'・'mjs' の行が残っていると戻せない（行は消さない）。先に人が確かめる。
    with op.batch_alter_table("talent_skills") as batch:
        batch.drop_constraint("talent_skill_valid_tag", type_="check")
        batch.create_check_constraint("talent_skill_valid_tag", _rule(OLD_TOOLS))
