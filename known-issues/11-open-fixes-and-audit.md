# 11 - Open fixes and audit status (frontend, connectors, tool calling, database)

## Current status

> **For the current list of what is still needed, use [12](12-open-issues-reasons-and-fixes.md).** This file is kept as the audit history and the status table of the original audit items. Its P0–P3 tables were not updated after the clean-up round.

Open. This file collects everything still to fix after the frontend audit, the redesign/connector/tool-calling/database round, and a re-audit of that round's changes. The two earlier reports (`docs/frontend-audit.md`, `docs/review-findings.md`) were deleted as superseded; this file and [12](12-open-issues-reasons-and-fixes.md) are the current record.

**Nothing from the last two rounds has been executed.** Node.js and Python were not installed on the machine that made the changes, so no typecheck, build, unit test, migration or browser check has run. Treat every "fixed" below as "fixed in source, unverified".

### Quick fixes applied since this file was written (in source, unverified)

| # | What changed | Where |
|---|---|---|
| 2 | Pin refresh passes `idempotency_key=None`, so every Re-run creates a new run | `main.py` `refresh_pin` |
| 3 | At startup, runs left `queued`/`running` are marked `failed` (outcome `interrupted`) with a closing `run.finished` event; Ask polls only runs under an hour old (8 s, then 30 s after 5 minutes) | `pg_repositories.py` `fail_interrupted_runs`, `main.py` startup, `AskView.tsx` |
| 5 | Links that return an HTML page are rejected with a clear message | `remote_file_connector.py` `_parse` |
| 17 | Groq, Kimi and NIM adapters tolerate a malformed tool call; it comes back to the model as an unknown-tool error | `groq_adapter.py`, `kimi_adapter.py`, `nim_adapter.py` |
| 20 | The citation filter accepts any evidence id already cited by an earlier step of the run | `orchestration_service.py` |
| 29 | External links in answers show their destination host | `SafeMarkdown.tsx` |
| 34 | Saving the preset or fallback no longer discards unsaved route or ceiling edits | `SettingsView.tsx` |
| 36 | Ask shows a note when the health check (run limits) fails | `AskView.tsx` |
| 39 (part) | Config clean-up: added `.gitignore` (keeps `.env` out of git) and `.dockerignore`; `NEXT_PUBLIC_API_BASE` is now a build argument; idle Celery worker moved behind a Compose profile; placeholder password removed from `alembic.ini`; unused `@radix-ui/react-tabs`, store fields and dispatcher loop removed | root, `infra/compose/`, `docker-compose.yml`, `backend/alembic.ini`, `frontend/` |

Tests added in `backend/tests/test_new_connectors.py`: HTML rejection, interrupted-run reconciliation, and two consecutive pin refreshes. Still open from the P0 list: **#1** (needs Node to generate the lockfile) and **#4** (run everything).

Each item is marked **Confirmed** (follows directly from the code) or **Plausible** (needs a runtime check to confirm).

Priorities:

| Priority | Meaning |
|---|---|
| P0 | Blocks release or breaks a shipped feature |
| P1 | Wrong results or lost data in common cases |
| P2 | Performance, scale or security hardening |
| P3 | UX and polish |

---

## P0: fix first

| # | Issue | Where | Fix | Status |
|---|---|---|---|---|
| 1 | **No frontend lockfile.** CI runs `npm ci`, which fails without `package-lock.json`, and the Docker image uses `npm install`, so builds aren't reproducible. | `.github/workflows/ci.yml:24`, `infra/compose/Dockerfile.frontend:4-5` | Run `npm install` once and commit `frontend/package-lock.json`. Copy it in the Dockerfile and use `npm ci`. | Confirmed |
| 2 | **Pin refresh works once per API process.** `refresh_pin` calls `create_run(...)` directly, so `idempotency_key` is FastAPI's `Header(default=None)` object, not `None`. That object is truthy and hashable, so the first refresh stores its response under it. Every later refresh either gets the earlier run back (same question) or a 409 "Idempotency-Key reused" (different question). Dashboard **Re-run** depends on this endpoint. | `backend/app/api/main.py`, `refresh_pin` → `create_run(workspace_id, CreateRunRequest(...))` | Pass `idempotency_key=None` explicitly. Better, move run creation into a plain function that both endpoints call. Add a test that refreshes two different pins. | Confirmed (logic); Plausible that the object is hashable rather than raising TypeError — either way the feature is broken |
| 3 | **Ask polls forever on runs stuck in `running`.** `/runs` is refetched every 8 s while any saved run isn't finished. After an API restart a run can stay `running` permanently (see [07](07-in-memory-state.md)), so polling never stops, and each poll returns every run with its full answer. | `frontend/src/features/ask/AskView.tsx` (`refetchInterval`); backend startup | Backend: at startup, mark runs without a live task as `failed` (outcome `interrupted`). Frontend: only poll runs created in the last hour, and back off to 30 s after 5 minutes. | Confirmed |
| 4 | **Nothing was executed.** See the checklist at the end. | All | Run the checklist before merging. | — |

