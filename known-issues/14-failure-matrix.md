# 14 - Failure-mode test matrix (guidelines §34)

Checked 2026-11-26 against `main`. Tracker: hareeshkumarch/analytics#28.

**Status:** Covered = an automated test asserts the expected behavior (file::test). Partial = behavior exists but the test does not cover every part, or only part of the behavior exists. Open = not handled yet, with its issue. N/A = does not apply to this product (read-only analytics: no write tools, no MCP, no webhooks, no long-term memory or vector retrieval), with the reason.

Tests marked (PG) run against real PostgreSQL. They run in CI, where the backend job starts PostgreSQL 16, and locally when `TEST_POSTGRES_URL` is set.

## 34.1 Tool calling and external integrations

| Failure | Status | Test / reason |
|---|---|---|
| Model invents nonexistent tool | Covered | `test_tool_envelope::error_classes` (UNKNOWN_TOOL), `test_agent_tool_isolation::filesystem_and_shell_tools_are_refused` |
| Wrong tool / overlapping definitions | N/A | Three disjoint read-only tools; a wrong pick costs one call and has no effect. Routing quality: #27 |
| Missing/unknown field, wrong enum or type | Covered | `test_tool_envelope::error_classes` (INVALID_ARGUMENT + `failed_fields`), `test_tool_loop::rejects_unknown_operation`, `test_pydantic_and_async_api::pydantic_extra_fields_rejected_with_error_envelope` |
| Optional field omitted vs null vs empty | Covered | `test_failure_matrix_extras::omitted_null_and_empty_lists_mean_the_same` |
| Tool definition changed mid-run | Partial | Each call records `tool_version` (`test_tool_executions`); no version check on resume yet |
| Tool result schema drift | N/A | Results are built in-process by the toolbox, not read from a remote schema |
| Streamed JSON arguments incomplete | Covered | `test_tool_loop::parse_arguments_tolerates_bad_json` (invalid args are never dispatched), `test_streaming::failed_stream_resets_before_the_retry` |
| Duplicate tool-call ID / delivery | N/A | Tools are read-only, so a repeat has no effect; every call is recorded once (`test_tool_executions::every_call_is_recorded_once_with_status_and_fingerprint`) |
| Duplicate equivalent read in parallel | Covered | `test_query_result_cache::parallel_identical_queries_execute_once` |
| Dependent calls launched together | Covered | `test_domain_limits_and_inr::plan_dag_cycle_and_missing_dep_rejection`, `test_agentic_harness::deep_mode_parallel_fanout_and_bounded_replan` |
| Batch partially succeeds | Covered | Per-call outcomes: `test_tool_executions::every_call_is_recorded_once_with_status_and_fingerprint`; run outcome `partial_success`: `test_verification::rules` |
| HTTP 200 contains domain error | Covered | `test_provider_bad_bodies::every_adapter_rejects_error_bodies_and_malformed_json`, `::test_truncated_replies_are_incomplete` |
| API responds empty data | Covered | `test_tool_envelope::success_empty_and_partial` (status `empty`); empty model reply falls back: `test_provider_bad_bodies::empty_200_is_retried_on_the_next_model` |
| Pagination cursor loop | Covered | `test_strict_invariants::rest_blocks_redirects_cross_origin_and_cursor_loops` |
| Response too large | Covered | `test_new_connectors::download_cap`, `test_expansion_limits`, inline byte cap per tool spec |
| Provider rate limit / 429 | Covered | `test_failure_matrix_extras::429_is_retryable_and_retry_after_is_parsed`, `test_providers_and_routing::circuit_breaker_opens_after_five_transient_failures` |
| Authentication expires | Covered | 401 is non-retryable and falls back to the next model: `test_routing_repair_and_endpoints::non_retryable_error_falls_back_to_the_next_model` |
| Provider returns 403 | Covered | Same test; one key per provider, never another credential (`test_failure_matrix_extras::429_is_retryable_and_retry_after_is_parsed` asserts 403 is not retried) |
| Remote success but local timeout | Partial | No remote mutations. A timed-out model call keeps the run flagged incomplete (`test_usage_totals::memory_ledger`) |
| Idempotency key reused with changed payload | Covered | `test_api_and_infra::fastapi_health_providers_and_run_idempotency` (409), `test_idempotency_durable` |
| Crash right after remote mutation | N/A | No remote mutations |
| Concurrent approvals / duplicate clicks | Covered | `test_semantic_approval::only_one_concurrent_approval_wins` |
| User changes approved target | Covered | `test_semantic_approval::stale_approval_is_refused` |
| Connector unavailable | Partial | Provider circuit breakers and fallback are tested; connector failures surface as `ConnectorError` without a breaker |
| Provider returns malicious instructions | Covered | `test_adversarial_data::hostile_rows_do_not_reach_the_model_or_change_scope`, `test_agent_tool_isolation` |
| MCP capability change | N/A | No MCP |
| Token intended for another resource | Covered | `test_new_connectors::follows_redirect_and_only_sends_token_to_first_origin` |
| Malicious URL / redirect / DNS | Covered | `test_adversarial_data::redirects_to_private_or_metadata_addresses_are_blocked`, `test_parsers_and_connectors::ssrf_metadata_never_overridden_by_local_test_flag` |
| Webhook delivered twice | N/A | No webhooks |
| Unbounded tool loop | Covered | `test_tool_loop::final_round_disables_tools_and_model_is_pinned`, `::time_budget_stops_run`, `::low_token_budget_forces_an_answer_round`, `test_tool_envelope::budget_exhausted_is_denied` |

