import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.adapters.persistence.database import create_session_factory
from app.adapters.persistence.pg_repositories import PgArtifactRepository, PgRestoreReconciliation
from app.adapters.storage.local_fs import LocalArtifactStore
from app.application.services.restore_reconciliation import reconcile_after_restore


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    sf = create_session_factory()
    report = reconcile_after_restore(
        PgRestoreReconciliation(sf),
        PgArtifactRepository(sf),
        LocalArtifactStore(Path(os.environ.get("ARTIFACT_ROOT", "/tmp/agentic_analytics_artifacts"))),
        now=datetime.now(timezone.utc),
        requeue_runs=os.environ.get("RUN_EXECUTION_MODE", "inline").strip().lower() == "celery",
    )
    sys.stdout.write(report.summary() + "\n")


if __name__ == "__main__":
    main()
