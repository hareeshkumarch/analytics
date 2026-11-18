"""Persist orchestration plans and clarification state."""

from alembic import op
import sqlalchemy as sa

revision = "0004_run_orchestration_state"
down_revision = "0003_run_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column(
            "orchestration_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("runs", "orchestration_json")
