# 13 - Gap analysis against the Deep Agent Engineering Guidelines v2

Checked 2026-11-25 against `main` @ `da9b4a9`; updated 2026-11-26 @ `ba8c22a`. Tracker: hareeshkumarch/analytics#15. Each gap links to its issue.

**Status:** Met = enforced in code and covered by tests. Partial = some of it. Gap = missing. N/A = does not apply to this product today (read-only analytics: agents have no write tools, no MCP, no code execution, no long-term memory), with the reason.

## Part I — architecture (sections 1–16)

| § | Topic | Status | Evidence in code / issue |
|---|---|---|---|
| 1 | Deterministic code for computation, LLM for reasoning | Met | Typed query IR compiled and executed by DuckDB; `calculate` tool does Decimal math; charts built from evidence. |
| 2 | Router → fast/deep path | Partial | User picks Quick/Deep; both start with a planner call. #24 |
| 3 | Query pushdown, validation, parameters, limits, offload | Met | `sqlglot_compiler.py`, bound parameters, row caps with truncation flags, evidence stored outside the prompt. Result caching: #23 |
| 3 | Snapshot consistency | Met | Runs are bound to an immutable snapshot + semantic version. |
| 4 | Tool contracts, typed args/results, dedup, circuit breakers, cancellation | Partial | Typed args (QueryIR parser), circuit breakers in routing, cancellation. Result envelope/error codes: #18. Dedup/cache: #23. Provenance ledger: #19 |
| 5 | Context engineering | Partial | Staged schema shrinking, old tool results trimmed, token estimate before calls, answer-only final round. Prompt caching/versioning/compiler: #25 |
| 6 | Durable execution state in Postgres | Partial | Runs, events, evidence, budget ledger, LangGraph checkpoints. Celery runs go through the transactional outbox. `tasks`/`tool_executions` tables: #19, #32 |
| 7 | DAG, checkpoints, resume, cancellation, real progress events | Partial | Plan DAG, events with sequence, resume after crash. Incremental replanning / task rows: #32. Partial-success outcome: #20 |
| 8 | Scoped subagents, bounded delegation | Met | Deep Agents harness with restricted tools (`FORBIDDEN_DEFAULT_TOOLS`), lifetime specialist cap, planner task limit. Per-task budgets: #32 |
| 9 | Model routing by capability | Met | Role routes, capability filters, presets, fallback. Measured routing: #27 |
| 10–11 | Illustrative numbers / differentiation | N/A | Guidance, not requirements; measured via #27. |
| 12 | Safety, untrusted data, secrets, budgets outside LLM | Partial | Untrusted framing, raw-row egress policy, encrypted keys, hard budgets. Adversarial tests: #29. Mutations: N/A (no write tools). |
| 13 | Observability and evaluation metrics | Partial | Request/run/trace ids, JSON logs, OTel spans, usage by model. Metrics/SLOs: #30. Evaluation: #27 |
| 14 | Stack | Met | FastAPI, Pydantic, LangGraph/Deep Agents, Postgres, SQLAlchemy/Alembic, DuckDB, SQLGlot, Redis, OTel, SSE. Object storage is local files (fine for single host). |
| 15–16 | Sequence and checklist | — | Covered by the rows above and the issues. |

## Part II — production rules (sections 17–36)

