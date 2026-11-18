# 10 · SOLID review: findings and refactor plan

All backend and frontend files were reviewed against SOLID (Single responsibility, Open/closed,
Liskov substitution, Interface segregation, Dependency inversion). Small, contained fixes were made
directly (see "Already fixed" at the end). The items below are structural refactors. They are
safe only with tests running, so they are planned here rather than done blind.

Order: do **P1** first (they block testability and the rest), then P2, then P3.

## P1: Biggest offenders

### 1. `backend/app/api/main.py`: `create_app` is a >1,200-line god function (SRP, DIP)
It holds every route, process-local run and event state, SSE fan-out and locking, run execution and
cancellation, and the export format switch. Repository adapters now persist several records, but
the API still uses in-memory working state and fallback dictionaries. Routes also reach into
private `routing_service._api_keys`.

**Fix:**
- One `APIRouter` per area: `connectors`, `semantics`, `providers`, `routing`, `runs`, `pins`, `exports`, `health`.
- Make the existing Postgres repositories the source of truth and isolate volatile execution state
  behind a run service (see 07).
- Add an `EventBus` class (publish, subscribe, replay) that owns the lock and sequence numbering.
- Add a `RunService` for create, execute, cancel and binding resolution.
- Add a `CredentialStore` port and `RoutingService.set_api_key()` / `has_key()`, so no code touches `_api_keys`.
- Make idempotency a dependency.

**Test:** the existing API tests pass unchanged, plus one unit test per router with a fake service.

### 2. `orchestration_service.py`: service locator and concrete adapters (DIP, SRP, OCP)
It takes `container: Any` and reaches into `.routing_service`, `.budget_ledger` and
`.default_routing_policy`. It imports the `deep_agents_bridge` and `supervisor` adapters
directly. One class builds the plan, picks agent roles (by substring of `task.kind`), runs
specialists, emits events and decides the final status. It calls `datetime.now` instead of `Clock`.

**Fix:**
- Constructor-inject the `SpecialistRunner`, `Supervisor` factory, `BudgetLedger`, default policy and `Clock` ports.
- Add a `PlanBuilder` per mode.
- Make `TaskNode.kind` an enum and map it to `AgentRole` through a registry.
- Add an `EventEmitter` helper.

**Test:** run the orchestrator with fake ports and assert the event sequence and final status for quick, deep, failed and cancelled runs.

### 3. Frontend `DataView.tsx` (~900 lines) and `AskView.tsx` (~430 lines) (SRP, DIP)
Each mixes data fetching (inline `apiFetch`), a state machine and presentation.

**Fix:**
- API modules `lib/api/connectors.ts` and `lib/api/semantics.ts`.
- Hooks `useSourceFlow`, `useFileSource`, `useSemantics(snapshotId)` and `useRunSessions()`.
- Components `SourceForm`, `FileDropzone`, `TableSelector`, `StepProgress`, `DatasetPanel`, `ProfileTab`, `MappingTab`, `VersionsTab`, `Composer`, `AnswerCard`, `ExecutionTrace` and `ConversationList`.
- Reuse `useSnapshots` from `lib/workspace.ts` instead of a second query.

**Test:** Vitest plus Testing Library for each hook, with a mocked fetch.

## P2: Duplication that slows every change

4. **Seven provider adapters repeat the same HTTP code (SRP/DRY).**
   - What repeats: the key check, the `httpx` POST, `>= 400 → classify_http_error`, the structured-schema check, tool payload building, and JSON parsing.
   - Fix: an `_HttpModelAdapter` base, one `OpenAiCompatibleChatAdapter` (Groq, Kimi, NIM as config), and one `ResponsesAdapter` (OpenAI, xAI).
   - Move endpoint paths and the Anthropic API version into `ProviderProfile` (`providers.yaml`).
   - Make "raise only `ProviderError`" part of the `ModelGateway` contract (Liskov). That lets `routing_service` drop its catch-all.
5. **Five snapshot connectors repeat the batch/schema/manifest assembly loop.** Fix: a `SnapshotAssembler`.
   - Note: their internal `manifest.sha256` still covers table names only. The API now publishes a real content hash, but the manifest field should be fixed in the assembler too.
