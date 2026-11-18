# 08 - Remaining frontend and API gaps

Several items in the earlier list are already present: Data shows snapshot schema, metric mapping and approval; it lists versions among snapshots with the same source id; `GET /runs` and `POST /pins` exist; and Dashboard reads saved pins. This pass added bounded snapshot preview and column profile endpoints plus Data tab rendering, Ask run history and pin controls, and routing controls. The rows below describe remaining work and runtime checks.

| Area | Current state | Fix needed | Main owner |
|---|---|---|---|
| **Data - Profile** | Endpoint and UI show null/distinct/duplicate counts and min/max alongside schema metadata. The UI labels sampled statistics when the snapshot is sampled. Profiles are computed across the full extracted artifact. | Verify the API and UI with fixtures; measure cost on large tables and sample or optimize the profile calculation if needed. | Verify / performance |
| **Data - Preview** | Endpoint and UI return at most `max_preview_rows` for a selected table and show an empty-table state. The query engine streams the artifact and decodes only enough Arrow batches to fill the preview (unless the table is already cached). | Verify row/byte behavior, empty tables, decimals/dates, and old snapshots without queryable artifacts; measure the remaining profile scan cost on large tables. | Verify / performance |
| **Data - Diagnostics** | Snapshot timestamps, completeness, sampling, consistency, source summary, row and byte totals, and a warnings list are now stored and shown in Data. Existing connectors do not yet emit warning records, and old snapshots have no diagnostics payload. | Generate meaningful connector warning records; verify new and legacy snapshot states and the displayed extraction facts. | Backend + frontend |
| **Data - Mapping** | Implemented: semantic definitions load and can be approved with an expected version. | No missing feature identified in static review; verify approval and stale-version handling in runtime tests. | Verify |
| **Data - Connector catalog** | The picker now exposes seven connection choices across eight backend families, including Airtable PAT and BigQuery service-account imports. It shows provider marks, auth/setup guidance, the import flow, and snapshot isolation limits. Source IDs are now namespaced by workspace. | Run frontend typecheck/build and backend tests; exercise Airtable and BigQuery with restricted credentials. No live integration has been verified. | Verify |
| **Data - Versions** | Implemented for snapshots sharing a `source_id`. A hashed source key maps to the server id in `sessionStorage`, so the mapping survives page reloads in the same browser tab session. | Verify reload behavior. A new browser session can still create a new source; move source identity resolution to durable server state if cross-session continuity is required. | Verify / backend |
| **Ask - Model override** | The nonexistent model selector was removed. `CreateRunRequest` does not accept `model_id`. | No fix required for the current UI. If manual selection is wanted later, add a validated optional model id to the API and expose enabled models from `GET /providers`. | Deferred |
| **Ask - Clarification** | The planner can pause a run for missing information. Prompt, revision, answer, and plan state are persisted; Ask displays the prompt and resumes inline or Celery execution with revision checks. | Verify reload, stale revisions, cancellation while paused, and resume in both execution modes. | Verify |
| **Ask - History** | Ask loads saved runs and answers into Recent from `GET /runs`; selecting a saved run now replays its event trace and reconnects if it is still active in this API process. Persisted `running` statuses can be stale after an API restart until worker ownership is durable. | Verify reload and replay behavior. Reconcile stale `running` statuses with durable worker ownership (see 06/07). | Verify / backend |
| **Ask - Pin answer** | Implemented: completed runs can be pinned from Ask; Dashboard reads the saved pins. | Verify the new action and duplicate/error states in runtime. | Verify |
| **Usage** | Time, mode, and status filters exist. API returns run-level totals from the in-memory `AtomicBudgetLedger`; there are no per-model/per-role attempts. Incomplete runs are marked as incompletely priced. | Use the durable budget ledger (07) and return attempt rows with model, role, tokens, latency, and known cost. Then add model/role filters and attempt-level charts. | Backend + frontend |
| **Settings - Routing** | API and UI now round-trip per-role model order and a positive hard cost ceiling. Role routes are checked against enabled models and supported roles. | Verify save/reload, stale versions, invalid ids, and clearing the ceiling. | Verify |

Other current behavior:

- Evidence chips, the query strip, and the evidence panel are implemented. Evidence is process-local and unavailable after an API restart (see 07).
- Answers arrive at the end of each specialist step; there is no token-by-token provider streaming. Tool results are emitted as they complete (see 02).
- The palette and core layouts are implemented. The Google Fonts runtime dependency still conflicts with the local/offline product description; self-host Manrope if offline operation is required (see 10).

## What to cover

- Profile and preview endpoints enforce limits and show the uploaded fixture's actual rows/column statistics; sampled profiles are identified in the UI.
- Diagnostics shows persisted extraction facts, warning records, and a clear legacy-snapshot state.
- Re-extracting a source after page reload creates a version; the Versions list shows it.
- Clarification pauses a run, saves the submitted answer, resumes it, and rejects a stale revision.
- Reloading Ask shows prior runs and answers; selecting a saved run restores its event trace and evidence chips; pinning a completed answer makes it appear on Dashboard.
- Usage totals match durable ledger attempts, including unknown prices, and model/role filters partition the attempts correctly.
- Routing round-trips role routes and cost ceiling; invalid model-role combinations return a validation error.
