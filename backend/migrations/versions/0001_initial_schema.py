from alembic import op
import sqlalchemy as sa

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("workspace_id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("retained_raw_bytes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "credential_refs",
        sa.Column("credential_id", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(64), sa.ForeignKey("workspaces.workspace_id"), nullable=False),
        sa.Column("provider_or_source", sa.String(64), nullable=False),
        sa.Column("generation", sa.Integer, nullable=False, server_default="1"),
        sa.Column("encrypted_payload", sa.Text, nullable=False),
        sa.Column("revoked", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "sources",
        sa.Column("source_id", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(64), sa.ForeignKey("workspaces.workspace_id"), nullable=False),
        sa.Column("family", sa.String(32), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("config_json", sa.JSON, nullable=False),
        sa.Column("credential_id", sa.String(64), sa.ForeignKey("credential_refs.credential_id"), nullable=True),
        sa.Column("current_snapshot_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "snapshot_manifests",
        sa.Column("snapshot_id", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(64), sa.ForeignKey("workspaces.workspace_id"), nullable=False),
        sa.Column("source_id", sa.String(64), sa.ForeignKey("sources.source_id"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("schema_fingerprint", sa.String(64), nullable=False),
        sa.Column("manifest_json", sa.JSON, nullable=False),
        sa.Column("artifact_id", sa.String(64), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "semantic_versions",
        sa.Column("semantic_version_id", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(64), sa.ForeignKey("workspaces.workspace_id"), nullable=False),
        sa.Column("snapshot_id", sa.String(64), sa.ForeignKey("snapshot_manifests.snapshot_id"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("approved", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("definitions_json", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "conversations",
        sa.Column("conversation_id", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(64), sa.ForeignKey("workspaces.workspace_id"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "runs",
        sa.Column("run_id", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(64), sa.ForeignKey("workspaces.workspace_id"), nullable=False),
        sa.Column("conversation_id", sa.String(64), sa.ForeignKey("conversations.conversation_id"), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=True),
        sa.Column("snapshot_id", sa.String(64), nullable=True),
        sa.Column("semantic_version_id", sa.String(64), nullable=True),
        sa.Column("policy_hash", sa.String(64), nullable=False),
        sa.Column("last_event_sequence", sa.Integer, nullable=False, server_default="0"),
        sa.Column("owner_generation", sa.Integer, nullable=False, server_default="1"),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("answer_json", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "budget_reservations",
        sa.Column("reservation_id", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(64), sa.ForeignKey("runs.run_id"), nullable=False),
        sa.Column("operation_id", sa.String(64), nullable=False),
        sa.Column("attempt_id", sa.String(64), nullable=False),
        sa.Column("reserved_tokens", sa.Integer, nullable=False),
        sa.Column("reserved_cost_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("cost_is_unknown", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("settled", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("actual_tokens", sa.Integer, nullable=True),
        sa.Column("actual_cost_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("unresolved_reason", sa.Text, nullable=True),
        sa.UniqueConstraint("run_id", "operation_id", "attempt_id", name="uq_budget_op_attempt"),
    )
    op.create_table(
        "run_events",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(64), sa.ForeignKey("runs.run_id"), nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.JSON, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "sequence", name="uq_run_event_seq"),
    )
    op.create_table(
        "outbox_jobs",
        sa.Column("job_id", sa.String(64), primary_key=True),
        sa.Column("job_type", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.JSON, nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "pins",
        sa.Column("pin_id", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(64), sa.ForeignKey("workspaces.workspace_id"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("question_text", sa.Text, nullable=False),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("artifact_json", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "routing_policies",
        sa.Column("policy_version", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.String(64), sa.ForeignKey("workspaces.workspace_id"), nullable=False),
        sa.Column("policy_json", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "idempotency_keys",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("response_json", sa.JSON, nullable=False),
        sa.Column("status_code", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    for tbl in (
        "idempotency_keys",
        "routing_policies",
        "pins",
        "outbox_jobs",
        "run_events",
        "budget_reservations",
        "runs",
        "conversations",
        "semantic_versions",
        "snapshot_manifests",
        "sources",
        "credential_refs",
        "workspaces",
    ):
        op.drop_table(tbl)
