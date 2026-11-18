"""Integrity fixes and indexes for the access patterns the API uses.

- routing_policies: primary key becomes (workspace_id, policy_version). The old global
  policy_version key made a second workspace's first save collide with the first
  workspace's version 2.
- pins: one pin per (workspace_id, run_id); duplicate rows from the old
  check-then-insert race are removed first, keeping the oldest.
- Indexes for workspace lists ordered by created_at, snapshot versions per source
  and semantic versions per snapshot.
- sources.config_json: remove uploaded file bytes (raw_base64) that were stored
  with the source config.
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_integrity_and_indexes"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("routing_policies_pkey", "routing_policies", type_="primary")
    op.alter_column("routing_policies", "policy_version", server_default=None)
    op.execute("DROP SEQUENCE IF EXISTS routing_policies_policy_version_seq")
    op.create_primary_key("routing_policies_pkey", "routing_policies", ["workspace_id", "policy_version"])

    op.execute(
        sa.text(
            "DELETE FROM pins a USING pins b "
            "WHERE a.workspace_id = b.workspace_id AND a.run_id = b.run_id "
            "AND (a.created_at, a.pin_id) > (b.created_at, b.pin_id)"
        )
    )
    op.create_unique_constraint("uq_pins_ws_run", "pins", ["workspace_id", "run_id"])

    op.create_index("ix_runs_ws_created", "runs", ["workspace_id", "created_at"])
    op.create_index("ix_pins_ws_created", "pins", ["workspace_id", "created_at"])
    op.create_index("ix_snapshot_manifests_ws_created", "snapshot_manifests", ["workspace_id", "created_at"])
    op.create_index("ix_snapshot_manifests_source_version", "snapshot_manifests", ["source_id", "version"])
    op.create_index("ix_semantic_versions_snapshot_version", "semantic_versions", ["snapshot_id", "version"])

    op.execute(
        sa.text(
            "UPDATE sources SET config_json = "
            "(config_json::jsonb - 'raw_base64' - 'raw_bytes' - 'file_path')::json "
            "WHERE config_json::jsonb ?| array['raw_base64', 'raw_bytes', 'file_path']"
        )
    )


def downgrade() -> None:
    op.drop_index("ix_semantic_versions_snapshot_version", table_name="semantic_versions")
    op.drop_index("ix_snapshot_manifests_source_version", table_name="snapshot_manifests")
    op.drop_index("ix_snapshot_manifests_ws_created", table_name="snapshot_manifests")
    op.drop_index("ix_pins_ws_created", table_name="pins")
    op.drop_index("ix_runs_ws_created", table_name="runs")
    op.drop_constraint("uq_pins_ws_run", "pins", type_="unique")
    op.drop_constraint("routing_policies_pkey", "routing_policies", type_="primary")
    op.create_primary_key("routing_policies_pkey", "routing_policies", ["policy_version"])
