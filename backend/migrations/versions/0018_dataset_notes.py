"""Notes a user confirms about a data source (definitions, corrections), remembered across its versions."""

from alembic import op
import sqlalchemy as sa

revision = "0018_dataset_notes"
down_revision = "0017_run_question"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dataset_notes",
        sa.Column("note_id", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(64), sa.ForeignKey("workspaces.workspace_id"), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_dataset_notes_ws_source", "dataset_notes", ["workspace_id", "source_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_dataset_notes_ws_source", table_name="dataset_notes")
    op.drop_table("dataset_notes")
