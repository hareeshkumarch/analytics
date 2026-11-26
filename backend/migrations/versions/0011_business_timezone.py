"""Business time zone per workspace; date-only bounds in queries are read in it."""

from alembic import op
import sqlalchemy as sa

revision = "0011_business_timezone"
down_revision = "0010_tool_executions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("workspaces") as batch:
        batch.add_column(sa.Column("business_timezone", sa.String(64), nullable=False, server_default="UTC"))


def downgrade() -> None:
    with op.batch_alter_table("workspaces") as batch:
        batch.drop_column("business_timezone")
