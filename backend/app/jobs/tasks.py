from typing import Any

from app.jobs.celery_app import celery_app


@celery_app.task(name="analytics.run_investigation", bind=True, max_retries=0)
def run_investigation_task(self: Any, run_id: str, workspace_id: str, question: str, mode: str) -> dict[str, str]:
    del self, workspace_id, question, mode
    from app.jobs.runner import execute_persisted_run

    return execute_persisted_run(run_id)
