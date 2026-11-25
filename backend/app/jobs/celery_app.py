import os
from celery import Celery
from celery.signals import setup_logging, worker_process_shutdown

from app.domain.limits import DEEP_BUDGET_LIMITS

REDELIVERY_MARGIN_SECONDS = 300

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
REDELIVERY_SECONDS = int(
    os.environ.get("RUN_REDELIVERY_SECONDS", DEEP_BUDGET_LIMITS.active_seconds + REDELIVERY_MARGIN_SECONDS)
)

celery_app = Celery(
    "agentic_analytics",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["app.jobs.tasks"],
)
celery_app.conf.update(
    broker_transport_options={"visibility_timeout": REDELIVERY_SECONDS},
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    worker_concurrency=1,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
)


@setup_logging.connect
def _configure_worker_logging(**_: object) -> None:
    from app.bootstrap.logging_setup import configure_logging

    configure_logging()


@worker_process_shutdown.connect
def _dispose_worker_resources(**_: object) -> None:
    from app.jobs.runner import dispose_worker_session_factory

    dispose_worker_session_factory()
