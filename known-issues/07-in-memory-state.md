# 07 - Runtime and budget state still depends on the API process

## Current status

Partly fixed. Postgres repositories are used for run records and answers, ordered events, evidence, budget reservations, snapshots and semantic definitions, provider credentials, routing policies, and pins. Compose runs investigations in Celery workers with PostgreSQL-backed cancellation and queue counts. Snapshot artifacts are stored in the configured artifact store. The API still keeps in-memory caches and some process-local state.

## State that still does not survive or coordinate across processes

- In Compose, worker task handles and worker concurrency are local to Celery processes; cancellation and event history are shared through PostgreSQL. Inline mode remains API-process-owned and still uses a process-local semaphore.
- Evidence is persisted in PostgreSQL; `app.state.evidence` is only a cache/fallback.
- The PostgreSQL budget ledger is used when `DATABASE_URL` is PostgreSQL. SQLite/inline development uses a local ledger and does not provide cross-process durability.
- Idempotency keys and live event subscriber queues are process-local. Multiple API workers can accept duplicate requests; worker SSE uses polling of the shared event log.
- API state dictionaries remain the source for some active lookups and fallback paths, which makes database and memory behavior diverge on partial persistence failures.
- Provider credentials are encrypted in Postgres and loaded at startup, but are copied into the routing service's process-local key dictionary. Credential access remains coupled to private routing-service state.
- Data now stores a hashed source descriptor-to-`source_id` mapping in `sessionStorage`, preserving versions across page reloads in the same browser session. Starting a new browser session can still create a new source instead of another version.

## Fix

1. Keep Postgres as the source of truth and avoid silently falling back to memory after database errors in production.
2. Add transactional outbox dispatch and verify Celery redelivery/restart behavior against the live stack.
3. Move idempotency keys to shared storage; inline mode also needs shared active-run coordination if deployed with multiple API workers.
5. Replace direct access to `routing_service._api_keys` with a credential-provider interface; load credentials by workspace without sharing one process-global key across workspaces.
6. If source continuity across browser sessions is required, persist or resolve source identity on the backend; current session storage covers page reloads only.
7. Prune durable events and idempotency records under an explicit retention policy.

## What to cover

- Save a key and routing policy, restart the API, and confirm settings and provider use are unchanged.
- Publish and approve a snapshot, restart, and confirm its metadata, definitions, and artifact remain available.
- Finish a Celery run, restart API, and confirm answer, events, evidence, and usage totals remain readable.
- Run two API processes and confirm idempotency behavior; confirm cancellation and event sequences remain shared.
- Re-extract a source after a page reload and confirm the snapshot version increments; verify new-session behavior against the desired source identity policy.
