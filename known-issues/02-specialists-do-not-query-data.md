# 02 · Specialists querying the data (tool loop)

## Status
**Implemented in source; runtime verification remains open.** The specialists now query the bound
dataset with real tools and cite evidence. This follow-up did not run the tests or exercise live
provider integrations, so the behavior described below still needs the checks in **What to test**.

## How it works now

### Tools (`backend/app/application/services/analytics_tools.py`)
| Tool | What it does |
|---|---|
| `inspect_schema` | Tables, columns, types and row counts, plus proposed or approved metric definitions. |
| `run_query_ir` | Runs a typed QueryIR (see the table below). |
| `calculate` | Sum, difference, ratio or percent change of values in earlier query results (named by evidence id, row and column), computed with `Decimal` and stored as new evidence that links to its inputs. Refuses evidence whose rows were withheld from the model. |

How `run_query_ir` handles a query:
- `parse_query_ir` (`domain/query_ir.py`) checks it, `SqlGlotQueryCompiler` compiles it, and it runs
  read-only in DuckDB over the snapshot's Arrow tables (`adapters/query/snapshot_engine.py`).
- Raw SQL is never accepted.
- Results sent to the model are capped at 50 rows and 64 KB.

Errors go back to the model as tool results, so it can fix its query and retry:
- Invalid queries, meaning parse or validation errors or bad JSON arguments, are **refunded** to the
  query budget.
- DuckDB runtime errors, such as summing a text column, are not refunded.

| Operation | Returns |
|---|---|
| `aggregate` | Metrics grouped by columns, with filters and ordering. |
| `detail` | Raw rows from selected columns. |
| `compare_periods` | One metric for two date ranges, optionally grouped. |
| `contribution_breakdown` | One metric per value of a dimension, largest first. |
| `distribution` | Numeric column: a histogram with `buckets` bins, plus min/max/avg/count. Other columns: min, max, count and distinct count. |
| `reconciliation_lookup` | Two tables joined on a key, with left/right totals and the variance. Each filter applies to whichever table has that column. |

### The loop (`application/services/orchestration_service.py::_run_specialist_task`)
- **Rounds and calls:**
  - Up to 6 tool rounds per step, then a final "answer now" round.
  - Several tool calls can be made in one turn, but at most 8 run. Any beyond that get an error back.
- **Budget per run:** one `ToolBudget` limits tool calls (16 quick, 64 deep) and queries (4 quick,
  16 deep).
- **The final round starts early when:**
  - the query budget is used up;
  - only one model attempt is left;
  - the run is within 45 s of its time limit.

  The final round sends `tool_choice: none`. That is `{"type":"none"}` for Anthropic and goes in
  `generation_config` for Gemini.
- **One model per step.** After round 1, later rounds pin the same model (`manual_model_id`). If that
  model fails, the round is retried once on the normal route, unless the deadline has passed.
- **Output and timeouts:**
  - Tool rounds request 12,000 output tokens, capped by the model's own limit.
  - Each call's timeout is the smaller of 120 s and the time left. Tool rounds get at least 10 s,
    and the final round at least 30 s.
- **Older results are trimmed.** Query results over 4 KB from earlier rounds are cut to 10 rows,
  keeping the evidence id.
- **Failures:**
  - A failing later round keeps the evidence already collected and marks the step "stopped early".
  - An empty answer with no tool use fails the step.
- **Cancelling** stops the loop before the next round.
- **Deep mode:**
  - The reviewer step gets the earlier steps' findings.
  - If a data step made claims with no query evidence, one remediation step is added, within
    `max_replans` (default `_evidence_verdict`; see 03).
- **Time budget** (`active_seconds`: 120 s quick, 600 s deep):
  - No new step starts after the deadline.
  - A run that produced nothing ends `failed` with "Time budget exceeded".
  - A partial run ends `completed` with `time_budget_exceeded: true` and a limitation note.

### Provider wire formats (`adapters/providers/message_format.py`)
Tool history is stored in one provider-neutral form and converted for each provider on every call.

| Provider | Tool calls / results | What is replayed between rounds |
|---|---|---|
| OpenAI, xAI (Responses) | `function_call` / `function_call_output` | Raw `output` items, including encrypted reasoning: `include: ["reasoning.encrypted_content"]` for reasoning models, `store: false`. |
| Anthropic (Messages) | `tool_use` / `tool_result` (results merged into one user turn) | Native `content`, so thinking blocks survive. Foreign ids are cleaned (`functions.x:0` → `functions_x_0`). |
| Gemini (Interactions) | `function_call` / `function_result` steps; errors sent as `ERROR: …` text | Native `steps`. The system prompt goes in `system_instruction`, and a missing call id is filled in. Format checked against Google's docs on 2026-11-17. |
| Groq, Kimi, NIM (chat completions) | `tool_calls` / `role: tool` | Kimi's `reasoning_content`. |

Malformed argument JSON from any provider becomes a tool error rather than a crash. A
`"tool_calls": null` response is accepted.

### Evidence
- **Evidence ids** are `ev_` plus the first 12 characters of
  `sha256(query_ir_hash + ":" + result_hash)`. That makes them unique per query and result.