| § | Topic | Status | Evidence / issue |
|---|---|---|---|
| 17.1 INV-01 | Scope from trusted context | Gap (deferred) | No authentication yet; some endpoints skip workspace checks: #21 |
| 17.1 INV-02/03 | Tool grants, versioned args, deadlines | Partial | Fixed tool set per agent; deadlines and budgets enforced. Tool versions/timeouts: #19 |
| 17.1 INV-04 | Subagents cannot widen permissions/budget | Met | Shared run budget and restricted harness for every specialist. |
| 17.1 INV-05/06 | Approvals; no blind retry of writes | N/A | No write tools. |
| 17.1 INV-07 | Reuse only valid completed work | Partial | Resume reuses task traces/evidence; fingerprinted reuse: #32 |
| 17.1 INV-08 | Success only when criteria verified | Gap | #20 |
| 17.1 INV-09/13 | No secrets/unapproved data in prompts; untrusted data | Partial | Enforced for detail rows and credentials; tests: #29 |
| 17.1 INV-10 | Finite shared limits | Met | Attempts, tools, active seconds, cost and token ceilings in both ledgers; failed calls release their reservation. Per-mode profiles: #26 |
| 17.1 INV-11 | Cancellation | Met | Cancel endpoint, `should_cancel`, per-run lock keeps cancel atomic with events. |
| 17.1 INV-12 | Final answer cites durable evidence | Met | Citations to evidence ids; unknown ids stripped; charts from cited evidence only. |
| 17.1 INV-14 | Stale workers cannot overwrite | Met | `claim_run` bumps `owner_generation`; events, plan, clarification and answer writes are fenced. |
| 17.1 INV-15 | Fail safely, no invented results | Partial | Failures surface with limitations; criteria-based outcome: #20 |
| 17.2–17.3 | Smallest execution path; stop conditions | Partial | #24, #26 |
| 18 | Tool registry metadata, envelope | Partial | #18, #19 |
| 19.1–19.4 | Gateway lifecycle, concurrency, IDs, error taxonomy | Partial | Budget reservation before dispatch, bounded tool calls per turn, parallel specialists. Error taxonomy: #18; single-flight: #23 |
| 19.5 | Mutation lifecycle | N/A | No write tools. |
| 20 | MCP, webhooks | N/A | Not used. API clients: timeouts, SSRF policy, TLS with optional CA (done). |
| 21.1–21.2 | Control-plane schema, short transactions, outbox, fencing | Partial | Unique constraints, atomic version/quota updates, lock/statement timeouts, no DB transaction held across model calls, outbox, fencing, durable idempotency keys. Task rows: #32 |
| 21.3 | Pools and limits | Partial | SQLAlchemy pools, `lock_timeout` 5 s, `statement_timeout` 30 s. Pool-exhaustion tests: #28 |
| 21.4 | Tenant isolation / RLS | Gap (deferred) | #21 and the authentication work. |
| 22.1–22.4 | Progressive schema discovery, typed intent, SQL gates, performance | Met | Staged `inspect_schema`, typed IR only, no free SQL, DuckDB external access off. |
| 22.5 | Analytical correctness traps | Partial | Decimals, half-open compare periods, truncation flags, reconciliation aggregates before joining. Timezone and lineage: #22 |
| 22.6 | Typed writes only | N/A | No write tools. |
| 23.1 | Cache levels | Gap | #23 |
| 23.2 | Artifact lifecycle | Partial | Streamed staged publish with sha256, private directories, failed imports cleaned, uploads swept after 24 h, exports expire after `EXPORT_RETENTION_DAYS`. Export metadata/orphans: #31 |
| 23.3 | Memory | N/A | No long-term memory; previous turns read from run records. |
| 24 | Budgets, quotas | Partial | #26 |
| 25.1 | Provider gateway | Met | Adapters, capability matrix, streaming fallback, structured output with bounded repair, circuit breakers. |
| 25.2–25.4 | Context compiler, compaction | Partial | #25 |
| 26 | Prompt architecture, structured outputs | Partial | Strict JSON schemas for planner/reviewer; untrusted fields separated. Versioning: #25 |
| 27 | Deep Agents harness controls | Met | Restricted harness per specialist, explicit tools, shared budget; one orchestration authority (LangGraph outer loop). Task-row authority: #32 |
| 28.1 | Task/run state machine and outcomes | Partial | #20, #32 |
| 28.2–28.3 | Scheduler, incremental replanning | Partial | #32 |
| 28.4 | Durable progress events, replay | Met | Sequenced events, replay, reconnect, stream cap in the browser. `budget.warning`: #26 |
| 29 | Verification and evidence | Partial | Evidence reviewer in deep mode, uncited-claim check. Deterministic claim/evidence check: #20, #22 |
| 30 | Security, sandboxing, governance | Partial | No code execution; credentials encrypted; egress policy. Tests: #29. Auth: deferred |
| 31 | Observability, SLOs, incidents | Partial | #30 |
| 32 | Evaluation and gates | Gap | #27 |
| 33 | Deployment and CI | Partial | CI runs lint, types, unit, Playwright, accessibility, OpenAPI contract; compose verified. Security scans/SBOM and readiness checks: #30 |
| 34 | Failure-mode matrix | Partial | #28 |
| 35 | Prompt library | Partial | Planner/specialist/reviewer/composer prompts exist; versioned templates: #25 |
| 36 | Release checklist | — | Tracked by this file and #15. |

## Priority order

1. **P0** #16 outbox for Celery runs (done).
2. **P1** #17 fencing (done), #18 tool envelope and error codes, #19 tool ledger, #20 criteria-based completion, #21 workspace scoping, #22 analytical correctness, #28 failure-mode tests, #29 security tests.
3. **P2** #23 query cache, #24 execution-path router, #25 prompt versioning and context compiler, #26 budget profiles, #31 artifact lifecycle.
4. **P3** #27 evaluation harness, #30 operations, #32 durable task records and incremental replanning.
