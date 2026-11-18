"""Persist run evidence for recovery and cross-process access."""

from alembic import op
import sqlalchemy as sa

revision = "0003_run_evidence"
down_revision = "0002_integrity_and_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "run_evidence",
        sa.Column("evidence_record_id", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(64), sa.ForeignKey("runs.run_id"), nullable=False),
        sa.Column("evidence_id", sa.String(64), nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("task_id", sa.String(64), nullable=False),
        sa.Column("evidence_json", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "evidence_id", "task_id", name="uq_run_evidence_task_id"),
    )
    op.create_index("ix_run_evidence_run_task", "run_evidence", ["run_id", "task_id"])


def downgrade() -> None:
    op.drop_index("ix_run_evidence_run_task", table_name="run_evidence")
    op.drop_table("run_evidence")
