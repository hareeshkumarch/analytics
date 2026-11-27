from concurrent.futures import ThreadPoolExecutor
import contextvars
from datetime import datetime, timezone
import hashlib
import json
import logging
import re
import threading
import time
import uuid
from typing import Any, Callable, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from app.adapters.orchestration.deep_agents_bridge import (
    BoundedChildContext,
    GatewayChatModelBridge,
    build_restricted_deep_agent_config,
    compact_child_context,
    estimate_input_tokens,
)
from app.adapters.orchestration.deep_agent_runtime import DeepAgentTaskRunner
from app.adapters.persistence.langgraph_checkpointer import open_langgraph_checkpointer
from app.adapters.orchestration.structured_schemas import PLAN_SCHEMA, REPLAN_SCHEMA
from app.application.runtime_settings import AgentRuntime, agent_runtime
from app.application.services.context_compiler import (
    CLAIM_STATEMENT_CHARS,
    MAX_COMPLETION_CRITERIA,
    MAX_CRITERION_CHARS,
    MAX_REVIEW_ITEM_CHARS,
    MAX_REVIEW_ITEMS,
    REPAIR_INPUT_TOKENS,
    REPLAN_FINDINGS_TOKENS,
    bounded_items,
    clip_statement,
    composer_findings,
    fit_text,
    specialist_context,
)
from app.application.services.task_records import (
    TaskLedger,
    budget_slice,
    outcome_result,
    restored_attempt,
    restored_claim,
    restored_verdict,
    task_fingerprints,
)
from app.adapters.orchestration.supervisor import (
    DeterministicSupervisor,
    ReviewVerdict,
    SpecialistOutcome,
    TaskAttemptResult,
)
from app.adapters.query.snapshot_engine import SnapshotQueryEngine
from app.adapters.query.sqlglot_compiler import SqlGlotQueryCompiler
from app.application.ports.query import SnapshotQueryPort
from app.application import prompts
from app.application.ports.tools import ToolExecution
from app.application.prompts import using_prompt
from app.application.services.analytics_tools import AnalyticsToolbox, ToolBudget, ToolErrorCode
from app.application.services.charts import build_answer_charts
from app.application.services.execution_router import ExecutionRoute, RouteDecision, choose_route
from app.application.services.text_utils import (
    EVIDENCE_ID_RE as _EVIDENCE_ID_RE,
    clean_single_line as _clean_objective,
    clean_text as _clean_text,
    extract_json_object,
    strip_unknown_citations as _strip_unknown_citations,
)
from app.application.services.verification import assess_answer
from app.domain.errors import BudgetExhaustedError, ClarificationRequired, public_message
from app.domain.events import EventType, RunEvent
from app.domain.timezone import DEFAULT_BUSINESS_TIMEZONE
from app.domain.limits import DEFAULT_GLOBAL_LIMITS, REPLAN_TASKS
from app.domain.models import Answer, Claim, PlanDAG, RunMode, RunStatus, TaskBudget, TaskKind, TaskNode, TaskStatus
from app.domain.providers import AgentRole, RoutingPolicy, role_for_kind

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 6
TOOL_ROUND_MAX_OUTPUT_TOKENS = 12_000
TOOL_ROUND_TIMEOUT_SECONDS = 120
DEADLINE_MARGIN_SECONDS = 45
FINAL_ROUND_MIN_TIMEOUT_SECONDS = 30
OLDER_RESULT_MAX_CHARS = 4_000
OLDER_RESULT_ROWS = 10
COMPOSER_ATTEMPT_RESERVE = 1
MIN_TOKENS_PER_STEP = 8_000
PRIOR_TURNS = 3
PRIOR_ANSWER_CHARS = 1500
STREAM_FLUSH_CHARS = 120
STREAM_FLUSH_SECONDS = 0.25
MIN_ATTEMPTS_PER_TASK = 2
TRIMMED_FINDINGS_LIMITATION = "Some step findings were shortened to fit the answer writer's context, so details from earlier steps may be missing."


class _OrchestrationState(TypedDict):
    action: str

_TASK_TITLES = {
    "data_specialist": "Analysis",
    "reconciliation_specialist": "Reconciliation",
    "evidence_reviewer": "Evidence review",
}





def _compact_older_results(messages: list[dict[str, Any]], keep_from: int) -> None:
    for idx in range(keep_from):
        m = messages[idx]
        if m.get("role") != "tool" or m.get("compacted") or len(str(m.get("content", ""))) <= OLDER_RESULT_MAX_CHARS:
            continue
        try:
            data = json.loads(m["content"])
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict) or not isinstance(data.get("rows"), list):
            continue
        rows = data["rows"][:OLDER_RESULT_ROWS]
        data.update(rows=rows, rows_shown=len(rows), older_result_trimmed=True)
        messages[idx] = {**m, "content": json.dumps(data, default=str), "compacted": True}


_TASK_ID_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_]{0,47}")


def _call_timeout(deadline: float | None, final_round: bool = False) -> int:
    if deadline is None:
        return TOOL_ROUND_TIMEOUT_SECONDS
    floor = FINAL_ROUND_MIN_TIMEOUT_SECONDS if final_round else 10
    return max(floor, min(TOOL_ROUND_TIMEOUT_SECONDS, int(deadline - time.monotonic())))


def _evidence_verdict(claims: list[Claim], flagged: set[str], remediation_claims: set[str]) -> ReviewVerdict:
    gaps: list[str] = []
    for claim in claims:
        if claim.claim_id in flagged or claim.claim_id in remediation_claims:
            continue
        if not claim.evidence_ids:
            flagged.add(claim.claim_id)
            step = claim.claim_id.removeprefix("clm_")
            gaps.append(f"step {step} gave figures without query evidence; re-derive them with run_query_ir and cite evidence ids")
    return ReviewVerdict(approved=not gaps, gaps=tuple(gaps))


def _default_plan(
    question: str,
    mode: RunMode,
    snapshot_id: str,
    semantic_version_id: str,
    revision: int = 1,
) -> PlanDAG:
    tasks = [
        TaskNode(
            task_id="t1_analyze",
            kind="data_specialist",
            objective="Answer the question with figures from the data.",
            dependencies=(),
            snapshot_id=snapshot_id,
            semantic_version_id=semantic_version_id,
            required_inputs=(),
            result_schema="analytical_result_v1",
            priority=2,
            acceptance_criteria=_DEFAULT_ACCEPTANCE["data_specialist"],
        )
    ]
    if mode == RunMode.DEEP:
        tasks.extend(
            [
                TaskNode(
                    task_id="t1_reconcile",
                    kind="reconciliation_specialist",
                    objective="Cross-check the totals behind the question and explain any variances or timing differences.",
                    dependencies=(),
                    snapshot_id=snapshot_id,
                    semantic_version_id=semantic_version_id,
                    required_inputs=(),
                    result_schema="reconciliation_result_v1",
                    priority=2,
                    acceptance_criteria=_DEFAULT_ACCEPTANCE["reconciliation_specialist"],
                ),
                TaskNode(
                    task_id="t2_review",
                    kind="evidence_reviewer",
                    objective="Review the earlier findings: check units, currencies and conflicting figures, re-query anything doubtful, and state what is reliable.",
                    dependencies=("t1_analyze", "t1_reconcile"),
                    snapshot_id=snapshot_id,
                    semantic_version_id=semantic_version_id,
                    required_inputs=("t1_analyze", "t1_reconcile"),
                    result_schema="review_result_v1",
                    priority=1,
                    acceptance_criteria=_DEFAULT_ACCEPTANCE["evidence_reviewer"],
                ),
            ]
        )
    return PlanDAG(
        revision=revision,
        goal=question,
        completion_criteria=("All analytical claims grounded in evidence",),
        tasks=tuple(tasks),
    )


