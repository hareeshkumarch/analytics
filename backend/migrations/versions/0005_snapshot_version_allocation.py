"""Allocate snapshot versions on the server.

- sources.last_version: per-source counter the API increments under a row lock before
  extraction, so concurrent imports of one source get distinct versions.
- snapshot_manifests: (source_id, version) becomes unique. Duplicates from browser-chosen
  versions are renumbered above the source's highest version first, oldest kept.
"""

from alembic import op
import sqlalchemy as sa

revision = "0005_snapshot_version_allocation"
down_revision = "0004_run_orchestration_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sources",
        sa.Column("last_version", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.execute(
        sa.text(
            "WITH ranked AS ("
            " SELECT snapshot_id, source_id,"
            " ROW_NUMBER() OVER (PARTITION BY source_id, version ORDER BY created_at, snapshot_id) AS dup_rank"
            " FROM snapshot_manifests"
            "), renumbered AS ("
            " SELECT r.snapshot_id,"
            " (SELECT MAX(m.version) FROM snapshot_manifests m WHERE m.source_id = r.source_id)"
            " + ROW_NUMBER() OVER (PARTITION BY r.source_id ORDER BY r.snapshot_id) AS new_version"
            " FROM ranked r WHERE r.dup_rank > 1"
            ")"
            " UPDATE snapshot_manifests SET version = renumbered.new_version"
            " FROM renumbered WHERE snapshot_manifests.snapshot_id = renumbered.snapshot_id"
        )
    )
    op.execute(
        sa.text(
            "UPDATE sources SET last_version = COALESCE("
            "(SELECT MAX(version) FROM snapshot_manifests m WHERE m.source_id = sources.source_id), 0)"
        )
    )
    op.drop_index("ix_snapshot_manifests_source_version", table_name="snapshot_manifests")
    op.create_unique_constraint(
        "uq_snapshot_manifests_source_version", "snapshot_manifests", ["source_id", "version"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_snapshot_manifests_source_version", "snapshot_manifests", type_="unique")
    op.create_index("ix_snapshot_manifests_source_version", "snapshot_manifests", ["source_id", "version"])
    op.drop_column("sources", "last_version")
