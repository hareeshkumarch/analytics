"""Metadata for exported answers: workspace, run, format, size, digest and expiry."""

from alembic import op
import sqlalchemy as sa

revision = "0014_export_artifacts"
down_revision = "0013_model_call_prompt_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "export_artifacts",
        sa.Column("artifact_id", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=True),
        sa.Column("format", sa.String(16), nullable=False),
        sa.Column("byte_size", sa.Integer, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_export_artifacts_expires", "export_artifacts", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_export_artifacts_expires", table_name="export_artifacts")
    op.drop_table("export_artifacts")
