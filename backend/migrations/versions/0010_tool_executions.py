"""One row per agent tool call: tool version, argument fingerprint, status, error code and timing."""

from alembic import op
import sqlalchemy as sa

revision = "0010_tool_executions"
down_revision = "0009_outbox_claims"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tool_executions",
        sa.Column("execution_id", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("task_id", sa.String(128), nullable=False),
        sa.Column("tool", sa.String(64), nullable=False),
        sa.Column("tool_version", sa.String(16), nullable=False),
        sa.Column("args_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("duration_ms", sa.Integer, nullable=False),
        sa.Column("evidence_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_tool_executions_run", "tool_executions", ["run_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_tool_executions_run", table_name="tool_executions")
    op.drop_table("tool_executions")
