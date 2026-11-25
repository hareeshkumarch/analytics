"""Record which provider, model and role each budget attempt used, and its latency.

Usage can then be broken down by model and provider. Older rows keep NULLs and
are reported as "unknown".
"""

from alembic import op
import sqlalchemy as sa

revision = "0007_budget_attempt_models"
down_revision = "0006_semantic_version_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("budget_reservations") as batch:
        batch.add_column(sa.Column("provider_id", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("model_id", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("role", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("latency_ms", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("budget_reservations") as batch:
        batch.drop_column("latency_ms")
        batch.drop_column("role")
        batch.drop_column("model_id")
        batch.drop_column("provider_id")
