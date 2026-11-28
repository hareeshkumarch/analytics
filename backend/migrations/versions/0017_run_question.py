"""Question text as its own column on runs, so usage search and listings do not open each run's JSON."""

from alembic import op
import sqlalchemy as sa

revision = "0017_run_question"
down_revision = "0016_run_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("question", sa.Text, nullable=True))
    # Backfill from the JSON the question has lived in until now.
    if op.get_bind().dialect.name == "postgresql":
        op.execute("UPDATE runs SET question = answer_json ->> 'question' WHERE question IS NULL AND answer_json IS NOT NULL")
    else:
        op.execute("UPDATE runs SET question = json_extract(answer_json, '$.question') WHERE question IS NULL AND answer_json IS NOT NULL")


def downgrade() -> None:
    op.drop_column("runs", "question")
