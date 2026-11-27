# 13 - Gap analysis against the Deep Agent Engineering Guidelines v2

Checked 2026-11-25 against `main` @ `da9b4a9`; updated 2026-11-26 @ `6033894` plus #32. Tracker: hareeshkumarch/analytics#15. Each gap links to its issue.

**Status:** Met = enforced in code and covered by tests. Partial = some of it. Gap = missing. N/A = does not apply to this product today (read-only analytics: agents have no write tools, no MCP, no code execution, no long-term memory), with the reason.

## Part I — architecture (sections 1–16)

| § | Topic | Status | Evidence in code / issue |
|---|---|---|---|
| 1 | Deterministic code for computation, LLM for reasoning | Met | Typed query IR compiled and executed by DuckDB; `calculate` tool does Decimal math; charts built from evidence. |
| 2 | Router → fast/deep path | Met | Deterministic router: single short questions on one table skip the planner and composer (2 model calls); Deep always plans. |
| 3 | Query pushdown, validation, parameters, limits, offload | Met | `sqlglot_compiler.py`, bound parameters, row caps with truncation flags, evidence stored outside the prompt. Result cache with single-flight keyed by snapshot, definitions, time zone and compiled query. |
| 3 | Snapshot consistency | Met | Runs are bound to an immutable snapshot + semantic version. |
| 4 | Tool contracts, typed args/results, dedup, circuit breakers, cancellation | Met | Typed args, one result envelope with statuses and error codes, identical failed calls rejected, single-flight result cache, circuit breakers, cancellation, durable tool_executions ledger. |
| 5 | Context engineering | Met | Staged schema shrinking, old tool results trimmed, token estimate before calls, per-section token counts on spans, stable versioned prompt prefix with provider caching. |
| 6 | Durable execution state in Postgres | Met | Runs, events, evidence, budget ledger, `run_tasks` (one versioned row per task), LangGraph checkpoints. Celery runs go through the transactional outbox; every tool call is recorded in `tool_executions`. |
| 7 | DAG, checkpoints, resume, cancellation, real progress events | Met | Plan DAG, events with sequence, criteria-based outcomes. On resume, completed tasks with an unchanged input fingerprint are restored from `run_tasks` without model or tool calls; each replan is saved as a plan revision. |
| 8 | Scoped subagents, bounded delegation | Met | Deep Agents harness with restricted tools (`FORBIDDEN_DEFAULT_TOOLS`), lifetime specialist cap, planner task limit, and a token and tool-call slice per task taken from what the run has left at dispatch. |
| 9 | Model routing by capability | Met | Role routes, capability filters, presets, fallback, operator pause per provider. `make eval --model` compares models on the corpus. |
| 10–11 | Illustrative numbers / differentiation | N/A | Guidance, not requirements; measured with `make eval`. |
| 12 | Safety, untrusted data, secrets, budgets outside LLM | Met | Untrusted framing, raw-row egress policy, encrypted keys, log redaction, hard budgets; adversarial tests for data injection, hidden tools, secret leaks and redirect egress. Mutations: N/A (no write tools). |
| 13 | Observability and evaluation metrics | Met | Request/run/trace ids, JSON logs, OTel spans, usage by model, Prometheus metrics with alert rules and an SLO dashboard, and the evaluation harness. |
| 14 | Stack | Met | FastAPI, Pydantic, LangGraph/Deep Agents, Postgres, SQLAlchemy/Alembic, DuckDB, SQLGlot, Redis, OTel, SSE. Object storage is local files (fine for single host). |
| 15–16 | Sequence and checklist | — | Covered by the rows above and the issues. |

## Part II — production rules (sections 17–36)

