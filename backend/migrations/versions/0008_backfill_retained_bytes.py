"""Fill workspaces.retained_raw_bytes from the snapshots that exist.

The storage total used to live in API memory and was lost on restart; it is now
kept in this column, so start it from the stored snapshot sizes.
"""

from alembic import op
import sqlalchemy as sa

revision = "0008_backfill_retained_bytes"
down_revision = "0007_budget_attempt_models"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE workspaces w SET retained_raw_bytes = COALESCE(("
            " SELECT SUM(COALESCE((s.manifest_json->>'total_normalized_bytes')::bigint, 0))"
            " FROM snapshot_manifests s WHERE s.workspace_id = w.workspace_id"
            "), 0)"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("UPDATE workspaces SET retained_raw_bytes = 0"))