## P1: correctness and data integrity

### Connectors

| # | Issue | Where | Fix | Status |
|---|---|---|---|---|
| 5 | **HTML pages are imported as data.** A private Google Sheet export, or a Dropbox/OneDrive share page, can redirect to an HTML login or preview page with HTTP 200, which is then parsed as CSV. | `backend/app/adapters/connectors/remote_file_connector.py`, `_download` / `_parse` | Reject `text/html` responses (and bodies starting with `<!doctype` or `<html`), with a message such as "This link opens a web page, not a file. Share it publicly or use a direct download link." | Confirmed (no check); Plausible (redirect behaviour) |
| 6 | **REST imports store every value as text.** Nested objects become Python `repr` strings, e.g. `{'a': 1}`. Stripe amounts, HubSpot numbers and GitHub counts can't be summed or compared numerically, and HubSpot `properties` can't be read. | `backend/app/adapters/connectors/rest_connector.py`, `extract` (`str(r.get(k))`) | Infer column types with `csv_parser._infer_column_values` (moved to a public module, per [10](10-solid-refactors.md) item 10). Serialize dict/list values with `json.dumps`. Optionally flatten one level of `properties`. | Confirmed |
| 7 | **The MySQL TLS mode and the DuckDB import depend on library behaviour that hasn't been checked.** `pymysql.connect(ssl=SSLContext)`, and the `duckdb_tables()` / `duckdb_views()` / `duckdb_columns()` column names in DuckDB 1.1.3. | `mysql_connector.py` (`_tls_context`), `duckdb_file_connector.py` | Run `tests/test_new_connectors.py` and one live `verify-full` connection (e.g. Azure MySQL). | Plausible |
| 8 | **Snapshot versions are picked by the browser.** Source identity lives in `sessionStorage`, there is no unique `(source_id, version)`, and two tabs can create the same version. Dashboard "Newer data" only works within one source id, so a new browser session's re-import isn't recognized. | `features/data/ConnectSource.tsx` (`runExtract`); `main.py` `extract_connector` | Resolve the source and the next version on the server under a `SELECT … FOR UPDATE` on the source row. Then add the `uq_snap_source_version` constraint (de-duplicating first) and drop the client `version`. | Confirmed |

### Database

| # | Issue | Where | Fix | Status |
|---|---|---|---|---|
| 9 | **Database errors still fall back silently to memory, and the data is lost on restart.** Affected writes: snapshot save, run create, routing save (non-conflict errors), pin create, provider key save, event persist, and lookups that `except: pass`. `update_status` at run start sits outside the try, so a DB error kills the background task with no `run.finished`. | `backend/app/api/main.py` (search `except Exception` near `repo_`) | Make Postgres the source of truth: return 503 on DB errors and update memory only after the commit. See [07](07-in-memory-state.md) step 1. | Confirmed |
| 10 | **The saved routing policy (and its cost limit) isn't applied after a restart** until someone opens `GET /routing`. Runs read only the in-memory policy, so pin refresh and API clients run without the ceiling. | `main.py` run creation (`app.state.routing_policies.get`) | Load the policy through `repo_routing` at run start. | Confirmed |
| 11 | **Approving definitions reads without a lock.** Two concurrent approvals with the same `expected_version` both succeed. Approval also edits the semantic-version row in place, so `runs.semantic_version_id` doesn't identify which definitions a run used. | `pg_repositories.py` `approve_semantics` | `UPDATE … WHERE snapshot_id=:s AND version=:expected RETURNING`. Insert a new semantic-version row on approval instead of mutating. | Confirmed |
| 12 | **Clarification state and answers were not persisted**, so runs could not resume after a restart. | `pg_repositories.py`; `main.py` `clarify_run` | Persist orchestration state, validate revisions atomically, and reschedule the run. | Implemented; runtime verification pending |
| 13 | **Evidence is in memory only.** Evidence chips return "not available" after any API restart. | `main.py` `app.state.evidence` | Persist evidence records, or rebuild them from stored query IR. See [07](07-in-memory-state.md). | Confirmed |

