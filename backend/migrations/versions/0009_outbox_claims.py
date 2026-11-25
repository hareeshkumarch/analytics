"""Record when an outbox job was claimed for publishing.

Celery runs are now enqueued through the outbox in the same transaction as
the run row; a claim older than a minute is retried by the dispatcher.
"""

from alembic import op
import sqlalchemy as sa

revision = "0009_outbox_claims"
down_revision = "0008_backfill_retained_bytes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("outbox_jobs") as batch:
        batch.add_column(sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("outbox_jobs") as batch:
        batch.drop_column("claimed_at")
