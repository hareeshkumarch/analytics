import hashlib
import json
import threading
from dataclasses import dataclass, field
from typing import Any

from app.application.ports.providers import ToolCallResult, ToolDefinition
from app.application.ports.query import SnapshotQueryPort
from app.domain.errors import DomainError, QueryValidationError
from app.domain.limits import DEFAULT_GLOBAL_LIMITS
from app.domain.query_ir import AggregateFunction, FilterOperator, QueryIRParseError, parse_query_ir

MAX_ROWS_TO_MODEL = 50
MAX_QUERY_ROWS = 1000

_STRING_LIST = {"type": "array", "items": {"type": "string"}}
_METRIC = {
    "type": "object",
    "properties": {
        "alias": {"type": "string", "description": "Output column name for the result, e.g. total_amount."},
        "func": {"type": "string", "enum": [f.value for f in AggregateFunction]},
        "field_id": {"type": "string", "description": "Column to aggregate."},
    },
    "required": ["alias", "func", "field_id"],
}

RUN_QUERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "operation": {
            "type": "string",
            "enum": [
                "aggregate",
                "detail",
                "compare_periods",
                "contribution_breakdown",
                "distribution",
                "reconciliation_lookup",
            ],
            "description": (
                "aggregate: metrics grouped by columns. detail: raw rows. compare_periods: one metric for two "
                "date ranges. contribution_breakdown: one metric per value of a dimension, largest first. "
                "distribution: for a numeric column, a histogram with `buckets` bins plus min/max/avg/count; for other "
                "columns, min/max/count/distinct count. reconciliation_lookup: join two tables on a key "
                "and compare amount totals; each filter applies to whichever table has that column."
            ),
        },
        "purpose": {"type": "string", "description": "One sentence: what this query is for."},
        "table_id": {"type": "string", "description": "Table name from inspect_schema."},
        "metrics": {"type": "array", "items": _METRIC, "description": "aggregate only."},
        "metric": {**_METRIC, "description": "compare_periods and contribution_breakdown."},
        "group_by": {**_STRING_LIST, "description": "aggregate and compare_periods."},
        "select_fields": {**_STRING_LIST, "description": "detail only."},
        "filters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field_id": {"type": "string"},
                    "op": {"type": "string", "enum": [o.value for o in FilterOperator]},
                    "value": {
                        "anyOf": [
                            {"type": "string"},
                            {"type": "number"},
                            {"type": "boolean"},
                            {"type": "array", "items": {"type": "string"}},
                        ],
                        "description": "Omit for is_null / is_not_null; a list for 'in'.",
                    },
                },
                "required": ["field_id", "op"],
            },
        },
        "order_by": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field_id": {"type": "string", "description": "A metric alias or group_by column."},
                    "direction": {"type": "string", "enum": ["asc", "desc"]},
                },
                "required": ["field_id"],
            },
        },
        "date_field": {"type": "string", "description": "compare_periods only."},
        "current_start": {"type": "string", "description": "ISO date, compare_periods only."},
        "current_end": {"type": "string", "description": "ISO date, compare_periods only."},
        "previous_start": {"type": "string", "description": "ISO date, compare_periods only."},
        "previous_end": {"type": "string", "description": "ISO date, compare_periods only."},
        "dimension_field": {"type": "string", "description": "contribution_breakdown only."},
        "field_id": {"type": "string", "description": "distribution only."},
        "buckets": {"type": "integer", "description": "distribution only, 1-100."},
        "left_table_id": {"type": "string", "description": "reconciliation_lookup only."},
        "right_table_id": {"type": "string", "description": "reconciliation_lookup only."},
        "left_key": {"type": "string", "description": "reconciliation_lookup only."},
        "right_key": {"type": "string", "description": "reconciliation_lookup only."},
        "left_amount_field": {"type": "string", "description": "reconciliation_lookup only."},
        "right_amount_field": {"type": "string", "description": "reconciliation_lookup only."},
        "limit": {"type": "integer", "description": "Maximum rows, 1-1000."},
    },
    "required": ["operation", "purpose"],
}