## 34.2 Database, query and data correctness

| Failure | Status | Test / reason |
|---|---|---|
| Fabricated table or column | Covered | `test_query_ir_and_ast::unknown_field_rejected` |
| Schema changed after planning | Covered | Snapshots are immutable and runs are bound to one: `test_snapshot_binding::reimport_after_the_run_was_created_does_not_change_its_data` |
| SQL injection via filter/identifier | Covered | `test_strict_invariants::query_ir_all_operations_and_sqli_parameterization`, `test_query_ir_and_ast::aggregate_query_parameterized_and_quoted` |
| Hidden write CTE | Covered | `test_query_ir_and_ast::forbidden_sql_ast_nodes_rejected` |
| Unsafe function / external scan | Covered | Same test, `::missing_sqlglot_fails_closed`; DuckDB runs with external access and extension loading off |
| Unbounded cross join / huge scan | Covered | `test_tool_executions::executor_stops_a_query_at_its_deadline`; DuckDB memory limit |
| `LIMIT 10` still scans huge table | Partial | Bounded by the deadline and memory limit; no scanned-bytes budget |
| Connection pool exhausted | Covered | `test_fault_injection::requests_fail_fast_with_503_and_connections_are_returned`, `::production_pool_waits_a_bounded_time` |
| Leaked connection on cancellation | Partial | Pool returns to baseline after failures (same test); cancelling inside a query is not tested |
| Same ORM session in parallel tasks | Covered | A session per operation; `test_event_locking::other_runs_can_be_queued_and_cancelled_while_one_runs_event_write_is_stuck` |
| Connection drops during commit | Covered | `test_fault_injection::api_run_reports_one_failed_finish_and_never_completed`, `::worker_leaves_the_run_reclaimable_and_a_redelivery_finishes_it_once` |
| Deadlock / serialization failure | Partial | `lock_timeout` 5 s and unique-constraint retries: `test_snapshot_versions::concurrent_reservations_get_distinct_versions` |
| Transaction held during model call | Partial | Not done by design (short transactions per write); no automated idle-transaction check |
| Lost update from concurrent workers | Covered | `test_run_fencing::only_the_latest_claim_can_write`, `test_semantic_approval::stale_approval_is_refused` |
| Worker lease expires | Covered | `test_run_fencing::a_redelivered_run_is_finished_only_by_the_newest_worker` |
| Connection reused with tenant setting | N/A | No per-connection tenant settings; RLS is deferred with authentication |
| RLS bypass | Open | Deferred with authentication |
| Cross-tenant join / cache / FK | Covered | `test_workspace_scoping::path_ids_from_another_workspace_are_not_found` (every object route in the OpenAPI schema), `::ids_in_request_bodies_from_another_workspace_are_not_found`, `::a_key_saved_in_one_workspace_is_not_used_by_another`, `test_uploads::uploads_are_scoped_to_their_workspace`; caches are keyed by snapshot, which belongs to one workspace |
| Lagging read replica | N/A | Single primary |
| Two queries see different snapshots | Covered | A run reads one snapshot: `test_snapshot_binding` |
| N+1 / repeated schema query | Covered | Schema comes from snapshot metadata, not the database; `test_query_result_cache::a_repeated_question_is_served_from_cache_and_counted` |
| Many-to-many joins inflate metric | Covered | Reconciliation aggregates each side before joining: `test_domain_limits_and_inr::inr_acceptance_fixture_exact_reconciliation`, `test_tool_loop::reconciliation_applies_filters` |
| Null denominator / division by zero | Covered | `test_failure_matrix_extras::ratio_against_a_zero_baseline_is_refused` |
| Date boundary / DST / fiscal period | Partial | Half-open ranges and business time zone: `test_query_guardrails`, `test_business_timezone`; ambiguous DST times and fiscal calendars not tested |
| Currency / decimal precision | Covered | `test_domain_limits_and_inr::mixed_currency_rejected`, `test_parsers_and_connectors::parquet_preserves_decimal_metadata` |
| CSV mixed types / missing / duplicate columns | Covered | `test_parser_inference`, `test_parsers_and_connectors::csv_preserves_leading_zero_ids` |
| Paged query skips or duplicates rows | Covered | `test_run_pagination::runs_are_paged_newest_first_with_a_cursor`, REST link-header tests |
| Query cancelled midway | Partial | DuckDB interrupt at the deadline is tested; user cancel stops between calls, not inside one |
| Commit succeeds but event publication fails | Covered | `test_outbox::job_is_written_with_the_run_and_survives_a_failed_publish` |
| Migration during rolling deployment | Partial | CI applies, rolls back and re-applies every migration on PostgreSQL; old/new worker compatibility is not tested |
| Disk full / upload incomplete | Covered | `test_local_artifact_store::publish_streams_with_correct_digest_and_leaves_no_temp_files`, `test_workspace_quota::failed_save_releases_the_reservation` |

