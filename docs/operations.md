# Operations & Startup Guide

## Prerequisites
- Docker Desktop / Docker Engine with Docker Compose v2 (WSL2 on Windows or Linux host)
- Python 3.11+ on host for `make doctor`

## Quickstart
1. Copy `.env.example` to `.env` and set `APP_MASTER_KEY` (generate via `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`).
2. Run `make doctor` to verify configuration and limits.
3. Run `make dev` to start `edge` (`127.0.0.1:8080`), `web` (`127.0.0.1:3000`), `api` (`127.0.0.1:8000`), `postgres`, `redis`, and the Celery `worker`. The one-shot `migrate` service applies Alembic migrations before the API and worker start. Background investigations are persisted in PostgreSQL and queued through Redis. Set `RUN_EXECUTION_MODE=inline` in `.env` when running the API without the worker.
4. Run `make smoke` to verify `/api/v1/health`. `/api/v1/ready` also checks the database and Redis; incident steps, metrics and the operator switches are in [`runbook.md`](runbook.md).

## Evaluation
`make eval` runs the questions in `backend/app/evaluation/corpus/inr_v1.json` against the INR sample dataset in an isolated in-process app, using the provider keys in the environment (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, ...). Each question is scored from its answer and evidence rows: expected figures must appear in the answer and in a cited evidence row, and the run must not need review. Ambiguous, unanswerable and prompt-injection questions have their own checks.

The JSON and Markdown report goes to `backend/eval/reports/` with per-question pass/fail, route, tokens, cached tokens, cost, model calls and latency, plus totals and deltas against `backend/eval/baselines/<corpus>_v<version>.json`. The command exits non-zero when the pass rate drops by more than `--pass-margin` (default 0.1) or cost per verified completion rises by more than `--cost-margin` (default 0.25).

- Pick a model for every role: `make eval EVAL_ARGS="--model gpt-6.1-terra"`
- Run a few questions: `make eval EVAL_ARGS="--only q01_net_sales q12_profit"`
- Save a passing report as the new baseline: `make eval EVAL_ARGS="--update-baseline"`

The `Evaluation` workflow runs the corpus nightly and on demand. It is skipped unless a provider key secret is set, and keeps the last passing report as its baseline.

## Dependency security
The `Security` workflow runs on every push and pull request and weekly. It fails when `pip-audit` finds a known vulnerability in `backend/requirements.lock` or `requirements-dev.lock`, or `npm audit` finds a high or critical one in the frontend's production dependencies, and uploads CycloneDX SBOMs for both as the `sbom` artifact. To fix a finding, raise the pin in `backend/pyproject.toml` or `frontend/package.json` and run `make lock`.

## Backup & Restore
- Backup PostgreSQL + published artifacts: `make backup-dev`
- Restore from backup directory: `make restore-dev BACKUP_DIR=./backups/<timestamp>`

The web app calls the API at the relative path `/api/v1`, which Caddy serves on the same origin. To run `next dev` on its own, start it with `API_PROXY_ORIGIN=http://127.0.0.1:8000` so `/api` is proxied to the API, or set `NEXT_PUBLIC_API_BASE` to an absolute URL at build time.
