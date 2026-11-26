"""Durable task records: one row per planned task with versioned status, fingerprint, budget slice and result."""

from alembic import op
import sqlalchemy as sa

revision = "0016_run_tasks"
down_revision = "0015_ops_switches"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "run_tasks",
        sa.Column("run_id", sa.String(64), sa.ForeignKey("runs.run_id"), primary_key=True),
        sa.Column("task_id", sa.String(64), primary_key=True),
        sa.Column("plan_revision", sa.Integer, nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("objective", sa.Text, nullable=False),
        sa.Column("acceptance_criteria", sa.JSON, nullable=False),
        sa.Column("dependencies", sa.JSON, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempt", sa.Integer, nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("evidence_ids", sa.JSON, nullable=False),
        sa.Column("result_json", sa.JSON, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("budget_tokens", sa.Integer, nullable=True),
        sa.Column("budget_tool_calls", sa.Integer, nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("run_tasks")