- **Claims** carry the ids of the queries behind them. The model is told to cite them as `[ev_…]`.
- **Live events:**
  - Each tool call emits `tool.result` as soon as it finishes: tool, ok, operation, row_count,
    evidence_id, ms and round.
  - Event emission is locked, so parallel steps keep sequence numbers in order.
- **Storage:**
  - Evidence records (SQL, hashes, columns, first 50 rows) go to `app.state.evidence`, which keeps
    the last 200 runs.
  - API: `GET /workspaces/{ws}/runs/{run}/evidence` lists a run's evidence, and
    `…/evidence/{ev_id}` returns one record.
- **UI (Ask):**
  - Every `[ev_…]`, including grouped `[ev_a, ev_b]`, is a chip that opens a side panel
    (`features/ask/EvidencePanel.tsx`) with the rows, the compiled SQL and the hashes.
  - A live **Queries** strip lists each tool call as it finishes.

### Data plumbing
- **Snapshot records** store `artifact_id` and `table_artifacts` (`{table: artifact id}`). Published
  table files are named `"{artifact_id}__t_<sha256(table)[:16]>"` (`ingestion.table_artifact_id`),
  so any table name is path-safe.
- **Runs:** `_execute_bounded_run` passes the snapshot and semantic records to the orchestrator.
  Without a dataset, the model gets no tools and is told to ask for one.
- **Token estimates:** before each call, `estimate_input_tokens` (`deep_agents_bridge.py`) counts the
  whole serialised history plus tool schemas, at about 3 characters per token. Budget and cost checks
  therefore don't undercount.

## Still open
1. **Re-extract old datasets.** Snapshots extracted before this change have no `table_artifacts` and
   return "This snapshot has no queryable tables".
2. **No token-by-token streaming.** This was left out on purpose: it needs an SSE parser for 7
   formats and a partial tool-call assembler, and none of it could be tested here.
   - Today each step's answer arrives when the step ends. Tool calls appear live.
   - To add it: set `stream: true` per adapter, parse the deltas, emit `answer.delta` from inside the
     loop, and use `answer.reset` for retries.
3. **Evidence is lost on API restart** (in-memory, like runs and events; see 07). The panel says so.
4. **Kimi after a fallback.** If a pinned model fails and the retry lands on Kimi in thinking mode,
   Kimi may reject earlier tool turns that have no `reasoning_content`.
5. **Gemini fields not confirmed by Google's docs:**
   - whether `temperature` is supported (it is not sent);
   - whether `response_mime_type` is accepted;
   - whether `tool_choice` belongs in `generation_config`.

   A 400 on Gemini probably means one of these.
6. **Runs can overrun the time limit slightly.** Each model call can retry up to 3 times inside
   `RoutingService.execute_step`, and the deadline isn't checked between those attempts.
7. **Prompt caching is weaker.** Trimming older results rewrites earlier messages, so fewer cached
   prompts are reused. This was accepted to stay under the 500k-token run budget.
8. **Unverified DuckDB setting.** `SET enable_external_access = false` runs after the Arrow tables are
   registered. Registered views should still be readable. If every query fails in the first test
   run, look here first (`adapters/query/duckdb_executor.py`).

## What to test
```bash
cd backend && pip install -e ".[dev]" && pytest -v
```
```bash
cd frontend && npm install && npm run typecheck && npm run build
```

Tests in `backend/tests/test_tool_loop.py`, by area:

| Area | Tests |
|---|---|
| Core loop | `test_specialist_loop_executes_tools_and_cites_evidence`, `test_no_snapshot_offers_no_tools` |
| Toolbox | `test_toolbox_runs_query_and_returns_evidence` (real Arrow → DuckDB; invalid queries refunded), `test_distribution_histogram_and_unique_evidence_ids`, `test_reconciliation_applies_filters` |
| Final round and pinning | `test_final_round_disables_tools_and_model_is_pinned` |
| Budgets | `test_time_budget_stops_run`, `test_older_tool_results_are_trimmed`, `test_token_estimate_counts_provider_state_and_tools`, `test_tool_calls_per_turn_are_capped_and_evidence_is_streamed` |
| Wire formats | `TestMessageFormat` (Anthropic, Responses, chat completions, Gemini, id cleaning, bad JSON), `test_responses_replays_native_output_for_same_provider` |
| Review and storage | `test_evidence_verdict_flags_uncited_claims_once`, `test_table_artifact_ids_are_path_safe` |

Live check, with a provider key saved:
1. On Data, upload a CSV and extract it.
2. In Ask, ask "total by <column>".
   - The Queries strip fills in as queries run.
   - The answer cites `[ev_…]` chips, and clicking one shows its rows and SQL. Esc closes the panel.
3. Repeat with Anthropic, OpenAI, xAI and Gemini keys to exercise each wire format.
4. Deep mode: the answer has Analysis, Reconciliation and Evidence review sections.
5. Cancel a deep run mid-way. No further `tool.result` events should arrive.
6. INR acceptance: run `fixtures/inr_acceptance/expected_answers.json` end to end. The numbers should
   match exactly as Decimals.
