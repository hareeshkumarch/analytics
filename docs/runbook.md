# Incident runbook

This runbook covers the Docker Compose stack in `docker-compose.yml`. Commands run from the repository root.

## Signals

| Where | What |
|---|---|
| `GET /api/v1/health` | Liveness: the API process is up. |
| `GET /api/v1/ready` | Readiness: the database answers and, in Celery mode, Redis answers. Returns 503 with the failing check otherwise. The `api` container's health check uses it. |
| `GET /api/v1/metrics` | Prometheus metrics, computed from the database so they cover the API and every worker. The edge proxy hides this path: scrape `api:8000` on the internal network. |
| Settings → Incident controls (bottom of the page) and Settings → Providers | Pause new questions, or pause a provider from its row; Incident controls also show the change history. Also `GET/PUT /api/v1/ops/switches[/{id}]`. |
| Usage page, `/runs/{id}/model-calls`, `/runs/{id}/tool-executions` | Per-run cost, tokens, prompt versions and tool calls. |
| JSON logs (`LOG_FORMAT=json`) and OTLP traces | Every line carries `request_id` and `run_id`. |

Start Prometheus (`127.0.0.1:9090`, alert rules in `infra/monitoring/alerts.yml`) and Grafana (`127.0.0.1:3001`, dashboard "Ledgerline SLOs"):

```bash
docker compose --profile monitoring up -d
```

### Metrics

| Metric | Meaning |
|---|---|
| `ledgerline_runs{status,mode,outcome}` | Runs by current state. |
| `ledgerline_runs_finished_window{outcome}` | Runs finished in the last hour. |
| `ledgerline_verified_completion_ratio` | Share of last-hour runs whose outcome is `success`. 1 when nothing finished. |
| `ledgerline_run_duration_seconds{mode,quantile}` | p50 and p95 from creation to finish, last hour. |
| `ledgerline_model_calls_total{provider,result}` | Model calls, `ok` or `error` (a call that failed or was abandoned). |
| `ledgerline_queue_depth`, `ledgerline_queue_oldest_age_seconds` | Runs waiting to start and the age of the oldest one. |
| `ledgerline_outbox_pending` | Run jobs not yet handed to Redis. |
| `ledgerline_stuck_runs` | Runs in `running` with no event for 20 minutes (twice the longest time budget). |
| `ledgerline_switch_paused{switch}` | Paused operator switches. |

## Pause and resume

Pausing is the first step for most incidents. It is reversible and deletes nothing.

- **Pause new questions** (`runs`): new runs and pin refreshes get `503 service_paused` with your reason. Runs already queued or running continue.
- **Pause a provider** (`provider:<id>`, such as `provider:openai`): routing skips that provider's models from the next model call (within about 5 seconds in every process). Roles with a fallback model on another provider keep working. A run whose only models are paused fails with "paused providers: …" in its error.

Every change needs a reason and is stored in `ops_switch_changes`. From the command line:

```bash
curl -X PUT http://127.0.0.1:8000/api/v1/ops/switches/provider:openai -H 'Content-Type: application/json' -d '{"paused": true, "reason": "OpenAI outage, incident 42"}'
```

Resume with `"paused": false` and a reason. `LedgerlineSwitchPaused` reminds you after 4 hours.

## A provider is failing

Alert `LedgerlineModelErrorsHigh`. Also look for runs failing with `provider_error`.

1. Check the provider's status page and the API logs for `provider_error` with the provider id.
2. If the errors are 401 or 403, the key was revoked or rotated: save a new key under Settings → Providers.
3. If the provider is down or rate limiting, pause `provider:<id>`. Under Settings → Routing, make sure each role has a model from another provider, or turn on fallback.
4. When the provider recovers, resume the switch. Circuit breakers close on their own after the cool-down.

## Runs are stuck in the queue

Alert `LedgerlineQueueBacklog`, or `ledgerline_queue_depth` keeps rising.

1. Check `docker compose ps`. The worker must be running, and `redis` and `api` must be healthy.
2. If `ledgerline_outbox_pending` is rising, the API cannot reach Redis. Look at `/api/v1/ready` and restart `redis`. The outbox re-dispatches when it is back.
3. If the worker is up but idle, restart it with `docker compose restart worker`. Queued runs are kept in PostgreSQL and redelivered.
4. To shed load while you recover, pause `runs`.

## A run is stuck

Alert `LedgerlineStuckRuns`.

1. Find the run: `docker compose exec postgres psql -U postgres analytics_poc -c "select run_id, created_at from runs where status='running'"`.
2. Read its events (`GET /api/v1/workspaces/{ws}/runs/{run_id}/events`) and the worker logs filtered by `run_id`.
3. Cancel it from the Ask page, or restart the worker. Redelivery is fenced, so a stale worker cannot finish a run that a newer one owns.

## Runs are slow

Alert `LedgerlineRunLatencyHigh`.

1. On the Usage page, compare model latency and look for one slow model or provider.
2. Under Settings → Routing, switch the preset to Latency, or move the slow model down a role's list.
3. If DuckDB queries are slow (`tool-executions` latency), look for very large snapshots and for the query timeout in the logs.

## Answer quality dropped

Alert `LedgerlineVerifiedCompletionLow`.

1. Look at recent runs with outcome `needs_review` or `partial_success`, and at their criteria.
2. Check whether a prompt version changed (`GET /api/v1/prompts`, `/runs/{id}/model-calls`) or a model was re-routed.
3. Run `make eval` against the current routing and compare it with the baseline (see `docs/operations.md`).
4. Roll back the routing or prompt change. Pause the provider if a model update caused the drop.

## API down or not ready

Alert `LedgerlineApiDown`, or `/api/v1/ready` returns 503.

1. `docker compose ps` and `docker compose logs api --tail 200`.
2. If the database is unavailable, check `postgres` health and disk space. Restore from `make backup-dev` output only if the data is damaged (`make restore-dev BACKUP_DIR=...`). The restore then runs `python -m app.jobs.reconcile`, which prints what it repaired:
   - datasets with missing files are marked unavailable (runs on them get a 409 until they are re-imported)
   - exports whose file is gone are removed
   - in Celery mode, runs that were queued or running are re-queued and resume from their finished tasks
   - files that nothing references are deleted

   Running it again is safe and should report nothing to do.
3. If Redis is unavailable, restart `redis`. Runs created meanwhile wait in the outbox.
4. On shutdown, the API stops its background tasks, and Compose gives it 30 seconds. In inline mode, runs that were active resume when the API starts again. In Celery mode, the worker owns runs, and they are redelivered after a restart.

## Secrets leaked

1. Rotate the provider key with the provider, then save it under Settings → Providers.
2. If `APP_MASTER_KEY` leaked, put a new key first and keep the old one after it (`APP_MASTER_KEY=new,old`), restart `api` and `worker`, run `make rotate-keys`, then remove the old key and restart again.
3. Logs are redacted, but search them for the key prefix and purge any copies.

## Authentication

Incident controls, provider pauses and metrics are not authenticated yet. Keep the stack bound to `127.0.0.1` (the Compose default) until sign-in lands.