### Tool calling

| # | Issue | Where | Fix | Status |
|---|---|---|---|---|
| 14 | **Truncation is never detected.** `row_count` means "rows returned up to the LIMIT", and the UI and the model both treat it as the true total. | `sqlglot_compiler.py` (LIMIT ≤ 1000), `duckdb_executor.py` (fetches 1001 against a 1000 cap) | Compile `LIMIT n+1` and set `truncated` when the extra row arrives, or add `COUNT(*) OVER()`. | Confirmed |
| 15 | **The token budget isn't part of the final-round decision**, so a step can stop with evidence but no answer text. | `orchestration_service.py` `_run_specialist_task` (`final_round`) | Also take the final round when the tokens left are below about twice the estimated next call. | Plausible |
| 16 | **`inspect_schema` output can exceed 64 KB** on wide workbooks (200 columns × 20 sheets), and the model then sees no column names at all. | `analytics_tools.py` `_cap` | Shrink in stages (fewer fields per column, then table names only), and accept an optional `table_id`. | Plausible |
| 17 | **A malformed tool call from Groq, Kimi or NIM is retried as a transient error** instead of being returned to the model as a tool error. | `groq_adapter.py`, `kimi_adapter.py`, `nim_adapter.py` (`tc["function"]["name"]`) | Use `.get()` and return an unknown-tool error result. | Confirmed |
| 18 | **Weak argument validation.** Comparison filters without a value bind NULL and silently return 0 rows. `compare_periods` accepts any string as a date, and its inclusive end drops most of the last day for timestamp columns. | `domain/query_ir.py`, `sqlglot_compiler.py` | Require `value` for comparison operators, validate ISO dates, and use a half-open `< end + 1 day` bound. | Confirmed |
| 19 | **Raw provider and exception text is still sent** in `task.status.error`, `tool.result.error` and `run.finished.error`. The UI now hides it under "Details", but it remains in the event log and the API. | `orchestration_service.py`, `http_utils.py`, `main.py` | Send `{code, message, remediation}` built from the error code and log the raw text on the server only. | Confirmed |
| 20 | **The citation filter may remove valid links.** It keeps evidence from the step itself and from its `required_inputs`. A deep-mode reviewer step that cites evidence from a step outside its `required_inputs` would lose those links (the text stays). | `orchestration_service.py` `_strip_unknown_citations` call site | Check the deep-mode plan's dependencies. If needed, pass the run's full evidence id set. | Plausible |
| 21 | **`answer.reset` is never emitted.** If it is wired up later, its draft ids (`drf_…`) won't match `answer.delta`'s `draft_<task_id>`, and the UI clears the whole answer. | `routing_service.py` `on_answer_reset`, `deep_agents_bridge.py` | Wire the callback with the task's draft id. The UI should reset only that step's text. | Confirmed |

## P2: performance, scale and security

| # | Issue | Where | Fix | Status |
|---|---|---|---|---|
| 22 | **Unbounded lists.** Ask and Dashboard load every run with its full answer, Usage polls every run every 15 s, and `list_snapshots` runs one query per snapshot (N+1). | `pg_repositories.py` `list_runs`, `list_snapshots`; `main.py` `/usage` | Keyset pagination on `(created_at, id)`, a summary projection without `answer_json`, a lateral join for the latest semantic version, and server-side time filters for `/usage`. | Confirmed |
| 23 | **Uploads are still base64 inside JSON and sent twice** (discover and extract). Links are downloaded twice. | `ConnectSource.tsx`, `/connectors/*` | A multipart upload to a staging endpoint that returns an `upload_id`; discover and extract reference it. Cache link downloads by job. | Confirmed |
| 24 | **Synchronous database calls inside `async def` handlers, and event persistence under the global `RLock`, with no `lock_timeout`.** One slow query stalls every live stream. | `main.py` (run create, approve, routing save, cancel, pins, provider key; `_publish_event`) | Use plain `def` endpoints or `asyncio.to_thread`, write outside the lock, and set `lock_timeout` and `statement_timeout`. | Confirmed |
| 25 | **CORS allows any `localhost` / `127.0.0.1` port.** Any other local web app can read datasets, start paid runs and replace provider keys. | `main.py` `allow_origin_regex` | An exact origin list, plus a per-install token header on mutating routes. | Confirmed (config); risk depends on what else runs locally |
| 26 | **Key handling.** There is a single Fernet key with no rotation. A decrypt failure is only logged, yet the provider still shows "Ready". Keys from all workspaces share one dictionary. | `crypto.py`; `main.py` `_load_saved_credentials`, `list_providers` | `MultiFernet` plus a re-encrypt command; report decrypt failures as "Needs a key"; look up keys per workspace. | Confirmed |
| 27 | **Many open live streams can starve other requests.** Each running answer holds an `EventSource`; over plain HTTP/1.1 (Caddy on `:8080`) browsers allow about 6 connections per origin. | `lib/sse.ts`, Caddy | Serve HTTP/2 (TLS), or multiplex runs over one stream. | Plausible |
| 28 | **A missing `DATABASE_URL` only logs a warning** and still falls back to throwaway in-memory SQLite. | `persistence/database.py` | Fail at startup unless `TESTING=1`. | Confirmed |
| 29 | **Links in model-written answers open without warning.** A prompt-injected dataset could produce a phishing link. | `components/SafeMarkdown.tsx` | Show the destination host next to external links. | Confirmed (no host shown) |