def _serialize_plan(plan: PlanDAG) -> dict[str, Any]:
    return {
        "revision": plan.revision,
        "goal": plan.goal,
        "completion_criteria": list(plan.completion_criteria),
        "tasks": [
            {
                "task_id": task.task_id,
                "kind": task.kind,
                "objective": task.objective,
                "dependencies": list(task.dependencies),
                "snapshot_id": task.snapshot_id,
                "semantic_version_id": task.semantic_version_id,
                "required_inputs": list(task.required_inputs),
                "result_schema": task.result_schema,
                "priority": task.priority,
                "attempt_limit": task.attempt_limit,
                "acceptance_criteria": list(task.acceptance_criteria),
            }
            for task in plan.tasks
        ],
    }


def _deserialize_plan(value: Any) -> PlanDAG | None:
    if not isinstance(value, dict) or not isinstance(value.get("tasks"), list):
        return None
    try:
        plan = PlanDAG(
            revision=int(value.get("revision", 1)),
            goal=str(value.get("goal", "")),
            completion_criteria=tuple(str(item) for item in value.get("completion_criteria", [])),
            tasks=tuple(
                TaskNode(
                    task_id=str(task["task_id"]),
                    kind=str(task["kind"]),
                    objective=str(task["objective"]),
                    dependencies=tuple(str(item) for item in task.get("dependencies", [])),
                    snapshot_id=str(task.get("snapshot_id", "")),
                    semantic_version_id=str(task.get("semantic_version_id", "")),
                    required_inputs=tuple(str(item) for item in task.get("required_inputs", [])),
                    result_schema=str(task["result_schema"]),
                    priority=int(task.get("priority", 1)),
                    attempt_limit=int(task.get("attempt_limit", 3)),
                    acceptance_criteria=tuple(str(item) for item in task.get("acceptance_criteria", [])),
                )
                for task in value["tasks"]
                if isinstance(task, dict)
            ),
        )
        plan.validate_dag()
        return plan if plan.tasks else None
    except (KeyError, TypeError, ValueError):
        return None


def _parse_json_object(text: str) -> dict[str, Any]:
    return extract_json_object(text, error_message="Planner response must contain a JSON object")


_RESULT_SCHEMAS = {
    "data_specialist": "analytical_result_v1",
    "reconciliation_specialist": "reconciliation_result_v1",
    "evidence_reviewer": "review_result_v1",
}
_REMEDIATION_KINDS = ("data_specialist", "reconciliation_specialist")
_DEFAULT_ACCEPTANCE = {
    "data_specialist": ("Every figure comes from a query result and cites its evidence id",),
    "reconciliation_specialist": ("Each variance names both totals and the evidence ids behind them",),
    "evidence_reviewer": ("Returns a verdict that lists every unsupported or conflicting figure",),
}
MAX_TASK_CRITERIA = 3


def _plan_from_agent(
    text: str,
    *,
    question: str,
    mode: RunMode,
    snapshot_id: str,
    semantic_version_id: str,
    revision: int,
    max_tasks: int,
    clarification_answered: bool,
) -> tuple[PlanDAG, bool, list[str]]:
    try:
        draft = _parse_json_object(text)
        if draft.get("needs_clarification") is True and not clarification_answered:
            prompt = str(draft.get("clarification_question") or "").strip()
            if prompt:
                raise ClarificationRequired(prompt[:1000])
        notes: list[str] = []
        raw_tasks = draft.get("tasks")
        if not isinstance(raw_tasks, list) or not 1 <= len(raw_tasks) <= max_tasks:
            raise ValueError("Planner returned an invalid number of tasks")
        raw_task_ids = {
            str(item.get("task_id") or "")
            for item in raw_tasks
            if isinstance(item, dict)
        }
        tasks: list[TaskNode] = []
        for item in raw_tasks:
            if not isinstance(item, dict):
                raise ValueError("Planner returned an invalid task")
            task_id = str(item.get("task_id") or "")
            kind = str(item.get("kind") or "")
            clean_objective = _clean_objective(item.get("objective"))
            dependencies = item.get("dependencies", [])
            required_inputs = item.get("required_inputs", dependencies)
            raw_criteria = item.get("acceptance_criteria")
            listed = [c for c in raw_criteria if isinstance(c, str) and _clean_objective(c)] if isinstance(raw_criteria, list) else []
            task_criteria = tuple(clip_statement(_clean_objective(c), MAX_CRITERION_CHARS) for c in listed[:MAX_TASK_CRITERIA])
            if len(listed) > MAX_TASK_CRITERIA:
                notes.append(f"Step {task_id} listed {len(listed)} acceptance criteria; only the first {MAX_TASK_CRITERIA} were used.")
            if (
                not _TASK_ID_RE.fullmatch(task_id)
                or kind not in _RESULT_SCHEMAS
                or not clean_objective
                or not isinstance(dependencies, list)
                or not isinstance(required_inputs, list)
                or any(not isinstance(value, str) or value not in raw_task_ids for value in dependencies)
                or any(not isinstance(value, str) or value not in dependencies for value in required_inputs)
            ):
                raise ValueError("Planner returned an invalid task definition")
            tasks.append(
                TaskNode(
                    task_id=task_id,
                    kind=kind,
                    objective=clean_objective,
                    dependencies=tuple(str(value) for value in dependencies),
                    snapshot_id=snapshot_id,
                    semantic_version_id=semantic_version_id,
                    required_inputs=tuple(str(value) for value in required_inputs),
                    result_schema=_RESULT_SCHEMAS[kind],
                    priority=1 if kind == TaskKind.EVIDENCE_REVIEWER else 2,
                    acceptance_criteria=task_criteria or _DEFAULT_ACCEPTANCE[kind],
                )
            )
        if not any(task.kind == TaskKind.DATA_SPECIALIST for task in tasks):
            raise ValueError("Planner omitted a data specialist")
        if mode == RunMode.DEEP and not any(task.kind == TaskKind.EVIDENCE_REVIEWER for task in tasks):
            raise ValueError("Planner omitted the evidence reviewer")
        if mode == RunMode.DEEP:
            analytical_ids = {
                task.task_id for task in tasks if task.kind != TaskKind.EVIDENCE_REVIEWER
            }
            reviewers = [task for task in tasks if task.kind == TaskKind.EVIDENCE_REVIEWER]
            if len(reviewers) != 1 or not analytical_ids.issubset(set(reviewers[0].dependencies)):
                raise ValueError("Planner reviewer must depend on all analytical tasks")
        criteria = draft.get("completion_criteria", ["All analytical claims grounded in evidence"])
        if not isinstance(criteria, list):
            raise ValueError("Planner returned invalid completion criteria")
        plan = PlanDAG(
            revision=revision,
            goal=str(draft.get("goal") or question)[:1000],
            completion_criteria=tuple(clip_statement(str(value), MAX_CRITERION_CHARS) for value in criteria[:MAX_COMPLETION_CRITERIA]),
            tasks=tuple(tasks),
        )
        plan.validate_dag()
        if len(criteria) > MAX_COMPLETION_CRITERIA:
            notes.append(f"The plan listed {len(criteria)} completion criteria; only the first {MAX_COMPLETION_CRITERIA} were checked.")
        return plan, False, notes
    except ClarificationRequired:
        raise
    except (TypeError, ValueError, json.JSONDecodeError):
        return _default_plan(question, mode, snapshot_id, semantic_version_id, revision), True, []