INSPECT_SCHEMA_TOOL = ToolDefinition(
    name="inspect_schema",
    description=(
        "List the tables in the dataset snapshot bound to this run, with column names, types, row counts and "
        "any approved metric definitions. Call this before writing queries."
    ),
    parameters_schema={"type": "object", "properties": {}},
    strict=False,
)

RUN_QUERY_TOOL = ToolDefinition(
    name="run_query_ir",
    description=(
        "Run a read-only, typed query against the bound dataset snapshot and get the result rows plus an "
        "evidence_id. Use only table and column names returned by inspect_schema. Raw SQL is not accepted."
    ),
    parameters_schema=RUN_QUERY_SCHEMA,
    strict=False,
)


class ToolBudget:
    def __init__(
        self,
        max_invocations: int,
        max_analytical: int,
        *,
        used_invocations: int = 0,
        used_analytical: int = 0,
    ) -> None:
        self._lock = threading.Lock()
        self._invocations_left = max(0, max_invocations - used_invocations)
        self._analytical_left = max(0, max_analytical - used_analytical)
        self.used = max(0, used_invocations)
        self._used_analytical = max(0, used_analytical)

    @property
    def exhausted(self) -> bool:
        with self._lock:
            return self._invocations_left <= 0

    @property
    def analytical_exhausted(self) -> bool:
        with self._lock:
            return self._analytical_left <= 0

    @property
    def used_analytical(self) -> int:
        with self._lock:
            return self._used_analytical

    def refund(self, analytical: bool) -> None:
        with self._lock:
            self._invocations_left += 1
            if analytical:
                self._analytical_left += 1
                self._used_analytical = max(0, self._used_analytical - 1)
            self.used = max(0, self.used - 1)

    def take(self, analytical: bool) -> bool:
        with self._lock:
            if self._invocations_left <= 0 or (analytical and self._analytical_left <= 0):
                return False
            self._invocations_left -= 1
            if analytical:
                self._analytical_left -= 1
                self._used_analytical += 1
            self.used += 1
            return True


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    content: str
    is_error: bool
    summary: dict[str, Any]
    evidence: dict[str, Any] | None = None


