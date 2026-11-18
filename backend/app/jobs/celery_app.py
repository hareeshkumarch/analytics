import os
from celery import Celery
from celery.signals import worker_process_shutdown

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")

celery_app = Celery(
    "agentic_analytics",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["app.jobs.tasks"],
)
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    worker_concurrency=1,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
)


@worker_process_shutdown.connect
def _dispose_worker_resources(**_: object) -> None:
    from app.jobs.runner import dispose_worker_session_factory

    dispose_worker_session_factory()
