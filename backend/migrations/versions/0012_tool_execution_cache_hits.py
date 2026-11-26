"""Mark tool executions answered from the query result cache."""

from alembic import op
import sqlalchemy as sa

revision = "0012_tool_execution_cache_hits"
down_revision = "0011_business_timezone"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tool_executions") as batch:
        batch.add_column(sa.Column("cache_hit", sa.Boolean, nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("tool_executions") as batch:
        batch.drop_column("cache_hit")
