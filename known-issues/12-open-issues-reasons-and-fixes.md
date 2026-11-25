# 12 - Fixes and improvements still needed (current list)

Last checked 2026-11-18. This is the current list of what the project still needs. For every item it gives the reason and how to fix it. [11](11-open-fixes-and-audit.md) keeps the audit history and the status of each original audit item; item numbers #1–#40 match it, and #41 onward were found in the latest check.

**The latest Celery worker and orchestration changes have not had a live-stack run yet.** Source-level checks do not replace the PostgreSQL/Redis worker, restart, cancellation, and SSE checks in the verification checklist.

| Priority | Meaning |
|---|---|
| P0 | Blocks release |
| P1 | Wrong results or lost data |
| P2 | Performance, scale, security or build hygiene |
| P3 | UX, polish and code health |
| Improvement | New capability or a better experience; not a defect |

The **UX** tag marks items that users directly notice.

## Done since the previous version of this list

- **Release blockers (2026-11-24):**
  - The Data page is back. An unanchored `data/` rule in `.gitignore` had kept `frontend/src/app/data/` and `frontend/src/features/data/` out of git, so `/data` returned 404 and nothing could be imported. The rule is now anchored to the repo root, the page was rebuilt (catalog, connect form, table selection, import, and Definitions/Profile/Preview/Versions/Diagnostics tabs), and CI fails if any committed file matches an ignore rule.
  - Lock files are committed (#1, [05](05-python-dependency-lock.md)): `frontend/package-lock.json` plus hash-pinned `backend/requirements.lock` and `requirements-dev.lock`. CI and Docker install from them.
  - Deep runs no longer crash on a second replan. The default plan plus two replans needed 7 specialist instances against a limit of 6, and the planner could propose 8 tasks; the resulting `BudgetExhaustedError` escaped the graph and failed the run. Deep runs now allow 8 instances, `RunBudgetLimits.planner_task_limit` reserves room for every replan (checked at construction), replans are skipped when instances or model calls can't cover them, one model call stays reserved for the composer, and an exhausted budget cancels pending tasks with a limitation instead of failing.
  - CI now runs `ruff`, the Playwright suite, and `mypy` as advisory (#43, partly). `make test` uses `pytest`.
  - Fonts are self-hosted from pinned `@fontsource-variable` packages, so `next build` makes no network calls (an intermittent `next/font/google` crash had failed CI builds).
  - Every test has a 120 s timeout (`pytest-timeout`) and every CI job a `timeout-minutes`, after a test race hung CI for 25+ minutes: `test_fastapi_health_providers_and_run_idempotency` used a non-context `TestClient`, so each request ran in a short-lived event loop that destroyed the run's background task.
- **Indian finance imports (2026-11-24):**
  - CSV decoding detects UTF-8/UTF-16 byte-order marks, then tries UTF-8, cp1252 and latin-1.
  - CSV, TSV, XLSX, Google Sheets and REST imports share one inference module (`parsers/inference.py`). Amounts such as `₹1,23,456.78`, `Rs. 1,234`, `INR 1,234.50`, `-₹500` and `(1,234.00)` become decimals with `INR` currency metadata; Indian and Western digit grouping are validated; leading-zero ids stay text; numbers too large for decimal128 stay text.
  - ISO and day-first (`dd/mm/yyyy`, `dd-mm-yyyy`, `dd.mm.yyyy`) dates become `date32`, date-times become timestamps. Two-digit years are not inferred. A column with both day>12 and month>12 values stays text; all-ambiguous columns are read day-first (no warning is recorded yet).
  - XLSX files with printer-settings parts are accepted; macros, ActiveX, embedded objects, external links and other binary parts are still rejected.
- **Privacy and concurrency (2026-11-24):**
  - `allow_raw_record_egress` is enforced: detail-query row values reach the model only when every provider the routing policy allows has it enabled (all default to off). The model gets columns, row count and the evidence id; the rows stay in local evidence.
  - Snapshot versions are allocated on the server (#8).
  - In Celery mode the SSE endpoint polls the event log every second instead of every 15 s, off the event loop thread.
- **Wrong results and lost data (2026-11-24):**
  - Database errors are reported (#9). Writes (runs, datasets, pins, provider keys, routing policies) and reads (runs, pins, usage, providers, routing, queue counts) return `503 persistence_unavailable` with a remediation instead of reporting success or answering from memory; memory caches update only after the commit. A run whose failure can't be recorded still sends `run.finished`.
  - `DATABASE_URL` is required (#28). The API fails at startup without it unless `TESTING=1`; tests use a throwaway file-backed SQLite database so concurrent requests no longer share one connection.
  - One routing-policy loader (`routing_policy_from_saved`) serves the API, the Celery worker and the Settings screen (#10). A run fails closed with a clear error if the cost policy can't be loaded, and a removed cost ceiling now stays removed after a restart instead of showing the default.
  - Cut-off results are flagged (#14). The compiler asks for one row past the limit and the executor sets `truncated`; the model is told that totals over the returned rows are incomplete.
  - Query arguments are checked (#18). Comparison filters need a single value; `compare_periods` takes ISO dates or date-times (no time zone offsets), rejects reversed periods, uses `< end + 1 day` for date-only ends, binds typed values, and compares text date columns through `TRY_CAST(... AS TIMESTAMP)`. Before this, it failed on real timestamp columns.


- **Quick fixes:**
  - Pin refresh runs every time (#2).
  - Interrupted runs are closed at startup, and Ask stops polling stale runs (#3).
  - Links that return a web page are rejected (#5).
  - Malformed tool calls go back to the model (#17).
  - Citation filtering uses the whole run's evidence (#20).
  - Links in answers show their host (#29).
  - Settings keeps unsaved edits (#34).
  - Health-check failures are shown (#36).
- **Clean-up:**
  - Added `.gitignore` and `.dockerignore`.
  - `NEXT_PUBLIC_API_BASE` is now a build argument.
  - At that point, the Celery worker was opt-in; the 2026-11-18 worker integration now starts it in Compose by default.
  - Placeholder password removed from `alembic.ini`.
  - Removed: the runner stub and its launcher and image, the heartbeat endpoint and its token, placeholder Celery tasks, the dispatcher, the unused LangGraph topology and Deep Agents harness helpers, `RoutingPolicyPort`, an unused fixture and `requirements.txt`, and stale docs.
- **Small clean-ups (#41, #42, #45, #47):**
  - Removed 9 unused Python dependencies (the 8 listed in #41 plus `pydantic-settings`).
  - Redis now starts only with the `worker` profile; the API no longer gets `REDIS_URL` or waits for Redis.
  - `langgraph_scheduler.py` was renamed to `supervisor.py`.
  - `GatewayChatModelBridge` is a plain class with only `invoke_messages`.
- **Worker integration (2026-11-18):** Background runs now enqueue to Celery by default in Compose. The worker reloads persisted run context and writes status, evidence, answer, and ordered events to PostgreSQL. API SSE polls the shared event log, cancellation is shared, and SQLite is rejected for Celery mode. See [06](06-celery-worker-and-dispatcher.md); transactional outbox and live-stack checks remain.
- **Official logos (#38, mostly):** 16 connector brands now use their official mark from Simple Icons 16.35.0 (CC0), vendored in `frontend/src/components/brandPaths.ts`. Datasets imported from SQLite and DuckDB files show those logos too. The LLM providers in Settings show official marks for Anthropic, Gemini, Kimi and NVIDIA.

---

## P0: blocks release

### #1 No frontend lockfile (fixed)
- **Reason:** CI runs `npm ci`, which refuses to run without `package-lock.json`, so the frontend job always fails. The Docker image uses `npm install`, so builds can resolve different versions.
- **How to fix:** Install Node 22, run `cd frontend && npm install`, and commit `package-lock.json`. In `infra/compose/Dockerfile.frontend`, switch `npm install` to `npm ci` (the `COPY frontend/package*.json` line already picks up the lockfile).

### #4 Nothing has been executed (partly fixed)
- **Status:** The backend suite, ruff, frontend typecheck, build and Playwright run in CI. On 2026-11-24 the stack ran end to end outside Docker: PostgreSQL 16 (migrations 0001–0005, including the 0005 de-duplication, downgrade and re-upgrade, and `alembic check` against the ORM), Redis, the API in Celery mode and a Celery worker, with the Kimi chat adapter pointed at a local OpenAI-compatible mock. Import, approve, a quick run through the worker, SSE, evidence, pinning and an API restart all worked. On 2026-11-25 cancellation and a worker crash were checked on the same stack with a slowed mock: cancel returns `cancelled`, the worker stops making model calls and `run.finished` is sent; after `kill -9` of the worker mid-run, a fresh worker picked the run up and completed it once the Redis visibility timeout passed. That timeout used to be Celery's one-hour default, so a crashed worker left a run stuck for an hour; it's now the deep-run active budget plus 5 minutes (`RUN_REDELIVERY_SECONDS` overrides it). Still unverified: `docker compose` itself (no Docker daemon was available), deep runs against a real provider, and real provider responses.
- **Reason:** No typecheck, build, test or migration has run on any of the recent changes, so bugs may be hidden.
- **How to fix:** Run the verification checklist at the end of this file.

---

## P1: wrong results or lost data

### #6 API imports store every value as text (UX) (fixed)
- **Status (2026-11-24):** Fixed. REST records go through the shared column inference in `parsers/inference.py`; nested objects and lists are stored as JSON text and booleans as `true`/`false`.
- **Reason:** The REST connector converts every value with `str()`. Stripe amounts, HubSpot numbers and GitHub counts can't be summed, and nested objects become Python text like `{'a': 1}`.
- **How to fix:**
  1. In `rest_connector.py` `extract`, stop calling `str()` on every value.
  2. Move `_infer_column_values` from `csv_parser.py` into a public `parsers/type_inference.py` and use it to infer column types.
  3. Serialize dict and list values with `json.dumps`, and optionally flatten one level (HubSpot `properties`).
  4. Add a test.

### #7 MySQL TLS and the DuckDB import are unverified
- **Reason:** MySQL TLS passes an `ssl.SSLContext` to `pymysql.connect(ssl=…)`. The DuckDB connector relies on the `duckdb_tables()`, `duckdb_views()` and `duckdb_columns()` column names in DuckDB 1.1.3. Neither has been run.
- **How to fix:** Run `tests/test_new_connectors.py`, then make one `verify-full` connection to a managed MySQL. If pymysql rejects the context, pass `ssl={"ca": certifi.where()}` with `ssl_verify_identity=True` instead.

### #8 Snapshot version numbers are chosen by the browser (fixed)
- **Status (2026-11-24):** Fixed. The API derives the source id from a hash of the family and redacted config (or uses an explicit `source_id`) and reserves the version before extraction with an atomic `UPDATE sources SET last_version = last_version + 1 … RETURNING`. Migration `0005_snapshot_version_allocation` adds the counter, renumbers existing duplicates and makes `(source_id, version)` unique. The extract request no longer accepts a version.
- **Reason:** The browser picks the next version and keeps source identity in `sessionStorage`. Two tabs can create the same version, and a new browser session starts a new source, which also breaks the Dashboard "Newer data" flag.
- **How to fix:**
  1. In `extract_connector`, resolve the source from its redacted config and compute `max(version) + 1` on the server inside a transaction that locks the source row (`SELECT … FOR UPDATE`).
  2. Add a migration that de-duplicates, then adds a unique constraint on `(source_id, version)`.
  3. Remove the version and `sessionStorage` logic from `ConnectSource.tsx`.

### #9 Database errors silently fall back to memory (fixed)
- **Reason:** `main.py` catches database exceptions and keeps snapshots, runs, pins, provider keys and events only in memory, then reports success. That data disappears on restart. `update_status` at run start sits outside the `try`, so a database error kills the task and no `run.finished` is sent.
- **How to fix:** Make Postgres the source of truth: return 503 on database errors, update memory caches only after the commit, and wrap the run-start status update in the task's error handling.

### #10 The saved cost limit is skipped after a restart (fixed)
- **Reason:** Runs read only `app.state.routing_policies`, which stays empty after a restart until someone calls `GET /routing`. Pin refreshes and API clients run with no cost ceiling.
- **How to fix:** At run start, load the policy through `app.state.repo_routing.get_policy(workspace_id)`, reusing the conversion in `update_routing_policy`, and cache it.

### #11 Approving definitions has no lock and overwrites history (fixed)
- **Status (2026-11-24):** Fixed. Approval inserts a new semantic-version row (the proposed one stays as history) and migration `0006_semantic_version_history` makes `(snapshot_id, version)` unique, so a concurrent second approval of the same version gets 409. Alembic now ignores tables it doesn't manage (the LangGraph checkpoint tables), so autogenerate won't propose dropping them.
- **Reason:** `approve_semantics` reads, checks and writes without a lock, so two concurrent approvals can both succeed. It also edits the row in place, so a run's `semantic_version_id` doesn't show which definitions it used.
- **How to fix:** Use `UPDATE … WHERE snapshot_id=:s AND version=:expected RETURNING *` and return 409 when no row comes back. Insert a new semantic-version row on approval.

### #12 Clarification resume (implemented; runtime verification pending)
- **Previous reason:** Clarification revisions and answers weren't stored, so a run could not resume after a restart.
- **Current source:** The prompt, revision, answer, and plan are stored in `runs.orchestration_json`; submission is revision-checked in a transaction and reschedules the same run. Verify inline and Celery resume, stale submissions, and cancellation while paused.

### #13 Durable evidence persistence (implemented)
- **Status:** Evidence records now have a PostgreSQL table and repository. The API and Celery worker persist evidence, and evidence endpoints read the durable records. `app.state.evidence` remains a cache/fallback.

### #14 Results never say they were cut off (UX) (fixed)
- **Reason:** The compiler caps `LIMIT` at 1000 and the executor checks against the same cap, so `truncated` is never true. Users and the model treat "1,000 rows" as the total.
- **How to fix:** Compile `LIMIT n+1`, set `truncated = len(rows) > n`, and drop the extra row. Optionally add `COUNT(*) OVER()` for the true total.

### #15 A step can end with no answer text (UX) (fixed)
- **Status (2026-11-24):** Fixed. Both runtimes take the answer-only final round when the run's remaining tokens are below twice the next call's reservation (estimated input plus output tokens); `RoutingService.tokens_left()` reads the ledger. The gateway now reuses `estimate_input_tokens` instead of its own copy.
- **Reason:** The final-round decision ignores the token budget, so a step can run out of tokens and show only "stopped early".
- **How to fix:** In `orchestration_service.py` (`final_round`), also take the final round when the tokens left are below about twice the estimated next call, and keep a reserve for that call.

### #16 Very wide spreadsheets hide every column from the model (fixed)
- **Status (2026-11-24):** Fixed. `inspect_schema` shrinks in stages (full, then `name:type` columns, then table names with column counts) and accepts an optional `table_id`.
- **Reason:** `inspect_schema` output is capped at 64 KB and can only trim rows, which a schema doesn't have.
- **How to fix:** Shrink in stages (fewer fields per column, then table names only), and add an optional `table_id` argument.

### #18 Loose query argument checks (fixed)
- **Reason:** A comparison filter without a value binds NULL and silently returns 0 rows. `compare_periods` accepts any string as a date, and its inclusive end drops most of the last day for timestamps.
- **How to fix:** Require `value` for comparison operators, parse ISO dates, and use a half-open `< end + 1 day` bound.

### #19 Raw provider errors are sent to the browser (fixed)
- **Status (2026-11-24):** Fixed. Provider response bodies are logged on the server and no longer embedded in `ProviderError` messages. Step, tool and run failures go through `public_message()`: our own domain errors keep their text, anything else becomes a generic message that points to the server log. A structured `{code, message, remediation}` payload is still open.
- **Reason:** `task.status.error`, `tool.result.error` and `run.finished.error` carry raw exception text and parts of provider responses. The UI hides it, but it is still in the event log and the API.
- **How to fix:** Send `{code, message, remediation}` built from `DomainError`, and log the raw text on the server only.

### #21 The "reset answer" update is never sent (closed)
- **Status (2026-11-24):** Closed by removing the hook. Adapters don't stream, and answer text is emitted only after a step finishes, so a failed attempt never shows partial text and the routing-level `on_answer_reset` callback had nothing to reset. It was removed as dead code. The composer's `answer.reset` (replacing step findings with the final answer) is unchanged. Wire a per-draft reset if token streaming is added.
- **Reason:** `RoutingService.on_answer_reset` is never passed in. If wired up as is, its `drf_…` ids wouldn't match `draft_<task_id>`, and the UI would clear the whole answer.
- **How to fix:** Wire the callback with the task's draft id, and make `AskView.tsx` replace only that step's text.

---

## P2: performance, scale, security and build hygiene

### #22 Lists load everything (UX) (fixed)
- **Status (2026-11-24):** Partly fixed. `GET /runs` is keyset-paginated (`limit` default 200, max 500, `before` cursor, `next_before` in the response), usage reads at most the newest 500 runs, and `list_snapshots` loads the latest semantics for all snapshots in one query. The run list now carries only each answer's `answer_id` and `summary_markdown` (the full answer stays on `GET /runs/{id}`). `/usage` takes `since` (the Usage page sends its time window, rounded to the hour) and loads ledger totals for all listed runs in one grouped query instead of one query per run. Ask history has a "Load older questions" button that follows `next_before`.
- **Reason:** `/runs` returns every run with its full answer (used by Ask and Dashboard), `/usage` returns every run every 15 s, and `list_snapshots` runs one query per snapshot.
- **How to fix:** Keyset pagination (`?limit=&before=`), a summary response without `answer_json`, a lateral join for semantics, a server-side time filter for `/usage`, and "Load more" in the UI.

### #23 Uploads are sent twice (UX) (fixed)
- **Status (2026-11-25):** Fixed for uploaded files. `POST /workspaces/{ws}/uploads` streams the raw body (size-checked while reading) into a workspace-scoped staging area and returns an `upload_id`; probe, discover and extract reference it, it's deleted after a successful import, and unused uploads are swept after 24 hours. The Data page uploads once and no longer base64-encodes files. `raw_base64` still works for API clients. Remote links: the connector keeps the discover download (keyed by URL and bearer token, at most 4, for 10 minutes) and the next extract of that link consumes it, so a link is downloaded once per import and the snapshot matches the preview.
- **Reason:** Files travel as base64 JSON (about 1.33× their size) to both discover and extract, and links are downloaded twice.
- **How to fix:** `POST /uploads` (multipart) returns an `upload_id` that discover and extract use. Cache link downloads per job.

### #24 Blocking database calls stall live answers (partly fixed)
- **Status (2026-11-24):** Partly fixed. Approve, routing save, pin create and provider-key save are plain `def` endpoints (run in the thread pool); run creation and the SSE endpoint's database calls run in `asyncio.to_thread`; PostgreSQL sessions set `lock_timeout=5s` and `statement_timeout=30s`. `clarify_run` and `cancel_run` stay async (they schedule or cancel tasks) and now run their lookups and writes in `asyncio.to_thread`, except the cancel status write, which runs under the event lock and stays synchronous so other coroutines can't re-enter that lock. Still open: inline-mode event persistence happens under the global event lock.
- **Reason:** Several `async def` endpoints make synchronous SQLAlchemy calls, and events are persisted under the global lock with no `lock_timeout`.
- **How to fix:** Use plain `def` endpoints or `asyncio.to_thread`, persist events outside the lock, and set `lock_timeout` and `statement_timeout`.

### #25 Any localhost app can call the API (authentication)
- **Reason:** `allow_origin_regex` accepts every `localhost` / `127.0.0.1` port.
- **How to fix:** Allow exact origins only, and require a per-install token header on mutating routes.

### #26 Provider key handling (partly fixed)
- **Status (2026-11-25):** `APP_MASTER_KEY` accepts a comma-separated list (newest first, read by `MultiFernet`), `make rotate-keys` (`python -m app.jobs.rotate_credentials`) re-encrypts saved keys with the newest one, and a saved key that can't be decrypted is reported as `key_status: needs_key` ("Needs a key" in Settings) instead of Ready. Still open: per-workspace key lookup, which belongs with the multi-workspace/authentication work.
- **Reason:** There is one Fernet key with no rotation, a decrypt failure still shows "Ready", and keys from all workspaces share one dictionary.
- **How to fix:** `MultiFernet` plus a re-encrypt command, report decrypt failures as "Needs a key", and look up keys per workspace.

### #27 Many live streams can starve the browser
- **Reason:** Each running answer holds an `EventSource`, and HTTP/1.1 allows about 6 connections per host.
- **How to fix:** Serve over HTTP/2 (TLS in Caddy), or multiplex a workspace's runs on one stream.

### #28 A missing database setting only warns (fixed)
- **Reason:** Without `DATABASE_URL` the API uses throwaway in-memory SQLite.
- **How to fix:** Fail at startup unless `TESTING=1`, and set `TESTING=1` in tests and CI.

### #43 CI doesn't lint, type-check or run end-to-end tests (fixed)
- **Status:** `ruff` and Playwright run in CI and `make test` uses `pytest`. `mypy app` (strict) passes and blocks CI; untyped third-party packages are listed in `ignore_missing_imports` overrides, and `app.api.main` sets `warn_return_any = false` because Starlette's `app.state` is untyped.
- **Reason:** `.github/workflows/ci.yml` runs only `pytest` and the frontend typecheck and build. `ruff` and `mypy` are configured in `pyproject.toml` but never run, Playwright isn't run, and the Makefile `test` target uses `unittest` while CI uses `pytest`.
- **How to fix:** Add `ruff check app tests` and `mypy app` to CI; start mypy non-blocking because `strict = true` will flag existing code. Add a Playwright job that starts the Compose stack. Switch `make test` to `pytest -v`.

---

## P3: UX, polish and code health

### #30 Clarification UI (implemented; runtime verification pending)
- The Ask view now displays `clarification.required`, submits the answer with its revision, and follows the resumed run. Verify page reload, stale revisions, and both execution modes (see [08](08-frontend-gaps.md)).

### #31 No follow-up questions (UX)
- **Reason:** Every run shares one conversation id per workspace and gets no earlier context.
- **How to fix:**
  1. Accept an optional `conversation_id` in `CreateRunRequest`.
  2. Pass the earlier questions and summaries (trimmed) to the orchestrator.
  3. Group history by conversation, and add a "Follow up" box under an answer.

### #32 Only the run and dataset are in the URL (UX)
- **Reason:** The Data tab and the Usage and Dashboard filters reset on reload and can't be shared.
- **How to fix:** Sync them to URL search params with `history.replaceState`, as `?run=` and `?dataset=` already do.

### #33 One error page for the whole route (UX)
- **Reason:** A rendering error in one tab replaces the whole page.
- **How to fix:** Add a small `ErrorBoundary` around each Data tab panel, the Ask answer card and the evidence sheet, and add `app/global-error.tsx`.

### #35 Small accessibility gaps (UX)
- **Reason:** The theme radiogroup has no arrow-key navigation, and Ask announces "Answer ready" when you merely open an old run.
- **How to fix:** Roving tabindex for the radiogroup; announce only status changes while streaming.

### #37 Datasets and runs can't be deleted (UX) (mostly fixed)
- **Status (2026-11-25):** `DELETE /runs/{id}` (refused with 409 while the run is active) removes the run with its events, evidence, budget reservations, pin and LangGraph checkpoints. `DELETE /snapshots/{id}` removes the snapshot, its semantic versions and its table artifacts and releases the workspace byte reservation; it returns 409 when pinned answers use the dataset unless `force=true`. Runs that used a deleted dataset stay readable. Data and Ask have Delete buttons with confirmation. Still open: persisting the byte accounting in `workspaces.retained_raw_bytes` (it's in memory).
- **Reason:** Old imports and failed runs pile up, and the storage quota lives in memory.
- **How to fix:** `DELETE /snapshots/{id}` (warn if pins use it) and `DELETE /runs/{id}`, `ON DELETE CASCADE` on run children, persisted byte accounting in `workspaces.retained_raw_bytes`, and Delete actions with confirmation.

### #38 AWS, Azure, OpenAI, Groq and xAI still use drawn marks (UI)
- **Reason:** Simple Icons doesn't carry these brands. The official AWS Architecture Icons and Azure icons can't simply be added either: their terms allow only architecture diagrams, training and documentation ("Microsoft reserves all other rights"; AWS Trademark Guidelines §3(d) require prior written permission for other uses).
- **How to fix:** Ask AWS and Microsoft for written permission to show their logos as connector logos, and use OpenAI's, Groq's and xAI's published brand assets if their guidelines allow product use. Once permitted, add the official SVG path as a `brand` entry in `ConnectorLogo.tsx`. Until then the drawn marks stay.

### #39 Remaining clean-up (UI hygiene)
- **Reason:** `rounded-*` classes render square, and the API base URL is absolute.
- **How to fix:** Drop the `rounded-*` classes, and use a relative `/api/v1` base through Caddy.

### #40 Fonts need network at build time (fixed)
- **Status (2026-11-24):** Fixed. Fonts come from pinned `@fontsource-variable` packages through `next/font/local`.
- **Reason:** `next/font/google` downloads fonts during `next build`, so offline builds fail.
- **How to fix:** Vendor the font files into `frontend/src/fonts/` and use `next/font/local`.

### #44 Unused chart recommender (new)
- **Reason:** `recommend_chart_candidates` in `application/services/profiling.py` is never called, and every answer is built with `charts=()`.
- **How to fix:** Use it for the "charts in answers" improvement (I1), or delete it.

### #46 Older known-issue files are out of date (new)
- **Reason:** [08](08-frontend-gaps.md) describes the UI before the redesign, and [09](09-missing-tests.md) says Playwright covers only navigation; it now also covers the connector catalog and the 404 page.
- **How to fix:** Update both, or reduce them to pointers to this file.

---

## Improvements

### I1 Charts in answers and pins (UX)
- **Reason:** The Dashboard promised "validated charts", the domain has `Answer.charts`, and Recharts is already a dependency, but answers are text only.
- **How to fix:** When a query returns a time series or category breakdown, build a chart spec from that evidence (using `recommend_chart_candidates`) and store it in `Answer.charts`. Render it in the Ask answer card and on Dashboard pins, linked to its evidence id.

### I2 Export from the UI (UX)
- **Reason:** `POST /exports` already renders CSV, HTML, PDF and DOCX with formula-injection protection, but no button calls it.
- **How to fix:** Add an "Export" menu to the answer header and pin cards that posts to `/exports` and opens the returned `download_url`.

### I3 Re-import a dataset (UX)
- **Reason:** Refreshing a source means re-entering every field.
- **How to fix:** Keep the redacted config per source (already stored), and add "Import new version" in Data that pre-fills it and asks only for secrets. Do #8 first.

### I4 Automatic re-run of pins on new data (UX)
- **Reason:** Dashboard shows "Newer data", but the user has to click Re-run.
- **How to fix:** An optional per-pin setting that re-runs automatically when a newer approved snapshot of the same source appears, with the latest result shown on the card.

### I5 Usage by model and provider (UX)
- **Reason:** Usage shows totals per run only.
- **How to fix:** Use the persistent budget ledger ([07](07-in-memory-state.md)), expose attempt rows (model, role, tokens, cost, latency), and add model and provider filters and charts.

### I6 "Try with sample data" for first-time users (UX)
- **Reason:** New users must find their own data before they can see the product work.
- **How to fix:** A button on the Ask setup checklist and the Data page that imports `fixtures/inr_acceptance/*.csv` as a ready-made dataset and suggests a few example questions.

### I7 Typed API client generated from the backend
- **Reason:** Frontend types are hand-written per screen and can drift from the API.
- **How to fix:** Generate types from `/api/openapi.json` with `openapi-typescript`, and use them in `lib/api`.

### I8 Split `backend/app/api/main.py`
- **Reason:** One file of about 70 KB holds every route, state and run execution, which makes changes risky.
- **How to fix:** FastAPI routers per area (connectors, snapshots, runs, pins, settings, usage) plus a run service. See [10](10-solid-refactors.md) §1.

### I9 Frontend tests and linting
- **Reason:** There are no unit tests and no ESLint, even though the code has `eslint-disable` comments.
- **How to fix:** Add ESLint (`next/core-web-vitals` + `jsx-a11y`), Vitest + Testing Library for `sse.ts`, `connectors.ts` (`toPayload`, `toSheetsCsvUrl`) and the snapshot sorter, and an axe accessibility check in Playwright.

### I10 Observability
- **Reason:** There are no request ids in logs and no tracing (the unused OpenTelemetry packages were removed).
- **How to fix:** Add structured JSON logs with the request id and run id. If tracing is needed, add OpenTelemetry back as a dependency and instrument FastAPI and the provider calls.

---

## Suggested order

| Order | Items | Effort |
|---|---|---|
| 1 | #1, #4 | Setup and running the checklist |
| 2 | #43 | Add lint, type-check and Playwright to CI |
| 3 | #10, #12, #28, #18 | About 30–60 minutes each |
| 4 | #14, #33, #35, #32, I2 | Under an hour each |
| 5 | #6, #15, #16, #21, I6 | 1–2 hours each |
| 6 | #9, #11, #8, #13, #22, #23, #24 | Larger backend work |
| 7 | #31, #30, #37, I1, I3, I4, I5 | Feature work |
| 8 | #25, #26, #27 | Security and transport decisions |
| 9 | #38, #39, #40, #44, #46, I7–I10 | Anytime |

## Verification checklist

1. `cd frontend && npm install && npm run typecheck && npm run build`, then commit `package-lock.json`.
2. `cd backend && pip install -e ".[dev]" && pytest -v`.
3. `docker compose up --build`: migration `0002_integrity_and_indexes` must apply.
4. `npx playwright test` against the running stack.
5. Check by hand:
   - one quick and one deep run with a real key;
   - open evidence;
   - pin, then Re-run twice;
   - restart the API mid-run;
   - import an S3 link, a public Google Sheet and a DuckDB file;
   - import from HubSpot, Stripe and GitHub;
   - connect to MySQL with `verify-full`;
   - both themes at 375 px and 1280 px.