| § | Topic | Status | Evidence / issue |
|---|---|---|---|
| 17.1 INV-01 | Scope from trusted context | Partial | Every object route checks that the object belongs to the workspace in the URL (table-driven test over the OpenAPI paths), and saved provider keys are per workspace. The workspace itself still comes from the URL until authentication lands (deferred). |
| 17.1 INV-02/03 | Tool grants, versioned args, deadlines | Met | Fixed tool set per agent; each tool declares version, effect, timeout and result bounds, which are enforced. |
| 17.1 INV-04 | Subagents cannot widen permissions/budget | Met | Shared run budget and restricted harness for every specialist. |
| 17.1 INV-05/06 | Approvals; no blind retry of writes | N/A | No write tools. |
| 17.1 INV-07 | Reuse only valid completed work | Met | A completed task is reused only when its input fingerprint (objective, criteria, snapshot, semantic version, question and its inputs' fingerprints) is unchanged. |
| 17.1 INV-08 | Success only when criteria verified | Met | Figures are checked against cited evidence; per-criterion status; outcome success / partial_success / needs_review. |
| 17.1 INV-09/13 | No secrets/unapproved data in prompts; untrusted data | Met | Detail rows withheld, credentials never sent, secrets redacted from logs; covered by adversarial tests. |
| 17.1 INV-10 | Finite shared limits | Met | Attempts, tools, active seconds, cost and token ceilings in both ledgers; failed calls release their reservation. Per-mode profiles: #26 |
| 17.1 INV-11 | Cancellation | Met | Cancel endpoint, `should_cancel`, per-run lock keeps cancel atomic with events. |
| 17.1 INV-12 | Final answer cites durable evidence | Met | Citations to evidence ids; unknown ids stripped; charts from cited evidence only. |
| 17.1 INV-14 | Stale workers cannot overwrite | Met | `claim_run` bumps `owner_generation`; events, plan, clarification and answer writes are fenced. |
| 17.1 INV-15 | Fail safely, no invented results | Met | Failures surface with limitations; unmatched figures mark the run needs_review. |
| 17.2–17.3 | Smallest execution path; stop conditions | Partial | #24, #26 |
| 18 | Tool registry metadata, envelope | Met | Tool specs (version, effect, timeout, inline limits, retry class) and one result envelope. |
| 19.1–19.4 | Gateway lifecycle, concurrency, IDs, error taxonomy | Met | Budget reservation before dispatch, bounded tool calls per turn, parallel specialists, error taxonomy with retry flags, single-flight query cache. |
| 19.5 | Mutation lifecycle | N/A | No write tools. |
| 20 | MCP, webhooks | N/A | Not used. API clients: timeouts, SSRF policy, TLS with optional CA (done). |
| 21.1–21.2 | Control-plane schema, short transactions, outbox, fencing | Partial | Unique constraints, atomic version/quota updates, lock/statement timeouts, no DB transaction held across model calls, outbox, fencing, durable idempotency keys. Task rows: #32 |
| 21.3 | Pools and limits | Partial | SQLAlchemy pools, `lock_timeout` 5 s, `statement_timeout` 30 s. Pool-exhaustion tests: #28 |
| 21.4 | Tenant isolation / RLS | Gap (deferred) | Application-level scoping is done (#21); RLS waits on authentication. |
| 22.1–22.4 | Progressive schema discovery, typed intent, SQL gates, performance | Met | Staged `inspect_schema`, typed IR only, no free SQL, DuckDB external access off. |
| 22.5 | Analytical correctness traps | Met | Decimals, half-open compare periods in the workspace business time zone, truncation flags, reconciliation aggregates before joining, metric lineage on evidence, figures checked against cited evidence. |
| 22.6 | Typed writes only | N/A | No write tools. |
| 23.1 | Cache levels | Met | Query result cache with single-flight; cache hits on the tool ledger. |
| 23.2 | Artifact lifecycle | Met | Export metadata rows with expiry, expired downloads 404, hourly sweep of expired exports and orphaned files, private directories, streamed sha256 publish. |
| 23.3 | Memory | N/A | No long-term memory; previous turns read from run records. |
| 24 | Budgets, quotas | Met | Per-mode token, cost, call and time limits, final-answer reserve, budget.warning, optional daily workspace quota. |
| 25.1 | Provider gateway | Met | Adapters, capability matrix, streaming fallback, structured output with bounded repair, circuit breakers. |
| 25.2–25.4 | Context compiler, compaction | Partial | Versioned templates, stable prefix, per-section token accounting; no separate compiler stage. |
| 26 | Prompt architecture, structured outputs | Met | Strict JSON schemas, untrusted fields separated, versioned templates recorded on every model call. |
| 27 | Deep Agents harness controls | Met | Restricted harness per specialist, explicit tools, per-task budget slices and acceptance criteria. One owner per concern: `run_tasks` owns task status, results and budgets; the LangGraph checkpoint owns only the in-flight messages of a running task. |
| 28.1 | Task/run state machine and outcomes | Met | Run outcomes; task transitions checked against an allowed-transition table with optimistic versions. |
| 28.2–28.3 | Scheduler, incremental replanning | Met | A replan appends remediation and review tasks and keeps verified tasks and their evidence; the new revision is saved for resume. |
| 28.4 | Durable progress events, replay | Met | Sequenced events, replay, reconnect, stream cap in the browser. `budget.warning`: #26 |
| 29 | Verification and evidence | Met | Evidence reviewer in deep mode, deterministic figure/evidence check, per-criterion status, metric lineage on evidence. |
| 30 | Security, sandboxing, governance | Partial | No code execution; credentials encrypted; egress policy; log redaction; adversarial tests. Auth: deferred |
| 31 | Observability, SLOs, incidents | Met | `/ready`, `/metrics`, alert rules, Grafana dashboard, pause switches for runs and providers with history, `docs/runbook.md`. |
| 32 | Evaluation and gates | Partial | `make eval` with a versioned corpus, evidence-based scoring, baseline deltas and a regression exit code; runs nightly, on demand and as a check on PRs that change prompts or orchestration (needs a provider key secret). The corpus covers one dataset. |
| 33 | Deployment and CI | Met | CI runs lint, types, unit, Playwright, accessibility, OpenAPI contract, and applies/rolls back every migration on PostgreSQL 16 with PostgreSQL-only tests; compose verified. Readiness check used by the API container. The Security workflow audits both backend lock files and the frontend production dependencies on every push, PR and weekly, and uploads CycloneDX SBOMs. |
| 34 | Failure-mode matrix | Partial | Every row mapped in [14-failure-matrix.md](14-failure-matrix.md): 74 covered, 17 partial, 1 open (RLS, deferred with authentication), 14 N/A. |
| 35 | Prompt library | Met | All prompts in `app/application/prompts.py` with content-hash versions; `/api/v1/prompts`. |
| 36 | Release checklist | — | Tracked by this file and #15. |

## Priority order

1. **P0** #16 outbox for Celery runs (done).
2. **P1** #17 fencing (done), #18 tool envelope and error codes (done), #19 tool ledger (done), #20 criteria-based completion (done), #21 workspace scoping (done), #22 analytical correctness (done), #28 failure-mode tests (done), #29 security tests (done).
3. **P2** #23 query cache, #24 execution-path router, #25 prompt versioning, #26 budget profiles, #31 artifact lifecycle (all done).
4. **P3** #27 evaluation harness, #30 operations, #32 durable task records and incremental replanning (all done).
