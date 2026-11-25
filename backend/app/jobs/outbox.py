import logging
from collections.abc import Callable
from typing import Any, Protocol

from app.adapters.persistence.pg_repositories import RUN_JOB

logger = logging.getLogger(__name__)


class OutboxStore(Protocol):
    def claim_outbox_jobs(self, limit: int = 20, stale_after_seconds: int = 60) -> list[dict[str, Any]]: ...

    def finish_outbox_job(self, job_id: str, published: bool) -> None: ...


def publish_to_celery(job: dict[str, Any]) -> None:
    if job["job_type"] != RUN_JOB:
        raise ValueError(f"Unknown outbox job type {job['job_type']!r}")
    from app.jobs.tasks import run_investigation_task

    run_investigation_task.delay(str(job["payload"]["run_id"]), "", "", "")


def dispatch_pending(store: OutboxStore, publish: Callable[[dict[str, Any]], None] = publish_to_celery) -> int:
    jobs = store.claim_outbox_jobs()
    for index, job in enumerate(jobs):
        try:
            publish(job)
        except Exception:
            logger.warning("Could not publish outbox job %s; it stays pending", job["job_id"], exc_info=True)
            for unsent in jobs[index:]:
                store.finish_outbox_job(unsent["job_id"], published=False)
            return index
        store.finish_outbox_job(job["job_id"], published=True)
    return len(jobs)