## P3: UX and polish

| # | Issue | Where | Fix |
|---|---|---|---|
| 30 | **Clarification UI and resume.** The Ask view now displays `clarification.required`, posts the current revision, and follows the resumed run. | `AskView.tsx`; run API and repositories | Implemented; verify reload, stale revisions, and inline/Celery resume. |
| 31 | **No follow-up questions.** Everything shares one conversation per workspace (`conv_{workspace_id}`), and a follow-up has no context. | `main.py` `create_run` | Add a conversation id and thread history, or keep the "New question" wording. |
| 32 | **Only `?run=` and `?dataset=` are deep links.** The Data tab and Usage/Dashboard filters reset on reload. | Feature views | Add URL search params. |
| 33 | **One error page for the whole route.** There are no per-panel error boundaries and no `global-error.tsx`. | `app/` | Add a boundary around each tab panel and the evidence sheet. |
| 34 | **Changing the routing preset or fallback in Settings discards unsaved role-route edits.** | `SettingsView.tsx` (effect on `routing.data`) | Keep the drafts when only the preset or fallback changed. |
| 35 | **Small accessibility gaps.** The theme radiogroup has no arrow-key navigation, and status changes announced while browsing history are noisy. | `ThemeToggle.tsx`, `AskView.tsx` | Roving tabindex; announce only on live transitions. |
| 36 | **`/health` failures aren't shown**; the budget hint just disappears. | `AskView.tsx` | Show a small "limits unavailable" note. |
| 37 | **No delete paths.** Snapshots and runs can't be deleted and nothing is retained or cleaned up. The workspace quota lives in memory and `workspaces.retained_raw_bytes` is never written. | Backend | Delete endpoints, `ON DELETE CASCADE` on run children, and persisted byte accounting. |
| 38 | **Connector marks are drawn in the repo**, not official logos. | `components/ConnectorLogo.tsx` | Swap in official SVGs where brand guidelines allow. |
| 39 | **Leftovers:** unused `@radix-ui/react-tabs`, the unused `inspectorOpen` store field, `rounded-*` classes that render square, and `NEXT_PUBLIC_API_BASE` set at runtime (Next inlines it at build). | `package.json`, `lib/store.ts`, `AppShell.tsx`, `docker-compose.yml` | Remove them; use a relative `/api/v1` base through Caddy. |
| 40 | **`next/font` needs network access at build time.** | `app/layout.tsx` | Fine for Docker builds. For offline builds, vendor the font files and use `next/font/local`. |

## Data model notes (no immediate bug)

- Every JSON column is `JSON`, not `JSONB`, which rules out indexing or containment queries.
- Migrations are only exercised through SQLite `create_all` in tests. `FOR UPDATE`, `timestamptz` and migration 0002 are untested on Postgres.
- `conv_` plus a 64-character workspace id overflows `String(64)`, which pushes run creation onto the memory fallback.
- The first event sequence is 1 on the database path and 0 on the memory path.

## Tests and tooling still missing

