# 09 - Test coverage

## Covered
- Backend (`backend/tests`, run in CI with a 120 s per-test timeout): tool loop and budgets, query IR
  and SQL guardrails, truncation, date and filter validation, the `calculate` tool, parsers and type
  inference (CSV/TSV/XLSX encodings, INR amounts, dates), connectors (REST, remote files, uploaded
  SQLite/DuckDB, uploads), persistence and 503 handling, snapshot and semantic versioning
  (including concurrency), deletion, run pagination, usage totals, routing and provider adapters
  (including streaming), structured output, planner-driven replanning, the deep-run budget
  guardrails, raw-record egress, error exposure, key rotation and the inline and background run
  lifecycle (clarification pause and resume).
- Frontend (Playwright in CI against `next start`): navigation, the connector catalog and the 404 page.
- Live stack (checked by hand on 2026-11-24/25 with PostgreSQL, Redis, the API in Celery mode, a
  worker and a mock provider): migrations 0001-0006, import, approve, a worker run, SSE, evidence,
  pinning, API restart, cancellation, worker crash recovery and answer streaming.

## Still missing
- An automated live-stack job (Compose, or PostgreSQL and Redis services in CI).
- Playwright flows that import a file, approve definitions and ask a question against a running
  backend.
- Frontend unit tests (Vitest and Testing Library) and ESLint (I9).
- Tests for the BigQuery, Google Sheets, Airtable, PostgreSQL and MySQL connectors against real
  services (#7).
