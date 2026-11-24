"""Keep every approved definition set as its own semantic version.

Approval now inserts a new row instead of editing the latest one, and
(snapshot_id, version) becomes unique so two concurrent approvals of the same
version can't both succeed. Existing duplicates are renumbered above the
snapshot's highest version first, oldest kept.
"""

from alembic import op
import sqlalchemy as sa

revision = "0006_semantic_version_history"
down_revision = "0005_snapshot_version_allocation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "WITH ranked AS ("
            " SELECT semantic_version_id, snapshot_id,"
            " ROW_NUMBER() OVER (PARTITION BY snapshot_id, version ORDER BY created_at, semantic_version_id) AS dup_rank"
            " FROM semantic_versions"
            "), renumbered AS ("
            " SELECT r.semantic_version_id,"
            " (SELECT MAX(s.version) FROM semantic_versions s WHERE s.snapshot_id = r.snapshot_id)"
            " + ROW_NUMBER() OVER (PARTITION BY r.snapshot_id ORDER BY r.semantic_version_id) AS new_version"
            " FROM ranked r WHERE r.dup_rank > 1"
            ")"
            " UPDATE semantic_versions SET version = renumbered.new_version"
            " FROM renumbered WHERE semantic_versions.semantic_version_id = renumbered.semantic_version_id"
        )
    )
    op.drop_index("ix_semantic_versions_snapshot_version", table_name="semantic_versions")
    op.create_unique_constraint(
        "uq_semantic_versions_snapshot_version", "semantic_versions", ["snapshot_id", "version"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_semantic_versions_snapshot_version", "semantic_versions", type_="unique")
    op.create_index("ix_semantic_versions_snapshot_version", "semantic_versions", ["snapshot_id", "version"])