6. **Frontend UI primitives repeated with long class strings (OCP/DRY).** These are buttons, notices, status badges, cards, tables, skeletons, spinners and inputs. Fix: `components/ui/` (`Button`, `Notice`, `StatusBadge`, `Card`, `DataTable`, `Skeleton`, `Spinner`, `TextInput`, `Icon`).
7. **Settings, Dashboard and Usage call `apiFetch` inline (DIP).** Fix: hooks `useProviders`, `useSaveProviderKey`, `useTestProvider`, `usePins`, `useUsage`.

## P3: Smaller principle violations

8. **if/elif chains that should be registries (OCP):**
   - `SqlGlotQueryCompiler.compile` (isinstance chain): a per-type compiler map
   - REST pagination modes: a `PaginationStrategy` per mode
   - the export format switch in `main.py`: an exporter registry in `ExportService`
   - `statusTone` and the icon switches (frontend): one map each
   - `isFieldVisible` in `connectors.ts`: a `visibleWhen` on `FieldDef`
9. **Concrete registries imported by services (DIP).**
   - `routing_service` imports `ProviderRegistry`, and `ingestion` imports `ConnectorRegistry`. They should use `GatewayRegistry` and `ExtractorRegistry` protocols in `application/ports`.
   - `deep_agents_bridge` imports the concrete `RoutingService`. It should use a `StepExecutor` protocol.
10. **Private helper imported across modules (DIP).** `sheets_connector` and `xlsx_parser` import `csv_parser._infer_column_values`. Move it to a public `parsers/type_inference.py`.
11. **Globals instead of injection (DIP).** `DEFAULT_DATA_LIMITS` is read inside connectors and the DuckDB executor. `CredentialCipher` reads `APP_MASTER_KEY` itself. Both should be injected.
12. **Interface segregation.**
    - `configure_run` and `get_run_totals` are missing from the `BudgetLedger` port. Add a `BudgetAdmin` port.
    - `RoutingPolicyPort` was unused and has been deleted.
    - `ConnectorRegistry.register` forces discovery and extractor together.
13. **`LocalArtifactStore` mixes blob storage with quota accounting (SRP).** The quota lives in memory, so it resets on restart. Fix: a `QuotaLedger` port.
14. **`ExportService` imports `fpdf` and `docx` in the application layer (DIP).** Move the renderers to adapters behind an `Exporter` protocol.
15. **Frontend dependency direction.** `lib/routing.ts` and `lib/connectors.ts` import the `RichOption` type from `components/`. Move it to `lib/types.ts`. Unify the `"quick" | "deep"` union, which is defined in three places.

## Correctness and security findings

The table records findings from the original review; fixes made during this follow-up are listed immediately after it.

| Item | Where | Fix |
|---|---|---|
| PDF export turns ₹ and any non-Latin text into `?` (Latin-1 encoding) | `export_service.py` | Embed a Unicode TTF (e.g. DejaVu Sans) with `fpdf.add_font` and use it for all text |
| REST connector: DNS is checked, then httpx resolves again (DNS rebinding) | `rest_connector.py` | Connect to the validated IP with an explicit `Host`/SNI, or use a custom transport that pins the resolved address |
| SQLite and XLSX read all rows before the row-limit check (memory) | `sqlite_connector.py`, `xlsx_parser.py` | Stream with `fetchmany` / `iter_rows` and stop at `max_rows_per_dataset + 1` |
| Background stubs reported success without doing anything | `app/jobs/tasks.py` (`render_export_task`, `expire_clarifications_task`) | Deleted (along with the runner stub, its launcher and `Dockerfile.runner`) |
| `refresh_pin` returns a new run id but never starts a run | `main.py` | Create a real run from the pin's question (reuse `RunService`), or remove the endpoint |
| Compose started a dispatcher that held a lock but did not dispatch jobs | `jobs/dispatcher.py`, compose | The idle service and `dispatcher.py` were deleted; actual Celery enqueueing remains open (see 06) |
| Fallback SQL validator rejects columns named `set` and `load` | `sqlglot_compiler.py` | Not reproduced in current compiler-generated SQL: identifiers are quoted, and the fallback removes quoted text before scanning SQL keywords. Add regression coverage if raw SQL is introduced. |
| Tests are tied to one dataset | `tests/` | Add reconciliation tests for `amount`, `amount_usd` and custom `SettlementRules`. Make the end-to-end test assert the run used the uploaded snapshot. Replace "exactly 7 providers" with "contains these" |