## 34.3 Models, context and agents

| Failure | Status | Test / reason |
|---|---|---|
| Long context near model limit | Covered | `test_tool_loop::older_tool_results_are_trimmed`, `::low_token_budget_forces_an_answer_round`; context compiler: #25 |
| Compaction forgets user correction | Covered | `test_agentic_harness::harness_profiles_and_context_compaction_preserve_constraints` |
| Old tool result contradicted | N/A | A run reads one immutable snapshot |
| All tools loaded every request | N/A | Three small tools; loading all is cheaper than selecting |
| Large tool outputs appended | Covered | Inline caps and evidence stored by reference: `test_tool_loop::older_tool_results_are_trimmed` |
| Invalid structured output / truncation | Covered | `test_structured_output`, `test_routing_repair_and_endpoints::one_attempt_schema_repair_and_answer_reset_on_fallback`, `test_provider_bad_bodies::truncated_replies_are_incomplete` |
| Provider changes streaming format | Covered | `test_streaming` contract tests per API family |
| Model refuses | Covered | `test_providers_and_routing::all_seven_provider_adapters_contract` (refusal is reported, not treated as an answer) |
| Fallback lacks needed feature | Covered | `test_strict_invariants::manual_override_cannot_bypass_hard_capability_filter` |
| Subagent inherits too many tools | Covered | `test_budget_and_orchestration::restricted_tool_manifest_rejects_unsafe_defaults`, `test_agent_tool_isolation` |
| Subagent missing parent's middleware | Partial | Specialists share the toolbox, budget and tool ledger; not asserted per subagent |
| Agents repeat the same research | Covered | Identical failed calls are rejected; `test_query_result_cache::parallel_identical_queries_execute_once`; finished tasks are not re-run on resume: `test_task_records::a_resumed_deep_run_reexecutes_only_unfinished_tasks` |
| Recursive delegation | Covered | `test_budget_guardrails` (specialist and replan caps) |
| Agents disagree confidently | Partial | Reviewer contradictions make the outcome `needs_review` (`test_verification::rules`); disagreement is not shown side by side |
| Agent marks fabricated result verified | Covered | `test_verification::answer_citing_a_number_not_in_evidence_needs_review`, `test_new_connectors::unknown_citations_are_removed` |
| Memory poisoning | N/A | No long-term memory |
| Vector retrieval crosses ACL | N/A | No vector retrieval |
| Semantic cache false match | N/A | No semantic cache |
| User deletes source | Covered | `test_deletion::pinned_dataset_needs_force_and_its_files_are_removed` |
| Unnecessary reflection | Covered | Bounded rounds and replans: `test_budget_guardrails` |
| Token usage delayed or missing | Covered | Unsettled reservations keep the run incomplete: `test_usage_totals` |
| Parallel agents overdraw budget | Covered | `test_budget_and_orchestration::parallel_last_slot_budget_admission` |
| Prompt/tool update regresses quality | Partial | `make eval` fails on a pass-rate or cost regression against the baseline (`test_evaluation::report_scores_questions_and_flags_regressions_against_the_baseline`); it runs nightly and on demand, not on every PR |

