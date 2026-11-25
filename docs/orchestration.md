# Orchestration

## Runtime path

`InvestigationOrchestrator.execute_local()` compiles and invokes a LangGraph workflow. The graph
prepares a ready task batch, dispatches its specialists, records results, and routes completed Deep
runs through evidence review and bounded remediation. `DeterministicSupervisor` remains the domain
authority for DAG readiness and task/tool budgets; ready specialists run concurrently in a
`ThreadPoolExecutor`.

The default path uses Deep Agents for planning, each specialist step, and final answer composition.
The planner can inspect the bound schema and returns a validated, bounded `PlanDAG`; the supervisor
still owns DAG readiness, task limits, and tool budgets. Model calls use the existing
`RoutingService`, while analytics tools stay bound to `AnalyticsToolbox` and its typed QueryIR,
read-only execution, evidence capture, and call limits.

Each specialist may delegate one focused check through Deep Agents' `task` tool. Delegates inherit
only the approved analytics tools and share the run's tool and model budgets. The general-purpose
subagent, filesystem tools, shell, and interpreter remain disabled. The evidence reviewer returns
a structured approval, gaps, and contradictions. When a review is rejected and the budget allows, the
planner sees the gaps, contradictions and findings and decides the next step: one remediation task
(a data or reconciliation specialist with its own objective) followed by an automatic re-review, or
stopping with a reason that becomes an answer limitation. An unusable planner reply falls back to a
data-specialist remediation, and any planner fallback is flagged in `plan.updated` (`fallback`) and in
the answer's limitations. The replan decision uses the provider's native structured output (`REPLAN_SCHEMA`). The planner and reviewer keep their tool-using Deep Agents turn, because most models can't combine tools with structured output; when that reply isn't valid JSON, one structured repair call converts it with `PLAN_SCHEMA` or `REVIEW_SCHEMA` before the run falls back. The planner repair runs only when model calls remain for at least one task. A deterministic evidence check still blocks uncited claims. The answer
composer receives only specialist findings and validated claims, and cannot access dataset tools.
Set `AGENT_RUNTIME=legacy` to use the previous hand-written specialist loop with the bounded default
plan and summary assembly.

When the planner cannot proceed without one missing detail, the run is saved as `awaiting_input`.
Clarification revision, prompt, answer, and the accepted plan are persisted with the run. The Ask
screen submits the answer with the current revision and resumes the same run in inline or Celery
mode. Stale or duplicate clarification submissions return a conflict.

When `DATABASE_URL` points to PostgreSQL, the outer LangGraph workflow and each specialist use a
Postgres checkpointer with stable run/task thread IDs. Checkpoint tables are initialized on first
use. Evidence and tool events are stored in PostgreSQL, and the PostgreSQL budget ledger preserves
model-attempt limits across restarts. In Compose, the API queues background runs through Redis and
the Celery worker reloads their inputs, snapshot, semantics, routing policy, and saved credentials
from PostgreSQL. The worker persists status, events, evidence, and answers; SSE replays and polls the
shared PostgreSQL event log. Celery acknowledges tasks after completion and rejects tasks whose
worker process is lost, allowing an interrupted run to restart from saved specialist checkpoints.
The outer workflow replays from its initial node and rebuilds supervisor state from deterministic
run inputs and saved specialist results. SQLite runs do not have durable checkpoints or cross-process
worker support; Celery mode requires PostgreSQL.

Set `RUN_EXECUTION_MODE=inline` to run in the API process instead. In that mode, API startup replays
queued or running runs from their saved inputs. Both paths use the same orchestration graph.

The local backend suite exercises the Deep Agents planning, specialist, review, composition, and
clarification paths with deterministic provider gateways. PostgreSQL/Redis/Celery execution and live
provider calls require an environment with Docker running and provider credentials configured.

## Budgets

| Mode | Active time | Model attempts | Analytical calls | Accepted tasks | Concurrent specialists | Replans | Tool calls |
|---|---:|---:|---:|---:|---:|---:|---:|
| Quick | 120 s | 6 | 4 | 4 | 1 | 0 | 16 |
| Deep | 600 s | 24 | 16 | 12 | 2 | 2 | 64 |

The values come from `QUICK_BUDGET_LIMITS` and `DEEP_BUDGET_LIMITS` in `domain/limits.py`. A run
may start at most 4 (quick) or 8 (deep) specialist instances. `planner_task_limit` subtracts two
tasks per allowed replan from that, so the planner always leaves room for bounded remediation and
re-review; limits that leave no planner capacity are rejected at import. A replan is skipped when the
remaining instances or model calls can't cover it, each batch leaves one model call for the answer
composer, and a run that exhausts its step or model-call budget cancels its pending tasks and
finishes with a limitation instead of failing.
