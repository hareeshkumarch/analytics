# Operations & Startup Guide

## Prerequisites
- Docker Desktop / Docker Engine with Docker Compose v2 (WSL2 on Windows or Linux host)
- Python 3.11+ on host for `make doctor`

## Quickstart
1. Copy `.env.example` to `.env` and set `APP_MASTER_KEY` (generate via `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`).
2. Run `make doctor` to verify configuration and limits.
3. Run `make dev` to start `edge`, `web`, `api`, `postgres`, `redis`, and the Celery `worker` on `127.0.0.1:8080`. The one-shot `migrate` service applies Alembic migrations before the API and worker start. Background investigations are persisted in PostgreSQL and queued through Redis. Set `RUN_EXECUTION_MODE=inline` in `.env` when running the API without the worker.
4. Run `make smoke` to verify `/api/v1/health`.

## Backup & Restore
- Backup PostgreSQL + published artifacts: `make backup-dev`
- Restore from backup directory: `make restore-dev BACKUP_DIR=./backups/<timestamp>`

The web app calls the API at the relative path `/api/v1`, which Caddy serves on the same origin. To run `next dev` on its own, start it with `API_PROXY_ORIGIN=http://127.0.0.1:8000` so `/api` is proxied to the API, or set `NEXT_PUBLIC_API_BASE` to an absolute URL at build time.
