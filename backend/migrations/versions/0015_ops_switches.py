"""Operator switches that pause new runs or a provider, with a change history, and an index for metrics."""

from alembic import op
import sqlalchemy as sa

revision = "0015_ops_switches"
down_revision = "0014_export_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ops_switches",
        sa.Column("switch_id", sa.String(64), primary_key=True),
        sa.Column("paused", sa.Boolean, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "ops_switch_changes",
        sa.Column("change_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("switch_id", sa.String(64), nullable=False),
        sa.Column("paused", sa.Boolean, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_run_events_type_occurred", "run_events", ["type", "occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_run_events_type_occurred", table_name="run_events")
    op.drop_table("ops_switch_changes")
    op.drop_table("ops_switches")
