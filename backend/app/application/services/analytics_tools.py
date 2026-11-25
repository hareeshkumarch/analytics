import hashlib
import json
import threading
from dataclasses import dataclass, field
from decimal import Decimal, DivisionByZero, InvalidOperation
from typing import Any

from opentelemetry import trace

from app.application.ports.providers import ToolCallResult, ToolDefinition
from app.application.ports.query import SnapshotQueryPort
from app.domain.errors import DomainError, QueryValidationError
from app.domain.limits import DEFAULT_GLOBAL_LIMITS
from app.domain.query_ir import AggregateFunction, FilterOperator, QueryIRParseError, parse_query_ir

_tracer = trace.get_tracer("ledgerline.query")

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
        "any approved metric definitions. Call this before writing queries. Pass table_id to see one table "
        "when the full schema is too large."
    ),
    parameters_schema={
        "type": "object",
        "properties": {"table_id": {"type": "string", "description": "Only describe this table."}},
    },
    strict=False,
)

RUN_QUERY_TOOL = ToolDefinition(
    name="run_query_ir",
    description=(
        "Run a read-only, typed query against the bound dataset snapshot and get the result rows plus an "
        "evidence_id. Use only table and column names returned by inspect_schema. Raw SQL is not accepted. "
        "Row values from detail queries may be withheld by the data policy; use aggregates for figures."
    ),
    parameters_schema=RUN_QUERY_SCHEMA,
    strict=False,
)


CALCULATE_TOOL = ToolDefinition(
    name="calculate",
    description=(
        "Compute a figure from values already returned by run_query_ir, so derived numbers are traceable. "
        "operation is sum, difference (a - b), ratio (a / b) or percent_change ((a - b) / b * 100, a = current, "
        "b = baseline). Each value names an evidence_id, a 0-based row in that result and a column. Returns a new "
        "evidence_id to cite. Never do arithmetic in text instead."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": ["sum", "difference", "ratio", "percent_change"]},
            "purpose": {"type": "string"},
            "values": {
                "type": "array",
                "minItems": 1,
                "maxItems": 50,
                "items": {
                    "type": "object",
                    "properties": {
                        "evidence_id": {"type": "string"},
                        "row": {"type": "integer", "minimum": 0},
                        "column": {"type": "string"},
                    },
                    "required": ["evidence_id", "row", "column"],
                },
            },
        },
        "required": ["operation", "values", "purpose"],
    },
    strict=False,
)
_PAIRWISE_OPERATIONS = ("difference", "ratio", "percent_change")