- CI doesn't run Playwright or lint. There are no frontend unit tests (Vitest + Testing Library), and no ESLint config even though the code has `eslint-disable` comments.
- Backend tests that need Postgres: migration 0002 on a database with duplicate pins and stored `raw_base64`; concurrent routing saves; concurrent pin creates.
- End-to-end test: upload fixture → approve → ask → open evidence → pin → Dashboard re-run (covers P0 #2).

## Docs to correct after verification

- [02](02-specialists-do-not-query-data.md) says the tool loop is "Implemented in source". Until this round, every specialist step failed on an undefined `bridge` (now fixed in `orchestration_service.py`). Update it once a live run succeeds.
- The earlier audit reports were deleted; this file holds the current status.

---

## Audit status

Status of every item in the original frontend audit (ids C1–C7, H1–H3, M1–M16, L1–L18). Fixed means fixed in source and unverified.

| Audit id | Item | Status | Remaining work |
|---|---|---|---|
| C1 | Ask/Data defaulted to the oldest dataset | Fixed | — |
| C2 | Approval gate bypassable | Fixed (UI and API return 409) | — |
| C3 | Dropped stream stranded the run | Fixed (Reconnect, polling, Cancel kept) | #27 |
| C4 | History stale; leaving Ask dropped the run | Mostly fixed (server merge, `?run=`, resume on open) | #3 |
| C5 | Upload base64 sent three times | Partly fixed (now twice; rename doesn't re-upload) | #23 |
| C6 | CI fails without a lockfile | **Open** | #1 |
| C7 | No error boundaries | Partly fixed (`app/error.tsx`, `not-found.tsx`, safe formatters) | #33 |
| H1 | Connect-flow races | Fixed (request token) | — |
| H2 | No onboarding | Fixed (setup checklist, gated composer, journey nav order) | — |
| H3 | Dashboard dead end | Fixed (excerpt, dataset, newer data, Open, Re-run, Unpin) | #2 |
| M1 | Composer routing changed workspace policy | Fixed (now a link to Settings) | — |
| M2 | Settings saved on every blur | Fixed (explicit Save, validation, Discard) | #34 |
| M3 | Test used the saved key, not the typed one | Fixed ("Test saved key") | — |
| M4 | No deep links | Partly fixed | #32 |
| M5 | "New conversation" implied threads | Fixed by wording ("New question") | #31 |
| M6 | Approve without review | Fixed (banner, Definitions tab first, errors shown, cache invalidated) | #11 |
| M7 | Evidence dialog accessibility | Fixed (Radix dialog) | — |
| M8 | No focus ring on the composer | Fixed | — |
| M9 | Hidden scrollbars | Fixed (thin themed bars) | — |
| M10 | Error model lost status and hints | Fixed (`ApiError`, retry only on 5xx/network) | #19 |
| M11 | Immutable data refetched | Fixed (`staleTime: Infinity`) | — |
| M12 | Pending looked like done | Fixed (amber warn tokens) | — |
| M13 | Google Fonts at runtime | Fixed (`next/font`) | #40 |
| M14 | Client-side source identity and versions | **Open** | #8 |
| M15 | Unbounded lists | **Open** | #22 |
| M16 | History at the bottom on phones | Fixed (History toggle at the top) | — |
| L1–L18 | Polish items | Fixed except L4 (unused tabs dependency, #39), L5 (API base, #39), L6 radiogroup keys (#35), L14 tests in CI (see above), L16 health errors (#36), L17 single workspace (by design); clarification UI implemented, runtime verification pending | — |
| Security | CORS breadth, link hosts, credentials in memory | Credentials cleared after import; the rest open | #25, #29 |

---

## Verification checklist

1. Install Node 22 and Python 3.13, or use the Docker images.
2. `cd frontend && npm install && npm run typecheck && npm run build`, then commit `package-lock.json` (#1).
3. `cd backend && pip install -e ".[dev]" && pytest -v`. New tests are in `tests/test_new_connectors.py`; `tests/test_api_and_infra.py` was updated.
4. `docker compose up --build`: the `migrate` service applies `0002_integrity_and_indexes`. If a database was created without Alembic, run `alembic stamp 0001_initial_schema` first.
5. `npx playwright test` against the running stack.
6. Check by hand:
   - one quick and one deep run with a real provider key;
   - open evidence;
   - pin, then Re-run twice from the Dashboard (#2);
   - restart the API mid-run and check Ask's polling (#3);
   - an S3 pre-signed URL, a public and a private Google Sheet (#5), and a DuckDB file;
   - HubSpot, Stripe and GitHub with read-only tokens (#6);
   - MySQL `verify-full` (#7);
   - light and dark themes at 375 px and 1280 px.