## 34.4 Orchestration, UI and operations

| Failure | Status | Test / reason |
|---|---|---|
| Worker dies before task starts | Covered | `test_outbox::a_stalled_claim_is_published_again` |
| Worker dies after successful task | Covered (PG) | `test_worker_crash_resume::redelivery_reuses_the_finished_step`, `test_task_records::a_resumed_deep_run_reexecutes_only_unfinished_tasks` |
| Queue delivers task twice | Covered | `test_run_fencing::a_redelivered_run_is_finished_only_by_the_newest_worker` |
| User changes goal during run | N/A | A run's question is fixed; changes come as follow-ups (`test_follow_ups`) or clarifications (`test_end_to_end_slice::planner_clarification_pauses_and_resumes_the_same_run`) |
| One parallel branch fails | Covered | Outcome `partial_success`: `test_verification::rules` |
| Required dependency missing | Covered | `test_domain_limits_and_inr::plan_dag_cycle_and_missing_dep_rejection` |
| No meaningful progress | Covered | `test_tool_loop::time_budget_stops_run`, `test_budget_guardrails::exhausted_step_limit_skips_remaining_tasks_instead_of_crashing` |
| Cancel during tool call | Partial | Cancel is checked before each call (`test_event_locking`); an in-flight query finishes or hits its deadline |
| Browser refresh / disconnect | Covered | `frontend/src/lib/sse.test.ts` "resumes after the given sequence and drops duplicates" |
| Duplicate / out-of-order events | Covered | Same frontend test |
| Plan revision changes progress | Covered | Steps come from the latest `plan.updated`; there is no percentage bar |
| Slow event consumer | Covered | `test_sse_max_age`, `frontend/tests/stream-limit.spec.ts` |
| Result references inaccessible artifact | Partial | Exports are scoped and expire (`test_workspace_scoping`); unknown evidence ids are stripped from answers |
| UI says success when only queued | Covered | `run.finished` is published only after the answer is saved (`test_fault_injection`); outcome badge (`test_verification`) |
| Provider-wide outage | Covered | `test_providers_and_routing::circuit_breaker_opens_after_five_transient_failures`, fallback tests |
| Redis outage | Covered | `test_outbox::job_is_written_with_the_run_and_survives_a_failed_publish` |
| DB outage | Covered | `test_persistence_guardrails` (503, no in-memory fallback) |
| Logging/metrics outage | Partial | A failed tool-ledger write is logged and the run continues; metrics are computed from the database, so they survive a process restart (`test_ops::counts_queue_stuck_runs_and_model_call_errors`); no fail-closed audit mode |
| Missing secret / misconfiguration | Covered | `test_persistence_guardrails::missing_database_url_fails_outside_tests`, `::invalid_master_key_fails_startup` |
| Logs contain PII or API key | Covered | `test_secret_redaction` |
| Backup restores stale state | Open | `make backup-dev`/`restore-dev` and runbook steps exist; no restore reconciliation test |
| Cost/latency spike after deploy | Partial | The eval cost margin (`test_evaluation`) and the p95 latency and provider error alerts in `infra/monitoring/alerts.yml`; the alert rules have no automated test |

## Summary

106 rows: Covered 73, Partial 17, Open 2 (RLS, deferred with authentication; backup reconciliation), N/A 14.
