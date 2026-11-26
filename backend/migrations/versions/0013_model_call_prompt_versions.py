"""Record the prompt template version and cached input tokens of every model call."""

from alembic import op
import sqlalchemy as sa

revision = "0013_model_call_prompt_versions"
down_revision = "0012_tool_execution_cache_hits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("budget_reservations") as batch:
        batch.add_column(sa.Column("prompt_id", sa.String(64), nullable=True))
        batch.add_column(sa.Column("prompt_version", sa.String(16), nullable=True))
        batch.add_column(sa.Column("cached_input_tokens", sa.Integer, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("budget_reservations") as batch:
        batch.drop_column("cached_input_tokens")
        batch.drop_column("prompt_version")
        batch.drop_column("prompt_id")