def _remediation_from_draft(draft: dict[str, Any]) -> tuple[str, str] | str | None:
    if draft.get("action") == "stop":
        return _clean_objective(draft.get("reason")) or "no reason given"
    kind = str(draft.get("kind") or "")
    objective = _clean_objective(draft.get("objective"))
    if draft.get("action") != "remediate" or kind not in _REMEDIATION_KINDS or not objective:
        return None
    return kind, objective


class InvestigationOrchestrator:
    def __init__(self, container: Any, query_engine: SnapshotQueryPort | None = None) -> None:
        self._container = container
        self._query_engine = query_engine
        self._deep_agent_runner = DeepAgentTaskRunner(container.routing_service)

    def _engine(self) -> SnapshotQueryPort:
        if self._query_engine is None:
            self._query_engine = SnapshotQueryEngine(self._container.artifacts, SqlGlotQueryCompiler())
        return self._query_engine

    def _attempts_left(self, run_id: str, max_attempts: int) -> int:
        used = int(self._container.budget_ledger.get_run_totals(run_id).get("used_attempts", 0))
        return max_attempts - used

    def _run_specialist_task_legacy(
        self,
        *,
        task: TaskNode,
        question: str,
        run_id: str,
        policy: RoutingPolicy,
        generation: int,
        remaining_tools: int,
        toolbox: AnalyticsToolbox,
        prior_findings: dict[str, str],
        max_model_attempts: int,
        deadline: float | None = None,
        on_tool: Callable[[dict[str, Any], dict[str, Any] | None], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
        token_budget: int | None = None,
    ) -> SpecialistOutcome:
        role = role_for_kind(task.kind)
        tools = toolbox.definitions if toolbox.available else ()
        bridge = GatewayChatModelBridge(self._container.routing_service, policy, run_id, role)

        clean_task_obj = _clean_objective(task.objective)
        template = prompts.SPECIALIST if tools else prompts.SPECIALIST_NO_DATA
        harness = build_restricted_deep_agent_config(
            role=role,
            allowed_tools=tools,
            system_prompt=template.text,
        )
        child_ctx = compact_child_context(
            BoundedChildContext(
                task_id=task.task_id,
                objective=clean_task_obj,
                snapshot_id=task.snapshot_id,
                semantic_version_id=task.semantic_version_id,
                approved_schema_summary={"snapshot_id": task.snapshot_id},
                relevant_evidence_ids=task.required_inputs,
                unresolved_contradictions=(),
                hard_constraints=("No raw SQL", "Use Decimal for money"),
                remaining_tool_calls=remaining_tools,
                acceptance_criteria="; ".join(task.acceptance_criteria) or "All numbers backed by evidence_ids",
                bounded_summary=clean_task_obj,
            ),
            gateway_bridge=None,
        )

        context = specialist_context(
            task_summary=child_ctx.bounded_summary,
            criteria=child_ctx.acceptance_criteria,
            question=_clean_text(question, 4000),
            prior_findings=[(dep, str(prior_findings[dep]).strip()) for dep in task.required_inputs if prior_findings.get(dep)],
            tool_calls=remaining_tools,
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": f"{harness['system_prompt']}\n\nTask {task.task_id}: {clean_task_obj}\nDataset snapshot: {task.snapshot_id}"},
            {"role": "user", "content": context.text},
        ]

        trace: list[dict[str, Any]] = []
        evidence: list[dict[str, Any]] = []
        error: str | None = None
        text = ""
        provider_str = "none"
        model_str = "setup_required"
        used_tools = False
        stopped_early: str | None = None
        tokens_used = 0
        try:
            for round_idx in range(MAX_TOOL_ROUNDS + 1):
                if round_idx > 0 and should_cancel is not None and should_cancel():
                    stopped_early = "cancelled"
                    break
                final_round = round_idx == MAX_TOOL_ROUNDS or (
                    used_tools
                    and (
                        toolbox.budget.exhausted
                        or toolbox.budget.analytical_exhausted
                        or len(trace) >= remaining_tools
                        or (token_budget is not None and tokens_used >= token_budget)
                        or self._attempts_left(run_id, max_model_attempts) <= 1
                        or self._container.routing_service.tokens_left(run_id)
                        < 2 * (estimate_input_tokens(tuple(messages), tools) + TOOL_ROUND_MAX_OUTPUT_TOKENS)
                        or (deadline is not None and time.monotonic() >= deadline - DEADLINE_MARGIN_SECONDS)
                    )
                )
                if final_round and used_tools:
                    messages.append({"role": "user", "content": prompts.FINAL_ROUND.text})
                offer = tools if (tools and (not final_round or used_tools)) else ()
                pinned = model_str if used_tools else None
                call_kwargs: dict[str, Any] = {
                    "messages": tuple(messages),
                    "tools": offer,
                    "max_output_tokens": TOOL_ROUND_MAX_OUTPUT_TOKENS if offer else None,
                    "timeout_seconds": _call_timeout(deadline, final_round) if offer else None,
                    "tool_choice": "none" if (offer and final_round) else None,
                }
                try:
                    with using_prompt(template):
                        resp = bridge.invoke_messages(
                            request_id=f"req_{uuid.uuid4().hex[:8]}", manual_model_id=pinned, **call_kwargs
                        )
                except Exception as exc:  # noqa: BLE001
                    if not used_tools:
                        raise
                    resp = None
                    past_deadline = deadline is not None and time.monotonic() >= deadline
                    if pinned and not past_deadline and not isinstance(exc, BudgetExhaustedError):
                        if offer:
                            call_kwargs["timeout_seconds"] = _call_timeout(deadline, final_round)
                        try:
                            with using_prompt(template):
                                resp = bridge.invoke_messages(request_id=f"req_{uuid.uuid4().hex[:8]}", **call_kwargs)
                        except Exception as retry_exc:  # noqa: BLE001
                            exc = retry_exc
                    if resp is None:
                        stopped_early = public_message(exc)
                        break
                assert resp is not None
                tokens_used += resp.input_tokens + resp.output_tokens
                provider_str = resp.provider_id.value
                model_str = resp.model_id
                text = resp.text
                if not resp.tool_calls or not offer or final_round:
                    break
                used_tools = True
                _compact_older_results(messages, len(messages))
                calls = [
                    {"id": c.call_id or f"call_{round_idx}_{i}", "name": c.tool_name, "arguments": c.arguments}
                    for i, c in enumerate(resp.tool_calls)
                ]
                messages.append(
                    {
                        "role": "assistant",
                        "content": resp.text,
                        "tool_calls": calls,
                        "provider_state": {"provider": provider_str, "continuation": resp.continuation_state},
                    }
                )
                cap = DEFAULT_GLOBAL_LIMITS.max_proposed_tool_calls_per_response
                for n, (call, raw) in enumerate(zip(calls, resp.tool_calls)):
                    if should_cancel is not None and should_cancel():
                        stopped_early = "cancelled"
                        outcome = toolbox.failure(raw.tool_name, "The run was cancelled before this query ran.", ToolErrorCode.CANCELLED)
                    elif len(trace) >= remaining_tools:
                        outcome = toolbox.failure(
                            raw.tool_name,
                            f"This step used its share of {remaining_tools} tool calls; answer with the results you have.",
                            ToolErrorCode.LIMIT_EXCEEDED,
                        )
                    elif n >= cap:
                        outcome = toolbox.failure(
                            raw.tool_name, f"Too many tool calls in one turn; only the first {cap} were run.", ToolErrorCode.LIMIT_EXCEEDED
                        )
                    else:
                        try:
                            outcome = toolbox.execute(raw)
                        except Exception as exc:  # noqa: BLE001
                            outcome = toolbox.failure(raw.tool_name, f"Tool failed: {public_message(exc)}")
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "name": call["name"],
                            "content": outcome.content,
                            "is_error": outcome.is_error,
                        }
                    )
                    entry = {**outcome.summary, "round": round_idx + 1}
                    trace.append(entry)
                    if outcome.evidence:
                        evidence.append(outcome.evidence)
                    if on_tool is not None:
                        on_tool(entry, outcome.evidence)
                if stopped_early == "cancelled":
                    break
            known_ids = {e["evidence_id"] for e in evidence if e.get("evidence_id")}
            # Any id already cited by an earlier step of this run is real (those texts were filtered too),
            # so a reviewer step may cite them even when they are not among its direct inputs.
            for finding in prior_findings.values():
                known_ids.update(_EVIDENCE_ID_RE.findall(finding))
            text, _dropped_citations = _strip_unknown_citations(text, known_ids)
            if stopped_early:
                # The raw reason stays in the step payload (technical details); the answer gets plain words.
                reason = (
                    "the run was cancelled"
                    if stopped_early == "cancelled"
                    else "a model call failed or the step's budget ran out"
                )
                note = f"_This step stopped early ({reason}) after {len(trace)} queries; the figures below are partial._"
                text = f"{note}\n\n{text}".strip() if text.strip() else note
            elif not text.strip() and used_tools:
                text = "This step ran out of tool budget before it could write an answer."
            elif not text.strip():
                raise RuntimeError("The model returned an empty answer")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Task %s failed", task.task_id, exc_info=True)
            error = public_message(exc)
            text = ""
            provider_str = "none"
            model_str = "setup_required"

        evidence_ids = tuple(dict.fromkeys(e["evidence_id"] for e in evidence))
        payload = {
            "text": text,
            "evidence_id": evidence_ids[0] if evidence_ids else None,
            "stopped_early": stopped_early,
            "evidence": evidence,
            "tool_calls": len(trace),
            "harness_profile": harness["harness_profile_id"],
            "error": error,
        }
        r_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()
        attempt = TaskAttemptResult(
            task_id=task.task_id,
            attempt=1,
            generation=generation,
            result_hash=r_hash,
            payload=payload,
            evidence_ids=evidence_ids,
        )
        claim = None if error else Claim(
            claim_id=f"clm_{task.task_id}",
            label="observation" if evidence and not stopped_early else "unverified_observation",
            statement=clip_statement(text, CLAIM_STATEMENT_CHARS) or "Completed bounded step",
            value=None,
            unit=None,
            currency=None,
            evidence_ids=evidence_ids,
        )
        return SpecialistOutcome(attempt, claim, provider_str, model_str, error, trace)

    def _run_specialist_task(
        self,
        *,
        task: TaskNode,
        question: str,
        run_id: str,
        policy: RoutingPolicy,
        generation: int,
        remaining_tools: int,
        toolbox: AnalyticsToolbox,
        prior_findings: dict[str, str],
        max_model_attempts: int,
        deadline: float | None = None,
        on_tool: Callable[[dict[str, Any], dict[str, Any] | None], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
        evidence_lookup: Callable[[str], dict[str, Any] | None] | None = None,
        task_history_loader: Callable[[str], tuple[list[dict[str, Any]], list[dict[str, Any]]]] | None = None,
        token_budget: int | None = None,
    ) -> SpecialistOutcome:
        if agent_runtime() == AgentRuntime.LEGACY:
            return self._run_specialist_task_legacy(
                task=task,
                question=question,
                run_id=run_id,
                policy=policy,
                generation=generation,
                remaining_tools=remaining_tools,
                toolbox=toolbox,
                prior_findings=prior_findings,
                max_model_attempts=max_model_attempts,
                deadline=deadline,
                on_tool=on_tool,
                should_cancel=should_cancel,
                token_budget=token_budget,
            )
        return self._deep_agent_runner.run(
            task=task,
            question=question,
            run_id=run_id,
            policy=policy,
            generation=generation,
            remaining_tools=remaining_tools,
            toolbox=toolbox,
            prior_findings=prior_findings,
            deadline=deadline,
            on_tool=on_tool,
            should_cancel=should_cancel,
            evidence_lookup=evidence_lookup,
            task_history_loader=task_history_loader,
            token_budget=token_budget,
        )

    def _record_tool_execution(
        self, run_id: str, task_id: str, entry: dict[str, Any], evidence: dict[str, Any] | None
    ) -> None:
        execution = ToolExecution(
            execution_id=f"tex_{uuid.uuid4().hex[:16]}",
            run_id=run_id,
            task_id=task_id,
            tool=str(entry.get("tool") or "")[:64],
            tool_version=str(entry.get("tool_version") or ""),
            args_fingerprint=str(entry.get("args_fingerprint") or ""),
            status=str(entry.get("status") or ("success" if entry.get("ok") else "failed")),
            error_code=entry.get("code"),
            duration_ms=int(entry.get("duration_ms") or 0),
            evidence_id=(evidence or {}).get("evidence_id") or entry.get("evidence_id"),
            cache_hit=bool(entry.get("cache_hit")),
        )
        try:
            self._container.tool_executions.record(execution)
        except Exception:  # noqa: BLE001
            logger.exception("Could not record tool execution for run %s", run_id)

    def _raw_record_egress_allowed(self, policy: RoutingPolicy) -> bool:
        profiles = self._container.provider_profiles
        return bool(policy.allowed_providers) and all(
            profiles[provider].allow_raw_record_egress if provider in profiles else False
            for provider in policy.allowed_providers
        )

    def execute_local(
        self,
        *,
        run_id: str,
        workspace_id: str,
        question: str,
        mode: RunMode,
        snapshot_id: str = "",
        semantic_version_id: str = "",
        snapshot: dict[str, Any] | None = None,
        semantics: dict[str, Any] | None = None,
        emit_event: Callable[[RunEvent], None] | None = None,
        policy_override: RoutingPolicy | None = None,
        replan_evaluator: Callable[[list[Claim], int], ReviewVerdict] | None = None,
        should_cancel: Callable[[], bool] | None = None,
        on_evidence: Callable[[dict[str, Any]], None] | None = None,
        initial_tool_invocations: int = 0,
        initial_analytical_calls: int = 0,
        evidence_lookup: Callable[[str], dict[str, Any] | None] | None = None,
        task_history_loader: Callable[[str], tuple[list[dict[str, Any]], list[dict[str, Any]]]] | None = None,
        clarification_answer: str | None = None,
        prior_turns: list[dict[str, str]] | None = None,
        initial_plan: Any = None,
        on_plan: Callable[[dict[str, Any]], None] | None = None,
        business_timezone: str = DEFAULT_BUSINESS_TIMEZONE,
    ) -> Answer:
        seq = 1
        emit_lock = threading.Lock()
        run_evidence: list[dict[str, Any]] = []

        def _cancelled() -> bool:
            return bool(should_cancel and should_cancel())

        def _emit(etype: EventType, payload: dict[str, Any]) -> None:
            nonlocal seq
            with emit_lock:
                seq += 1
                evt = RunEvent(
                    schema_version=1,
                    event_id=f"evt_{uuid.uuid4().hex[:10]}",
                    sequence=seq,
                    workspace_id=workspace_id,
                    run_id=run_id,
                    type=etype,
                    occurred_at=datetime.now(timezone.utc),
                    payload=payload,
                )
                if emit_event is not None:
                    emit_event(evt)

        def _tool_callback(task_id: str) -> Callable[[dict[str, Any], dict[str, Any] | None], None]:
            def _on_tool(entry: dict[str, Any], evidence: dict[str, Any] | None) -> None:
                self._record_tool_execution(run_id, task_id, entry, evidence)
                if evidence is not None:
                    with emit_lock:
                        run_evidence.append(evidence)
                    if on_evidence is not None:
                        on_evidence({**evidence, "task_id": task_id})
                _emit(EventType.TOOL_RESULT, {"task_id": task_id, **entry})

            return _on_tool

        clean_question = _clean_text(question, DEFAULT_GLOBAL_LIMITS.max_question_chars)
        question_for_agents = clean_question
        recent_turns = [
            {"question": _clean_text(turn.get("question"), 500), "answer": _clean_text(turn.get("answer"), PRIOR_ANSWER_CHARS)}
            for turn in (prior_turns or [])[-PRIOR_TURNS:]
        ]
        if recent_turns:
            turns = "\n\n".join(f"Q: {turn['question']}\nA: {turn['answer']}" for turn in recent_turns)
            question_for_agents = f"{clean_question}\n\n<previous_turns>\n{turns}\n</previous_turns>"
        clean_clarification: str | None = None
        if clarification_answer:
            clean_clarification = _clean_text(clarification_answer, DEFAULT_GLOBAL_LIMITS.max_clarification_chars)
            question_for_agents = (
                f"{question_for_agents}\n\n"
                f"<user_clarification>\n{clean_clarification}\n</user_clarification>"
            )

        _emit(EventType.RUN_STATUS, {"status": RunStatus.RUNNING.value, "mode": mode.value})

        supervisor = DeterministicSupervisor(mode=mode, run_id=run_id)
        limits = supervisor.state.limits
        deadline = time.monotonic() + limits.active_seconds
        policy = policy_override or self._container.default_routing_policy
        timed_out = False
        toolbox = AnalyticsToolbox(
            engine=self._engine(),
            snapshot=snapshot,
            semantics=semantics,
            budget=ToolBudget(
                limits.max_tool_invocations,
                limits.analytical_calls,
                used_invocations=initial_tool_invocations,
                used_analytical=initial_analytical_calls,
            ),
            raw_record_egress=self._raw_record_egress_allowed(policy),
            business_timezone=business_timezone,
        )

        self._container.budget_ledger.configure_run(
            run_id,
            max_attempts=limits.total_model_attempts,
            max_tokens=limits.total_tokens,
            max_cost_usd=policy.hard_cost_ceiling_usd,
            finalization_reserve_tokens=limits.finalization_reserve_tokens,
        )
        use_deep_agents = agent_runtime() == AgentRuntime.DEEP_AGENTS
        saved_route = initial_plan.get("route") if isinstance(initial_plan, dict) else None
        if saved_route in ExecutionRoute._value2member_map_:
            route = RouteDecision(ExecutionRoute(saved_route), str(initial_plan.get("route_reason") or ""))
        else:
            route = choose_route(
                question=clean_question,
                mode=mode,
                snapshot=snapshot,
                has_prior_turns=bool(recent_turns),
                has_clarification=bool(clean_clarification),
            )
        direct = route.route == ExecutionRoute.DIRECT_QUERY
        attempt_reserve = COMPOSER_ATTEMPT_RESERVE if use_deep_agents and not direct else 0

        def _remaining_attempts() -> int:
            used = int(self._container.budget_ledger.get_run_totals(run_id).get("used_attempts", 0))
            return limits.total_model_attempts - used

        plan = initial_plan if isinstance(initial_plan, PlanDAG) else _deserialize_plan(initial_plan)
        plan_fell_back = False
        plan_notes: list[str] = []
        plan_limitations: list[str] = []
        if plan is None:
            if use_deep_agents and not direct:
                planner_task_limit = limits.planner_task_limit
                planner_input = {
                    "question": clean_question,
                    "mode": mode.value,
                    "snapshot_id": snapshot_id,
                    "semantic_version_id": semantic_version_id,
                    "clarification_answer": clean_clarification,
                    "previous_turns": recent_turns,
                    "max_tasks": planner_task_limit,
                }
                with using_prompt(prompts.PLANNER):
                    planner_text, planner_provider, planner_model = self._deep_agent_runner.run_control_agent(
                        role=AgentRole.PLANNER,
                        system_prompt=prompts.PLANNER.text,
                        user_prompt=json.dumps(planner_input, ensure_ascii=False),
                        run_id=run_id,
                        policy=policy,
                        toolbox=toolbox,
                        on_tool=_tool_callback("planner"),
                        deadline=deadline,
                        should_cancel=_cancelled,
                    )
                _emit(
                    EventType.ROUTE_SELECTED,
                    {"task_id": "planner", "provider": planner_provider, "model": planner_model},
                )
                plan_kwargs: dict[str, Any] = {
                    "question": question,
                    "mode": mode,
                    "snapshot_id": snapshot_id,
                    "semantic_version_id": semantic_version_id,
                    "revision": 1,
                    "max_tasks": planner_task_limit,
                    "clarification_answered": bool(clarification_answer),
                }
                plan, plan_fell_back, plan_notes = _plan_from_agent(planner_text, **plan_kwargs)
                if plan_fell_back and _remaining_attempts() > attempt_reserve + MIN_ATTEMPTS_PER_TASK:
                    try:
                        with using_prompt(prompts.PLAN_REPAIR):
                            repaired, _, _ = self._deep_agent_runner.run_structured(
                                role=AgentRole.PLANNER,
                                system_prompt=prompts.PLAN_REPAIR.with_context(f"Use at most {planner_task_limit} tasks. Mode is {mode.value}."),
                                user_prompt=fit_text(planner_text, REPAIR_INPUT_TOKENS),
                                schema=PLAN_SCHEMA,
                                run_id=run_id,
                                policy=policy,
                                deadline=deadline,
                            )
                        plan, plan_fell_back, plan_notes = _plan_from_agent(json.dumps(repaired), **plan_kwargs)
                    except ClarificationRequired:
                        raise
                    except Exception:  # noqa: BLE001
                        logger.warning("Plan repair failed for run %s", run_id, exc_info=True)
            else:
                plan = _default_plan(question, mode, snapshot_id, semantic_version_id)
        plan_limitations.extend(plan_notes)
        if plan_fell_back:
            plan_limitations.append("The planner's reply couldn't be used, so the standard plan ran instead.")
        plan.validate_dag()
        if on_plan is not None:
            on_plan({**_serialize_plan(plan), "route": route.route.value, "route_reason": route.reason})
        supervisor.accept_plan(plan)
        if plan.revision > 1:
            supervisor.resume_revision(plan.revision)
        fingerprints = task_fingerprints(plan, question_for_agents)
        ledger = TaskLedger(self._container.task_store, run_id)
        ledger.add_plan(plan, fingerprints)
        planned_criteria = plan.completion_criteria
        _emit(
            EventType.PLAN_UPDATED,
            {
                "revision": plan.revision,
                "tasks": [t.task_id for t in plan.tasks],
                "fallback": plan_fell_back,
                "route": route.route.value,
                "route_reason": route.reason,
            },
        )

        collected_claims: list[Claim] = []
        summary_parts: list[str] = []
        findings: dict[str, str] = {}
        task_errors: list[str] = []
        flagged: set[str] = set()
        latest_review_verdict: ReviewVerdict | None = None
        final_review_verdict: ReviewVerdict | None = None
        budget_limitations: list[str] = []
        cancelled = False

        ready_batch: tuple[TaskNode, ...] = ()
        batch_outcomes: list[tuple[TaskNode, SpecialistOutcome]] = []

        budget_warned: set[str] = set()
        task_budgets: dict[str, TaskBudget] = {}

        for task in plan.tasks:
            record = ledger.records[task.task_id]
            if record.status == TaskStatus.COMPLETED.value and record.input_fingerprint == fingerprints[task.task_id]:
                supervisor.record_task_result(restored_attempt(record, supervisor.state.generation))
                text = str((record.result or {}).get("text") or "")
                findings[task.task_id] = text
                summary_parts.append(text)
                restored = restored_claim(record)
                if restored is not None and task.kind != TaskKind.EVIDENCE_REVIEWER:
                    collected_claims.append(restored)
                verdict = restored_verdict(record)
                if verdict is not None and task.kind == TaskKind.EVIDENCE_REVIEWER:
                    latest_review_verdict = verdict
                for evidence_id in record.evidence_ids if evidence_lookup is not None else ():
                    item = evidence_lookup(evidence_id) if evidence_lookup is not None else None
                    if item is not None and all(e.get("evidence_id") != evidence_id for e in run_evidence):
                        run_evidence.append(item)
                _emit(
                    EventType.TASK_STATUS,
                    {"task_id": task.task_id, "status": "completed", "restored": True, "evidence_ids": list(record.evidence_ids)},
                )
            elif record.status in (TaskStatus.FAILED.value, TaskStatus.CANCELLED.value, TaskStatus.SUPERSEDED.value):
                supervisor.restore_status(task.task_id, TaskStatus(record.status))
                if record.status == TaskStatus.FAILED.value:
                    task_errors.append(f"{task.task_id}: {record.error or 'failed'}")

        def _check_budget() -> None:
            totals = self._container.budget_ledger.get_run_totals(run_id)
            usage = {
                "model_calls": (int(str(totals.get("used_attempts", 0))), limits.total_model_attempts),
                "tokens": (int(str(totals.get("total_tokens", 0))), limits.total_tokens),
            }
            for resource, (used, limit) in usage.items():
                if resource not in budget_warned and limit and used >= limits.warning_fraction * limit:
                    budget_warned.add(resource)
                    _emit(EventType.BUDGET_WARNING, {"resource": resource, "used": used, "limit": limit})

        def _stop_scheduling(reason: str) -> None:
            for task_id in supervisor.abandon_pending():
                _emit(EventType.TASK_STATUS, {"task_id": task_id, "status": "cancelled", "error": reason})
            ledger.sync_statuses(supervisor.state.task_statuses)
            budget_limitations.append(reason)

        def prepare_batch(state: _OrchestrationState) -> _OrchestrationState:
            nonlocal ready_batch, cancelled, timed_out
            if _cancelled():
                cancelled = True
                return {"action": "finish"}
            if time.monotonic() >= deadline:
                timed_out = True
                return {"action": "finish"}
            _check_budget()
            if supervisor.all_tasks_joined():
                return {"action": "review"}
            attempts_for_tasks = _remaining_attempts() - attempt_reserve
            if attempts_for_tasks <= 0:
                _stop_scheduling(
                    f"The run used its {limits.total_model_attempts} model calls, so some planned steps did not run."
                )
                return {"action": "finish"}
            if self._container.routing_service.tokens_left(run_id) < MIN_TOKENS_PER_STEP:
                _stop_scheduling(f"The run used its {limits.total_tokens:,} token budget, so some planned steps did not run.")
                return {"action": "finish"}
            try:
                ready_batch = supervisor.next_ready_tasks(max_tasks=attempts_for_tasks)
            except BudgetExhaustedError:
                _stop_scheduling(
                    f"The run reached its limit of {limits.lifetime_specialist_instances} analysis steps, "
                    "so some planned steps did not run."
                )
                return {"action": "finish"}
            if not ready_batch:
                task_errors.append("The plan has pending work but no dependency-ready tasks.")
                return {"action": "finish"}
            unfinished = sum(1 for st in supervisor.state.task_statuses.values() if st in (TaskStatus.PENDING, TaskStatus.RUNNING))
            tokens_left = self._container.routing_service.tokens_left(run_id)
            for task in ready_batch:
                task_budgets[task.task_id] = budget_slice(
                    tokens_left=tokens_left,
                    reserve_tokens=limits.finalization_reserve_tokens if attempt_reserve else 0,
                    tools_left=limits.max_tool_invocations - toolbox.budget.used,
                    unfinished=unfinished,
                    min_tokens=MIN_TOKENS_PER_STEP,
                )
                ledger.move(
                    task.task_id,
                    TaskStatus.RUNNING,
                    attempt=ledger.records[task.task_id].attempt + 1,
                    budget=task_budgets[task.task_id],
                    input_fingerprint=fingerprints[task.task_id],
                )
                _emit(
                    EventType.TASK_STATUS,
                    {
                        "task_id": task.task_id,
                        "status": "running",
                        "budget": {"tokens": task_budgets[task.task_id].tokens, "tool_calls": task_budgets[task.task_id].tool_calls},
                    },
                )
            return {"action": "dispatch"}

        def dispatch_batch(state: _OrchestrationState) -> _OrchestrationState:
            nonlocal batch_outcomes
            with ThreadPoolExecutor(max_workers=len(ready_batch)) as pool:
                futures = [
                    pool.submit(
                        contextvars.copy_context().run,
                        self._run_specialist_task,
                        task=task,
                        question=question_for_agents,
                        run_id=run_id,
                        policy=policy,
                        generation=supervisor.state.generation,
                        remaining_tools=task_budgets[task.task_id].tool_calls,
                        token_budget=task_budgets[task.task_id].tokens,
                        toolbox=toolbox,
                        prior_findings=dict(findings),
                        max_model_attempts=limits.total_model_attempts,
                        deadline=deadline,
                        on_tool=_tool_callback(task.task_id),
                        should_cancel=_cancelled,
                        evidence_lookup=evidence_lookup,
                        task_history_loader=task_history_loader,
                    )
                    for task in ready_batch
                ]
                batch_outcomes = [
                    (task, future.result()) for task, future in zip(ready_batch, futures)
                ]
            return {"action": "record"}

        def record_batch(state: _OrchestrationState) -> _OrchestrationState:
            nonlocal latest_review_verdict
            for task, outcome in batch_outcomes:
                supervisor.record_task_result(outcome.attempt, failed=bool(outcome.error))
                ledger.move(
                    task.task_id,
                    TaskStatus.FAILED if outcome.error else TaskStatus.COMPLETED,
                    evidence_ids=outcome.attempt.evidence_ids,
                    result=outcome_result(outcome),
                    error=outcome.error,
                )
                if outcome.error:
                    task_errors.append(f"{task.task_id}: {outcome.error}")
                    if task.kind == TaskKind.EVIDENCE_REVIEWER:
                        latest_review_verdict = ReviewVerdict(
                            approved=False,
                            gaps=("The evidence review failed and must be repeated.",),
                        )
                    _emit(
                        EventType.TASK_STATUS,
                        {"task_id": task.task_id, "status": "failed", "error": outcome.error[:500]},
                    )
                    continue
                if task.kind == TaskKind.EVIDENCE_REVIEWER and outcome.review_verdict is not None:
                    latest_review_verdict = outcome.review_verdict
                if outcome.claim is not None and task.kind != TaskKind.EVIDENCE_REVIEWER:
                    collected_claims.append(outcome.claim)
                text = str(outcome.attempt.payload["text"])
                findings[task.task_id] = text
                summary_parts.append(text)
                _emit(
                    EventType.ROUTE_SELECTED,
                    {"task_id": task.task_id, "provider": outcome.provider, "model": outcome.model},
                )
                delta = (
                    f"### {_TASK_TITLES.get(task.kind, task.task_id)}\n{text}\n\n"
                    if mode == RunMode.DEEP
                    else f"{text}\n\n"
                )
                _emit(EventType.ANSWER_DELTA, {"text": delta, "draft_id": f"draft_{task.task_id}"})
                _emit(
                    EventType.TASK_STATUS,
                    {
                        "task_id": task.task_id,
                        "status": "completed",
                        "tool_calls": len(outcome.tool_trace),
                        "evidence_ids": list(outcome.attempt.evidence_ids),
                        "review_verdict": (
                            {
                                "approved": outcome.review_verdict.approved,
                                "gaps": list(outcome.review_verdict.gaps),
                                "contradictions": list(outcome.review_verdict.contradictions),
                            }
                            if outcome.review_verdict is not None
                            else None
                        ),
                    },
                )
            return {"action": "prepare"}

        def review_and_replan(state: _OrchestrationState) -> _OrchestrationState:
            nonlocal cancelled, timed_out, final_review_verdict
            if _cancelled():
                cancelled = True
                return {"action": "finish"}
            if time.monotonic() >= deadline:
                timed_out = True
                return {"action": "finish"}
            if mode != RunMode.DEEP:
                return {"action": "finish"}
            if replan_evaluator is not None:
                verdict = replan_evaluator(collected_claims, supervisor.state.replan_count)
            else:
                remediation_claims = {f"clm_{task_id}" for task_id, record in ledger.records.items() if record.plan_revision > 1}
                evidence_verdict = _evidence_verdict(collected_claims, flagged, remediation_claims)
                if latest_review_verdict is None:
                    verdict = evidence_verdict
                else:
                    gaps = tuple(
                        dict.fromkeys((*latest_review_verdict.gaps, *latest_review_verdict.contradictions, *evidence_verdict.gaps))
                    )
                    verdict = ReviewVerdict(
                        approved=latest_review_verdict.approved and evidence_verdict.approved,
                        gaps=gaps,
                        contradictions=latest_review_verdict.contradictions,
                    )
            final_review_verdict = verdict
            if verdict.approved or supervisor.state.replan_count >= limits.max_replans:
                return {"action": "finish"}
            planner_call = 1 if use_deep_agents else 0
            if not supervisor.can_accept_replan(REPLAN_TASKS) or (
                _remaining_attempts() < REPLAN_TASKS * MIN_ATTEMPTS_PER_TASK + attempt_reserve + planner_call
            ):
                return {"action": "finish"}

            bounded_gaps = list(bounded_items(verdict.gaps, MAX_REVIEW_ITEMS, MAX_REVIEW_ITEM_CHARS))
            if not bounded_gaps:
                bounded_gaps = ["additional evidence is required"]
            remediation_kind = "data_specialist"
            remediation_objective = f"Resolve these evidence gaps with new queries: {'; '.join(bounded_gaps)}"
            replan_fell_back = False
            if use_deep_agents:
                replan_input = {
                    "question": question_for_agents,
                    "gaps": bounded_gaps,
                    "contradictions": list(bounded_items(verdict.contradictions, MAX_REVIEW_ITEMS, MAX_REVIEW_ITEM_CHARS)),
                    "findings": dict(zip(findings, (s.text for s in composer_findings(list(findings.values()), REPLAN_FINDINGS_TOKENS).sections))),
                }
                try:
                    with using_prompt(prompts.REPLAN):
                        replan_draft, _, _ = self._deep_agent_runner.run_structured(
                            role=AgentRole.PLANNER,
                            system_prompt=prompts.REPLAN.text,
                            user_prompt=json.dumps(replan_input, ensure_ascii=False, default=str),
                            schema=REPLAN_SCHEMA,
                            run_id=run_id,
                            policy=policy,
                            deadline=deadline,
                        )
                    decision = _remediation_from_draft(replan_draft)
                except Exception:  # noqa: BLE001
                    logger.warning("Replanning call failed for run %s", run_id, exc_info=True)
                    decision = None
                if isinstance(decision, str):
                    plan_limitations.append(f"The planner stopped remediation: {decision}")
                    return {"action": "finish"}
                if decision is None:
                    replan_fell_back = True
                else:
                    remediation_kind, remediation_objective = decision
            replan_idx = supervisor.state.replan_count + 2
            existing_tasks = supervisor.state.plan.tasks if supervisor.state.plan else ()
            existing_ids = {task.task_id for task in existing_tasks}
            remediation_index = replan_idx
            followup_task_id = f"t{remediation_index}_remediate"
            while followup_task_id in existing_ids:
                remediation_index += 1
                followup_task_id = f"t{remediation_index}_remediate"
            followup_task = TaskNode(
                task_id=followup_task_id,
                kind=remediation_kind,
                objective=remediation_objective,
                dependencies=(),
                snapshot_id=snapshot_id,
                semantic_version_id=semantic_version_id,
                required_inputs=(),
                result_schema=_RESULT_SCHEMAS[remediation_kind],
                priority=2,
                acceptance_criteria=_DEFAULT_ACCEPTANCE[remediation_kind],
            )
            review_index = remediation_index + 1
            review_task_id = f"t{review_index}_review"
            while review_task_id in existing_ids or review_task_id == followup_task.task_id:
                review_index += 1
                review_task_id = f"t{review_index}_review"
            review_dependencies = tuple(
                task.task_id
                for task in existing_tasks
                if task.kind != TaskKind.EVIDENCE_REVIEWER
                and supervisor.state.task_statuses.get(task.task_id) == TaskStatus.COMPLETED
            ) + (followup_task.task_id,)
            followup_review = TaskNode(
                task_id=review_task_id,
                kind="evidence_reviewer",
                objective="Reassess all prior findings after the remediation task; return a structured approval verdict and any remaining gaps.",
                dependencies=review_dependencies,
                snapshot_id=snapshot_id,
                semantic_version_id=semantic_version_id,
                required_inputs=review_dependencies,
                result_schema="review_result_v1",
                priority=1,
                acceptance_criteria=_DEFAULT_ACCEPTANCE["evidence_reviewer"],
            )
            new_plan = PlanDAG(
                revision=replan_idx,
                goal=question,
                completion_criteria=("Resolve review gaps",),
                tasks=(*existing_tasks, followup_task, followup_review),
            )
            try:
                supervisor.accept_plan(new_plan, is_replan=True)
            except BudgetExhaustedError as exc:
                task_errors.append(str(exc))
                return {"action": "finish"}
            fingerprints.update(task_fingerprints(new_plan, question_for_agents))
            ledger.add_plan(new_plan, fingerprints)
            ledger.sync_statuses(supervisor.state.task_statuses)
            if on_plan is not None:
                on_plan({**_serialize_plan(new_plan), "route": route.route.value, "route_reason": route.reason})
            _emit(
                EventType.PLAN_UPDATED,
                {"revision": replan_idx, "tasks": [task.task_id for task in new_plan.tasks], "fallback": replan_fell_back},
            )
            return {"action": "prepare"}

        workflow = StateGraph(_OrchestrationState, input_schema=_OrchestrationState)
        workflow.add_node("prepare", prepare_batch)
        workflow.add_node("dispatch", dispatch_batch)
        workflow.add_node("record", record_batch)
        workflow.add_node("review", review_and_replan)
        workflow.add_edge(START, "prepare")
        workflow.add_conditional_edges(
            "prepare",
            lambda state: state["action"],
            {"dispatch": "dispatch", "review": "review", "finish": END},
        )
        workflow.add_edge("dispatch", "record")
        workflow.add_edge("record", "prepare")
        workflow.add_conditional_edges(
            "review",
            lambda state: state["action"],
            {"prepare": "prepare", "finish": END},
        )
        with open_langgraph_checkpointer() as checkpointer:
            self._deep_agent_runner.set_checkpointer(checkpointer)
            try:
                graph_config: RunnableConfig = {
                    "recursion_limit": 200,
                    "configurable": {"thread_id": f"run:{run_id}"},
                }
                initial_state: _OrchestrationState = {"action": "prepare"}
                workflow.compile(checkpointer=checkpointer).invoke(
                    initial_state, config=graph_config, durability="sync" if checkpointer is not None else None
                )
            finally:
                self._deep_agent_runner.set_checkpointer(None)
        ledger.sync_statuses(supervisor.state.task_statuses)

        answer_markdown = "\n\n".join(summary_parts) if summary_parts else "No output generated."
        answer_limitations: list[str] = []
        if timed_out:
            answer_limitations.append(
                f"The run stopped at the {limits.active_seconds}s active time budget; some steps did not run."
            )
        if task_errors:
            answer_limitations.append("One or more steps failed, so the findings may be partial.")
        answer_limitations.extend(budget_limitations)
        answer_limitations.extend(plan_limitations)
        if (
            mode == RunMode.DEEP
            and final_review_verdict is not None
            and not final_review_verdict.approved
        ):
            answer_limitations.append("The review found unresolved gaps after the available remediation steps.")
        if use_deep_agents and summary_parts and not cancelled and not direct:
            composer_context = composer_findings([_clean_text(text, strip=False) for text in summary_parts])
            if composer_context.trimmed or composer_context.dropped:
                logger.info("Run %s composer findings trimmed %s, dropped %s", run_id, composer_context.trimmed, composer_context.dropped)
                answer_limitations.append(TRIMMED_FINDINGS_LIMITATION)
            composer_input = {
                "question": question_for_agents,
                "findings": [section.text for section in composer_context.sections],
                "claims": [
                    {
                        "statement": _clean_text(claim.statement, 500, strip=False),
                        "label": claim.label,
                        "evidence_ids": list(claim.evidence_ids),
                    }
                    for claim in collected_claims
                ],
                "limitations": answer_limitations,
            }
            streamed = ""
            pending = ""
            last_flush = time.monotonic()
            stream_started = False

            def _flush_stream() -> None:
                nonlocal streamed, pending, last_flush
                if pending:
                    _emit(EventType.ANSWER_DELTA, {"text": pending, "draft_id": "final_answer"})
                    streamed += pending
                    pending = ""
                last_flush = time.monotonic()

            def _on_composer_delta(piece: str) -> None:
                nonlocal pending, stream_started
                if not stream_started:
                    _emit(EventType.ANSWER_RESET, {})
                    stream_started = True
                pending += piece
                if len(pending) >= STREAM_FLUSH_CHARS or time.monotonic() - last_flush >= STREAM_FLUSH_SECONDS:
                    _flush_stream()

            def _on_composer_reset() -> None:
                nonlocal streamed, pending, stream_started
                streamed = pending = ""
                stream_started = False

            try:
                with using_prompt(prompts.COMPOSER):
                    composed, composer_provider, composer_model = self._deep_agent_runner.run_text(
                        role=AgentRole.ANSWER_COMPOSER,
                        system_prompt=prompts.COMPOSER.text,
                        user_prompt=json.dumps(composer_input, ensure_ascii=False, default=str),
                        run_id=run_id,
                        policy=policy,
                        on_delta=_on_composer_delta,
                        on_reset=_on_composer_reset,
                        deadline=deadline,
                    )
                _flush_stream()
                known_ids = {evidence_id for claim in collected_claims for evidence_id in claim.evidence_ids}
                composed, _ = _strip_unknown_citations(composed, known_ids)
                if composed.strip():
                    answer_markdown = composed.strip()
                    _emit(
                        EventType.ROUTE_SELECTED,
                        {"task_id": "answer_composer", "provider": composer_provider, "model": composer_model},
                    )
                if not stream_started or streamed.strip() != answer_markdown:
                    _emit(EventType.ANSWER_RESET, {})
                    _emit(EventType.ANSWER_DELTA, {"text": answer_markdown, "draft_id": "final_answer"})
            except Exception:
                answer_markdown = "\n\n".join(summary_parts) if summary_parts else "No output generated."
                if _cancelled():
                    cancelled = True
                else:
                    answer_markdown = (
                        f"{answer_markdown}\n\n> The findings are listed by analysis step because a concise summary could not be generated."
                    )
                    answer_limitations.append(
                        "The final synthesis step was unavailable, so this answer shows the completed step findings."
                    )
                    if not timed_out and deadline is not None and time.monotonic() >= deadline:
                        timed_out = True
                        answer_limitations.append(
                            f"The run stopped at the {limits.active_seconds}s active time budget; some steps did not run."
                        )
                    _emit(EventType.ANSWER_RESET, {})
                    _emit(EventType.ANSWER_DELTA, {"text": answer_markdown, "draft_id": "final_answer"})

        outcome, criteria = assess_answer(
            answer_markdown=answer_markdown,
            evidence=run_evidence,
            criteria=planned_criteria,
            review_approved=final_review_verdict.approved if mode == RunMode.DEEP and final_review_verdict is not None else None,
            review_contradictions=bool(final_review_verdict and final_review_verdict.contradictions),
            steps_failed=bool(task_errors),
            stopped_early=timed_out or bool(budget_limitations),
        )
        if outcome == "needs_review":
            answer_limitations.append("Some figures could not be matched to the evidence they cite; check them before relying on this answer.")
        answer = Answer(
            answer_id=f"ans_{uuid.uuid4().hex[:10]}",
            run_id=run_id,
            summary_markdown=answer_markdown,
            claims=tuple(collected_claims),
            charts=build_answer_charts(
                run_evidence,
                {e for c in collected_claims for e in c.evidence_ids} | set(_EVIDENCE_ID_RE.findall(answer_markdown)),
            ),
            limitations=tuple(answer_limitations),
            snapshot_id=snapshot_id,
            semantic_version_id=semantic_version_id,
            created_at=datetime.now(timezone.utc),
            outcome=outcome,
            criteria=criteria,
            route=route.route.value,
        )
        finished: dict[str, Any] = {"answer_id": answer.answer_id, "tool_calls": toolbox.budget.used, "route": route.route.value}
        if cancelled:
            finished["status"] = RunStatus.CANCELLED.value
        elif not summary_parts and timed_out:
            finished["status"] = RunStatus.FAILED.value
            finished["error"] = f"Time budget exceeded ({limits.active_seconds}s)"
        elif not summary_parts and task_errors:
            finished["status"] = RunStatus.FAILED.value
            finished["error"] = task_errors[0][:500]
        elif not summary_parts and budget_limitations:
            finished["status"] = RunStatus.FAILED.value
            finished["error"] = budget_limitations[0][:500]
        else:
            finished["status"] = RunStatus.COMPLETED.value
            finished["outcome"] = outcome
            finished["criteria"] = [{"criterion": c.criterion, "status": c.status} for c in criteria]
            if task_errors:
                finished["failed_tasks"] = len(task_errors)
            if timed_out:
                finished["time_budget_exceeded"] = True
        _emit(EventType.RUN_FINISHED, finished)
        return answer
