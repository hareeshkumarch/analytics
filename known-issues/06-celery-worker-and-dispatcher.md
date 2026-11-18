# 06 - Celery worker execution

## Current status

**Implemented:** Compose starts Redis and a Celery worker by default. In `RUN_EXECUTION_MODE=celery`,
the API requires PostgreSQL persistence, stores the run before enqueueing it, and returns 503 if
persistence or queue dispatch fails. The worker reloads run inputs and shared state from PostgreSQL,
persists status, events, evidence, and answers, honors cancellation, and uses stable LangGraph/Deep
Agents checkpoint thread IDs. API SSE reads the persisted event log so it works across processes.
Worker-lost Celery tasks are rejected for redelivery.

The API does not mark worker-owned runs failed on startup. Inline mode retains API-process execution
and startup replay. SQLite is unsupported for Celery mode.

## Remaining reliability work

- Dispatch uses a direct Celery publish after the run row commits; a transactional outbox is still a
  possible improvement for the crash window between commit and broker publish.
- Queue limits and cancellation use PostgreSQL, while worker concurrency is configured per Celery
  process. Multi-worker deployment has not been exercised here.
- Export and clarification-expiry tasks are out of scope; only `run_investigation_task` is used.

## What to validate against a live Compose stack

- Submit a background run and observe durable worker status and API SSE events.
- Restart the API during a run; the worker should continue and reconnecting SSE should replay events.
- Cancel through the API and confirm the worker stops cooperatively and persists `cancelled`.
- Confirm a lost worker causes Celery redelivery and specialist checkpoint resume.
- Confirm Celery mode rejects SQLite persistence instead of leaving an unexecutable run queued.
