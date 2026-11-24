# Agentic Analytics Workspace (Local POC)

Ledgerline is a local, evidence-backed analytics workspace. You connect a data source, import a read-only snapshot, approve its metric definitions, and ask questions in plain language. Models answer through a bounded tool loop: they propose typed queries that are validated and run in DuckDB, and every figure in an answer links to the query and rows behind it.

Built with FastAPI, PostgreSQL, DuckDB/SQLGlot, a LangGraph orchestration supervisor, Deep Agents planning, specialists, and answer composition with bounded analytics delegates, 7 LLM provider adapters, and a Next.js App Router frontend. Docker Compose sends background investigations through Redis and a Celery worker; set `RUN_EXECUTION_MODE=inline` to keep execution in the API process.

Docs:
- [`docs/architecture.md`](docs/architecture.md)
- [`docs/connectors.md`](docs/connectors.md)
- [`docs/orchestration.md`](docs/orchestration.md)
- [`docs/security.md`](docs/security.md)
- [`docs/operations.md`](docs/operations.md)
- [`docs/evaluation.md`](docs/evaluation.md)
- [`docs/design.md`](docs/design.md), the UI design rules

## Project maturity

**Overall: an advanced prototype.** The architecture is strong, often ahead of commercial "chat with your data" tools, but operational maturity is low. The project hasn't been run end to end in its current form, and a bug that made every analysis step fail sat unnoticed until the latest review. Roughly 4/5 on architecture and 2/5 on operations.

| Area | Level | Why |
|---|---|---|
| Answer trustworthiness | **Strong** | The model never writes SQL. It proposes typed queries that are checked against the schema, parameterized and inspected before they run in an isolated DuckDB. Every figure is linked to its evidence, and evidence ids the model invents are removed. |
| Security design | **Strong** | Outbound requests are pinned to validated addresses, so connectors can't reach internal networks. Uploaded SQLite and DuckDB files are opened read-only in quarantine, the app binds to localhost only, provider keys are encrypted at rest, and exports are protected against formula injection. |
| Model routing and cost | **Good** | 7 providers, fallback, circuit breakers, a per-run budget ledger, cost ceilings and routing presets. |
| Data governance | **Good idea, thin** | Snapshots are immutable and definitions must be approved, but definitions are auto-proposed and can only be approved, not edited. |
| Connectors | **Good breadth** | 24 catalog entries over 10 backend families. Snapshot-only, capped at 40 MB, and not yet tested against live systems. |
| Analysis depth | **Basic** | Aggregates, breakdowns, period comparisons, distributions and reconciliation. No joins across sources, charts, follow-up questions, forecasting or statistics. |
| Reliability and operations | **Weak** | Runs execute inside the API process, and some state is lost on restart. Database errors are reported as 503s instead of falling back to memory. There is no tracing. CI lints the backend, installs from lock files and runs the Playwright suite. |
| Product and platform | **Early** | No login, one workspace, no sharing, and no alerts or scheduled refresh. |

## Roadmap: from prototype to an advanced product

### Stage 1: make it real (reliability)
1. **Verify and gate.** Run the verification checklist in [known-issues/12](known-issues/12-open-issues-reasons-and-fixes.md), and add lint, type checks and Playwright to CI so a broken run can't merge.
2. **Durable execution.** Move runs to a worker on a durable queue, and keep evidence, budget and idempotency in Postgres with no silent memory fallbacks. Restarts should never lose a run.
3. **Observability.** OpenTelemetry traces for each run, step, model call and query, recording cost, latency and tokens, plus a run timeline view.
4. **Evaluation harness.** Golden question sets with expected answers per dataset, scored automatically for each model and route, so "smarter" can be measured.

### Stage 2: make it smarter (the biggest gains)
5. **A real semantic layer.** Editable, versioned metrics, dimensions, synonyms and approved join paths that the planner reasons over (in the spirit of dbt MetricFlow or LookML).
6. **Joins across sources.** Approved relationships between snapshots in a workspace. DuckDB can already join them.
7. **Deeper analysis.**
   - Cohorts, funnels, window functions, forecasting, anomaly detection and significance tests in the query layer.
   - A sandboxed Python step (gVisor or Firecracker, no network) for analysis the query layer can't express.
8. **Verified charts.** Validated chart specs (for example Vega-Lite) built only from evidence rows, shown in answers and on pins.
9. **Better answers.**
   - A verifier that recomputes every cited number, and a confidence label per claim.
   - Follow-up questions with conversation memory, and clarifying questions when a request is ambiguous.
   - Schema retrieval (embedding search over columns and descriptions) so very wide datasets still fit in context.
10. **Self-tuning routing.** Use the evaluation scores to pick, for each role, the cheapest model that meets a quality bar.

### Stage 3: live data at scale
11. **Live queries.** Read-only compiled SQL against BigQuery, Snowflake and Postgres alongside snapshots, with scheduled and incremental refresh.
12. **Large data.** Parquet in object storage with DuckDB reading it in place, which removes the 40 MB cap. New connectors: Snowflake, Databricks, Redshift, ClickHouse, Salesforce and Shopify.

### Stage 4: product and platform
13. **Accounts and access.** Single sign-on (OIDC), roles, multiple workspaces, row-level security and an audit log. Detect and mask PII before anything reaches a model.
14. **Private mode.** Local models (vLLM or Ollama) for sensitive data, which fits the product's local positioning.
15. **Proactive features.** Dashboards that refresh on a schedule, alerts (for example "tell me when refunds jump 20%"), Slack and email digests, sharing, and an embeddable API.
16. **Deployment.** Helm charts, highly available Postgres, and horizontally scaled API pods, with live updates fanned out through Redis or NATS.

### If you only do five things
1. Durable worker execution, with Postgres as the single source of truth.
2. An evaluation harness with golden questions.
3. An editable semantic layer with join paths.
4. Verified charts, follow-up questions and clarification.
5. Live warehouse queries with scheduled refresh.

Concrete, smaller fixes (bugs, clean-ups and quick wins) are tracked separately in [known-issues/12](known-issues/12-open-issues-reasons-and-fixes.md).

## Known issues

All known problems and deferred fixes live in their own folder: **[`known-issues/`](known-issues/README.md)**. Each file says what is wrong, where, how to fix it, and what to test.

| File | Topic |
|---|---|
| [README.md](known-issues/README.md) | Index, top priorities, verification commands |
| [02](known-issues/02-specialists-do-not-query-data.md) | Tool loop over snapshot data: how it works, what's open, what to test |
| [03](known-issues/03-replanning-never-triggers.md) | Structured deep-mode review and bounded replanning |
| [05](known-issues/05-python-dependency-lock.md) | Python dependency lock (fixed) |
| [06](known-issues/06-celery-worker-and-dispatcher.md) | Celery execution is connected; live-stack reliability checks remain |
| [07](known-issues/07-in-memory-state.md) | State lost on API restart |
| [08](known-issues/08-frontend-gaps.md) | UI features waiting on the backend |
| [09](known-issues/09-missing-tests.md) | Missing tests |
| [10](known-issues/10-solid-refactors.md) | SOLID refactor plan, open bugs, hardcoded values |
| [11](known-issues/11-open-fixes-and-audit.md) | Audit history and status of each original audit item |
| [12](known-issues/12-open-issues-reasons-and-fixes.md) | **Current list of fixes and improvements**, with reason and how to fix |

Start with the verification checklist at the end of [12](known-issues/12-open-issues-reasons-and-fixes.md).