### Fixed in source during the follow-up review

- PDF export registers Unicode TrueType fonts and no longer replaces non-Latin characters with `?`. The backend image installs DejaVu Sans; local installs need an available system font or `APP_UNICODE_FONT`.
- SQLite extraction reads at most `max_rows_per_dataset + 1` before rejecting an oversized table.
- XLSX parsing iterates rows and stops at the row cap instead of materializing a whole sheet first.
- REST requests now connect to the validated IP while retaining the original Host header and TLS SNI; REST extraction enforces the dataset row cap before accumulating excess records.
- Pin refresh now submits a real background run for the saved question.
- Export-rendering and clarification-expiry Celery stubs now fail explicitly instead of reporting success without work.
- The idle dispatcher service was removed from Compose; run execution is still not enqueued to Celery (see 06).

These source changes have not been run against tests or live integrations in this follow-up.

## Remaining hardcoded values (move to config or named constants)

- **Providers:**
  - endpoint paths, and the Anthropic API version `2023-06-01`;
  - the xAI tick divisor;
  - compaction thresholds `2000`, `8000` and `4096`;
  - the token estimate `len // 4`.
- **Connectors:**
  - Postgres/MySQL `allow_private_local_for_tests=True` by default (should be off outside dev);
  - connect and read timeouts;
  - REST retry counts and the 50-record discovery sample;
  - the Sheets 20-sheet cap (use `limits.max_workbook_sheets`);
  - the DuckDB `memory_limit='1GB'` and its 15 s timeout;
  - the compiler's `max_rows=1000`.
- **Error messages with literal numbers:** "40,000,000 bytes", "200 columns", and the format list. Generate them from `DataLimits` and the parser registry instead.
- **Orchestration:** the fixed plan (task ids, objective templates, 120- and 100-character truncation), English task titles, and the `"setup_required"` sentinel.
- **Infra:**
  - DB name `analytics_poc` and user `postgres`, repeated in compose and the backup scripts;
  - `NEXT_PUBLIC_API_BASE` set at runtime, though Next bakes it in at build time;
  - every model's `verification_date` is the same date.
- **Frontend:**
  - the API base fallback `http://127.0.0.1:8080/api/v1`;
  - the Playwright URL, which should use `baseURL`;
  - the reconnect limit `5`, the usage refresh `15000` ms, and the 1100 px rail breakpoint;
  - the fixed Postgres and MySQL ports, which mirror the server policy and should be exposed by `/health`.

## Already fixed in this pass

- **Reconciliation and profiling** no longer assume INR, `amount_inr` or `order_id`.
- **Runs** bind to a real snapshot; the `snap_default` / `sem_default` placeholders are gone.
- **Secrets:** no default runner token or Postgres password. Compose loads `.env`, and CORS origins come from the environment.
- **Frontend configuration:**
  - every path uses the active workspace instead of `default`;
  - providers, budgets and the upload limit come from the API.
- **New in this pass:**
  - the DuckDB timeout is now enforced (`conn.interrupt` on a timer);
  - period comparisons use `ELSE NULL`, so MIN/AVG/COUNT are correct;
  - the snapshot `sha256` hashes the actual data (`schema_fingerprint` is kept separately);
  - `CircuitBreakerManager` is thread-safe;
  - budget fallbacks come from `DEEP_BUDGET_LIMITS`.
- **Fakes and gaps closed:**
  - exports without a run no longer invent a fake "Exported claim";
  - deepagents no longer adds file tools that bypass the tool allowlist (the deepagents, LangGraph and LangChain dependencies have since been removed entirely).
- **Routing and compose:**
  - `grok-3-mini`, `kimi-k3` and the NVIDIA model are now in role routes;
  - provider API keys are passed to the `api` and `worker` containers;
  - the routing hook URL-encodes the workspace id.
- Legacy comments and docstrings were removed across backend and frontend sources. Only pragmas and the new source-ID storage fallback note remain: `# noqa`, `# type: ignore`, `eslint-disable`.