def _number(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _plain(number: Decimal) -> str:
    try:
        number = number.quantize(Decimal("1e-10"))
    except InvalidOperation:
        pass
    text = format(number.normalize(), "f")
    return "0" if text in ("-0", "") else text


def _apply(operation: str, numbers: list[Decimal]) -> Decimal:
    if operation == "sum":
        return sum(numbers, Decimal(0))
    current, baseline = numbers
    if operation == "difference":
        return current - baseline
    if baseline == 0:
        raise DivisionByZero
    return current / baseline if operation == "ratio" else (current - baseline) / baseline * 100


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


def _fits(raw: str) -> bool:
    return len(raw.encode("utf-8")) <= DEFAULT_GLOBAL_LIMITS.max_model_tool_result_bytes


def _cap(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, default=str)
    if _fits(raw):
        return raw
    rows = list(payload.get("rows") or [])
    while rows and not _fits(raw):
        rows = rows[: len(rows) // 2]
        raw = json.dumps({**payload, "rows": rows, "rows_shown": len(rows), "trimmed_for_size": True}, default=str)
    if _fits(raw):
        return raw
    return json.dumps(
        {
            "evidence_id": payload.get("evidence_id"),
            "row_count": payload.get("row_count"),
            "error": "Result too large to show. Narrow the query with filters, fewer columns or a lower limit.",
        }
    )


def _cap_schema(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, default=str)
    if _fits(raw):
        return raw
    compact = [
        {
            "table_id": t["table_id"],
            "row_count": t["row_count"],
            "columns": [f"{c['name']}:{c['type']}" + (f":{c['currency']}" if c.get("currency") else "") for c in t["columns"]],
        }
        for t in payload["tables"]
    ]
    raw = json.dumps({**payload, "tables": compact, "schema_compacted": True}, default=str)
    if _fits(raw):
        return raw
    listed = [{"table_id": t["table_id"], "row_count": t["row_count"], "column_count": len(t["columns"])} for t in payload["tables"]]
    return json.dumps(
        {
            "snapshot_id": payload.get("snapshot_id"),
            "definitions_approved": payload.get("definitions_approved"),
            "tables": listed,
            "note": "The full schema is too large. Call inspect_schema with a table_id to see that table's columns.",
        },
        default=str,
    )


@dataclass
class AnalyticsToolbox:
    engine: SnapshotQueryPort
    snapshot: dict[str, Any] | None
    semantics: dict[str, Any] | None
    budget: ToolBudget
    max_rows_to_model: int = MAX_ROWS_TO_MODEL
    raw_record_egress: bool = False
    definitions: tuple[ToolDefinition, ...] = field(default=(INSPECT_SCHEMA_TOOL, RUN_QUERY_TOOL, CALCULATE_TOOL))
    _calculable: dict[str, dict[str, Any]] = field(default_factory=dict, init=False, repr=False)
    _failed_calls: dict[str, str] = field(default_factory=dict, init=False, repr=False)

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
        names = [d.name for d in self.definitions]
        if name not in names:
            return self._error(name, f"Unknown tool {name!r}. Available: {', '.join(names)}.")
        if not self.available:
            return self._error(name, "No dataset snapshot is bound to this run.")
        arguments = call.arguments if isinstance(call.arguments, dict) else {}
        fingerprint = name + json.dumps(arguments, sort_keys=True, default=str)
        earlier = self._failed_calls.get(fingerprint)
        if earlier is not None:
            return self._error(
                name,
                f"This exact call already failed ({earlier[:200]}). It was not run again; change the arguments or answer with the evidence you have.",
            )
        if not self.budget.take(analytical=name == RUN_QUERY_TOOL.name):
            return self._error(name, "Tool budget for this run is used up. Answer with the results you already have.")
        if name == INSPECT_SCHEMA_TOOL.name:
            outcome = self._inspect_schema(arguments.get("table_id"))
        elif name == CALCULATE_TOOL.name:
            outcome = self._calculate(arguments)
        else:
            outcome = self._run_query(arguments)
        if outcome.is_error:
            self._failed_calls[fingerprint] = str(outcome.summary.get("error") or "")
        return outcome

    def _inspect_schema(self, table_id: Any = None) -> ToolOutcome:
        assert self.snapshot is not None
        details = self.snapshot.get("table_details", [])
        if isinstance(table_id, str) and table_id:
            details = [t for t in details if t["name"] == table_id]
            if not details:
                return self._error(INSPECT_SCHEMA_TOOL.name, f"Unknown table_id {table_id!r}. Call inspect_schema without it to list tables.")
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
            for t in details
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
            content=_cap_schema(payload),
            is_error=False,
            summary={"tool": INSPECT_SCHEMA_TOOL.name, "ok": True, "tables": len(tables)},
        )

    def _calculate(self, arguments: dict[str, Any]) -> ToolOutcome:
        assert self.snapshot is not None
        operation = arguments.get("operation")
        refs = arguments.get("values")
        if operation not in ("sum", *_PAIRWISE_OPERATIONS) or not isinstance(refs, list) or not 1 <= len(refs) <= 50:
            return self._error(CALCULATE_TOOL.name, "Give an operation (sum, difference, ratio, percent_change) and 1-50 values.")
        if operation in _PAIRWISE_OPERATIONS and len(refs) != 2:
            return self._error(CALCULATE_TOOL.name, f"{operation} takes exactly two values: current first, then baseline.")
        inputs: list[dict[str, Any]] = []
        for ref in refs:
            source = self._calculable.get(str(ref.get("evidence_id"))) if isinstance(ref, dict) else None
            if source is None:
                return self._error(
                    CALCULATE_TOOL.name,
                    "Each value must reference an evidence_id returned by run_query_ir in this run whose rows you saw.",
                )
            row, column = ref.get("row"), ref.get("column")
            if column not in source["columns"] or not isinstance(row, int) or not 0 <= row < len(source["rows"]):
                return self._error(CALCULATE_TOOL.name, f"No row {row!r} / column {column!r} in {ref['evidence_id']}.")
            number = _number(source["rows"][row][source["columns"].index(column)])
            if number is None:
                return self._error(CALCULATE_TOOL.name, f"{ref['evidence_id']} row {row} column {column!r} is not a number.")
            inputs.append({"evidence_id": ref["evidence_id"], "row": row, "column": column, "value": str(number)})
        try:
            result = _apply(operation, [Decimal(item["value"]) for item in inputs])
        except (DivisionByZero, InvalidOperation):
            return self._error(CALCULATE_TOOL.name, "The baseline value is zero, so this can't be computed.")
        formula = f"{operation}(" + ", ".join(f"{i['evidence_id']}[{i['row']}].{i['column']}" for i in inputs) + ")"
        inputs_hash = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode("utf-8")).hexdigest()
        result_text = _plain(result)
        evidence_id = "ev_" + hashlib.sha256(f"{formula}:{result_text}".encode("utf-8")).hexdigest()[:12]
        evidence = {
            "evidence_id": evidence_id,
            "operation": f"calculate_{operation}",
            "purpose": str(arguments.get("purpose") or "")[:500],
            "query_ir_hash": inputs_hash,
            "result_hash": hashlib.sha256(result_text.encode("utf-8")).hexdigest(),
            "sql": formula,
            "row_count": 1,
            "truncated": False,
            "snapshot_id": self.snapshot.get("snapshot_id"),
            "execution_ms": 0,
            "columns": ["result"],
            "rows": [[result_text]],
            "inputs": inputs,
        }
        self._calculable[evidence_id] = {"columns": ["result"], "rows": [[result_text]]}
        return ToolOutcome(
            content=json.dumps({"evidence_id": evidence_id, "result": result_text, "formula": formula}),
            is_error=False,
            summary={"tool": CALCULATE_TOOL.name, "ok": True, "operation": operation, "evidence_id": evidence_id},
            evidence=evidence,
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
            with _tracer.start_as_current_span(
                "query.run", attributes={"query.operation": ir.operation, "snapshot.id": str(self.snapshot.get("snapshot_id") or "")}
            ) as span:
                result, sql = self.engine.run(self.snapshot, ir, semantic_version_id, MAX_QUERY_ROWS)
                span.set_attribute("query.rows", result.row_count)
                span.set_attribute("query.truncated", result.truncated)
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
        withheld = ir.operation == "detail" and not self.raw_record_egress
        if not withheld:
            self._calculable[evidence_id] = {"columns": evidence["columns"], "rows": evidence["rows"]}
        payload = {
            "evidence_id": evidence_id,
            "columns": list(result.columns),
            "rows": [] if withheld else [list(r) for r in shown],
            "row_count": result.row_count,
            "rows_shown": 0 if withheld else len(shown),
            "truncated": result.truncated or len(shown) < result.row_count,
        }
        notes: list[str] = []
        if withheld:
            payload["rows_withheld"] = True
            notes.append(
                "The data policy keeps raw row values from detail queries out of the model. The user can see these "
                f"rows in evidence {evidence_id}; cite it, and never guess values. Use aggregate queries for figures."
            )
        if result.truncated:
            notes.append(
                f"More than {result.row_count} rows matched, so only the first {result.row_count} were returned. "
                "Totals or counts taken from these rows are incomplete: aggregate in the query or narrow the filters."
            )
        if notes:
            payload["note"] = " ".join(notes)
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