def _cap(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, default=str)
    limit = DEFAULT_GLOBAL_LIMITS.max_model_tool_result_bytes
    if len(raw.encode("utf-8")) <= limit:
        return raw
    rows = list(payload.get("rows") or [])
    while rows and len(raw.encode("utf-8")) > limit:
        rows = rows[: len(rows) // 2]
        raw = json.dumps({**payload, "rows": rows, "rows_shown": len(rows), "trimmed_for_size": True}, default=str)
    if len(raw.encode("utf-8")) <= limit:
        return raw
    return json.dumps(
        {
            "evidence_id": payload.get("evidence_id"),
            "row_count": payload.get("row_count"),
            "error": "Result too large to show. Narrow the query with filters, fewer columns or a lower limit.",
        }
    )


@dataclass
class AnalyticsToolbox:
    engine: SnapshotQueryPort
    snapshot: dict[str, Any] | None
    semantics: dict[str, Any] | None
    budget: ToolBudget
    max_rows_to_model: int = MAX_ROWS_TO_MODEL
    definitions: tuple[ToolDefinition, ...] = field(default=(INSPECT_SCHEMA_TOOL, RUN_QUERY_TOOL))

    @property
    def available(self) -> bool:
        return bool(self.snapshot and self.snapshot.get("table_details"))

    def failure(self, tool: str, message: str) -> ToolOutcome:
        return self._error(tool, message)

    def _error(self, tool: str, message: str) -> ToolOutcome:
        return ToolOutcome(
            content=json.dumps({"error": message}),
            is_error=True,
            summary={"tool": tool, "ok": False, "error": message[:300]},
        )

    def execute(self, call: ToolCallResult) -> ToolOutcome:
        name = call.tool_name
        if name not in {d.name for d in self.definitions}:
            return self._error(name, f"Unknown tool {name!r}. Available: inspect_schema, run_query_ir.")
        if not self.available:
            return self._error(name, "No dataset snapshot is bound to this run.")
        if not self.budget.take(analytical=name == RUN_QUERY_TOOL.name):
            return self._error(name, "Tool budget for this run is used up. Answer with the results you already have.")
        if name == INSPECT_SCHEMA_TOOL.name:
            return self._inspect_schema()
        return self._run_query(call.arguments if isinstance(call.arguments, dict) else {})

    def _inspect_schema(self) -> ToolOutcome:
        assert self.snapshot is not None
        tables = [
            {
                "table_id": t["name"],
                "row_count": t.get("row_count"),
                "columns": [
                    {
                        "name": c["name"],
                        "type": c.get("type"),
                        "nullable": c.get("nullable", True),
                        **({"currency": c["currency"]} if c.get("currency") else {}),
                        **({"identifier_with_leading_zeros": True} if c.get("leading_zero_id") else {}),
                    }
                    for c in t.get("columns", [])
                ],
            }
            for t in self.snapshot.get("table_details", [])
        ]
        semantics = self.semantics or {}
        payload = {
            "snapshot_id": self.snapshot.get("snapshot_id"),
            "definitions_approved": bool(semantics.get("approved")),
            "metric_definitions": [
                {k: m.get(k) for k in ("display_name", "expression", "grain", "unit", "currency")}
                for m in semantics.get("metrics", [])
            ],
            "tables": tables,
        }
        return ToolOutcome(
            content=_cap(payload),
            is_error=False,
            summary={"tool": INSPECT_SCHEMA_TOOL.name, "ok": True, "tables": len(tables)},
        )

    def _run_query(self, arguments: dict[str, Any]) -> ToolOutcome:
        assert self.snapshot is not None
        if "_invalid_arguments" in arguments:
            self.budget.refund(analytical=True)
            return self._error(RUN_QUERY_TOOL.name, "Tool arguments were not valid JSON. Send a JSON object.")
        try:
            ir = parse_query_ir(arguments)
        except QueryIRParseError as exc:
            self.budget.refund(analytical=True)
            return self._error(RUN_QUERY_TOOL.name, f"Invalid query (not counted against the budget): {exc}")
        semantic_version_id = str((self.semantics or {}).get("semantic_version_id") or "")
        try:
            result, sql = self.engine.run(self.snapshot, ir, semantic_version_id, MAX_QUERY_ROWS)
        except QueryValidationError as exc:
            self.budget.refund(analytical=True)
            return self._error(RUN_QUERY_TOOL.name, f"Invalid query (not counted against the budget): {exc}")
        except DomainError as exc:
            return self._error(RUN_QUERY_TOOL.name, f"{exc}")
        except Exception as exc:  # noqa: BLE001
            return self._error(RUN_QUERY_TOOL.name, f"Query failed: {exc}"[:500])
        evidence_id = "ev_" + hashlib.sha256(f"{result.query_ir_hash}:{result.result_hash}".encode("utf-8")).hexdigest()[:12]
        evidence = {
            "evidence_id": evidence_id,
            "operation": ir.operation,
            "purpose": getattr(ir, "purpose", ""),
            "query_ir_hash": result.query_ir_hash,
            "result_hash": result.result_hash,
            "sql": sql,
            "row_count": result.row_count,
            "truncated": result.truncated,
            "snapshot_id": result.snapshot_id,
            "execution_ms": result.execution_ms,
        }
        shown = result.rows[: self.max_rows_to_model]
        evidence["columns"] = list(result.columns)
        evidence["rows"] = json.loads(json.dumps([list(r) for r in shown], default=str))
        payload = {
            "evidence_id": evidence_id,
            "columns": list(result.columns),
            "rows": [list(r) for r in shown],
            "row_count": result.row_count,
            "rows_shown": len(shown),
            "truncated": result.truncated or len(shown) < result.row_count,
        }
        if result.truncated:
            payload["note"] = (
                f"More than {result.row_count} rows matched, so only the first {result.row_count} were returned. "
                "Totals or counts taken from these rows are incomplete: aggregate in the query or narrow the filters."
            )
        return ToolOutcome(
            content=_cap(payload),
            is_error=False,
            summary={
                "tool": RUN_QUERY_TOOL.name,
                "ok": True,
                "operation": ir.operation,
                "row_count": result.row_count,
                "evidence_id": evidence_id,
                "execution_ms": result.execution_ms,
            },
            evidence=evidence,
        )
