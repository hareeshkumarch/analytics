import hashlib
import json
import re
from dataclasses import asdict
from datetime import date, datetime, time, timedelta
from typing import Any

from app.application.ports.query import CompiledQuery
from app.domain.errors import QueryValidationError, SecurityPolicyError
from app.domain.models import TableSchema
from app.domain.query_ir import (
    AggregateFunction,
    AggregateQueryIR,
    ComparePeriodsQueryIR,
    ContributionBreakdownQueryIR,
    DetailQueryIR,
    DistributionQueryIR,
    FilterOperator,
    FilterPredicate,
    QueryIR,
    ReconciliationLookupQueryIR,
)

COMPILER_VERSION = "1.0.0"

_FORBIDDEN_FUNCTIONS = {
    "read_csv",
    "read_csv_auto",
    "read_parquet",
    "read_json",
    "read_json_auto",
    "read_text",
    "read_blob",
    "glob",
    "sqlite_scan",
    "sqlite_attach",
    "postgres_scan",
    "mysql_scan",
    "parquet_scan",
    "duckdb_tables",
    "duckdb_columns",
    "duckdb_databases",
    "duckdb_settings",
    "duckdb_extensions",
    "current_setting",
    "getenv",
}

_FORBIDDEN_TABLES = {
    "information_schema",
    "pg_catalog",
    "sqlite_master",
    "sqlite_schema",
    "duckdb_tables",
    "duckdb_columns",
    "duckdb_settings",
}


def _quote_ident(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


def _hash_ir(ir: QueryIR) -> str:
    payload = json.dumps(asdict(ir), sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


_NUMERIC_TYPE = re.compile(r"^(u?int(8|16|32|64)?|halffloat|float(16|32|64)?|double|decimal(32|64|128|256)?|numeric|real)\b")


_TEMPORAL_TYPE = re.compile(r"^(date(32|64)?|timestamp)\b")


def _arrow_type(schema: TableSchema, field_id: str) -> str:
    for col in schema.columns:
        if col.name == field_id:
            arrow_type = col.arrow_type.lower().strip()
            dictionary = re.match(r"^dictionary<values=([^,>]+)", arrow_type)
            return dictionary.group(1).strip() if dictionary else arrow_type
    return ""


def _is_numeric(schema: TableSchema, field_id: str) -> bool:
    return bool(_NUMERIC_TYPE.match(_arrow_type(schema, field_id)))


def _is_temporal(schema: TableSchema, field_id: str) -> bool:
    return bool(_TEMPORAL_TYPE.match(_arrow_type(schema, field_id)))


def validate_sql_ast(sql: str) -> None:
    if ";" in sql.strip().rstrip(";"):
        raise SecurityPolicyError("Multi-statement SQL is forbidden")

    try:
        import sqlglot
        from sqlglot import exp
    except ImportError as exc:
        raise SecurityPolicyError("SQL AST validator unavailable: sqlglot is required") from exc

    parsed = sqlglot.parse(sql, read="duckdb")
    if len(parsed) != 1 or parsed[0] is None:
        raise SecurityPolicyError("SQL must parse to exactly one statement")
    root = parsed[0]
    if not isinstance(root, exp.Select) and not isinstance(root, exp.Union):
        raise SecurityPolicyError(f"Top-level statement must be SELECT, got {type(root).__name__}")

    forbidden_node_types = (
        exp.Create,
        exp.Drop,
        exp.Alter,
        exp.Insert,
        exp.Update,
        exp.Delete,
        exp.Command,
        exp.Pragma,
        exp.Set,
        exp.Attach,
        exp.Detach,
    )
    for node in root.walk():
        if isinstance(node, forbidden_node_types):
            raise SecurityPolicyError(f"Forbidden SQL AST node: {type(node).__name__}")
        if isinstance(node, exp.With) and node.args.get("recursive"):
            raise SecurityPolicyError("WITH RECURSIVE is forbidden")
        if isinstance(node, (exp.Func, exp.Anonymous)):
            fname = (node.name or "").lower()
            if fname in _FORBIDDEN_FUNCTIONS or fname.startswith("duckdb_") or fname.startswith("read_"):
                raise SecurityPolicyError(f"Forbidden SQL function: {fname}")
        if isinstance(node, exp.Table):
            tname = (node.name or "").lower()
            tdb = (node.db or "").lower()
            if tname in _FORBIDDEN_TABLES or tdb in _FORBIDDEN_TABLES or tname.startswith("duckdb_"):
                raise SecurityPolicyError(f"Forbidden system catalog table: {tname}")


def _parse_bound(value: str, label: str) -> date | datetime:
    try:
        return date.fromisoformat(value)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise QueryValidationError(
            f"{label} must be an ISO date (YYYY-MM-DD) or date-time, got {value!r}",
            failed_fields=(label,),
        ) from exc
    if parsed.tzinfo is not None:
        raise QueryValidationError(
            f"{label} must not include a time zone offset, got {value!r}",
            failed_fields=(label,),
        )
    return parsed


def _as_timestamp(value: date | datetime) -> datetime:
    return value if isinstance(value, datetime) else datetime.combine(value, time.min)


def _period_bounds(start: str, end: str, period: str) -> tuple[datetime, str, datetime]:
    start_value = _parse_bound(start, f"{period}_start")
    end_value = _parse_bound(end, f"{period}_end")
    start_dt = start_value if isinstance(start_value, datetime) else datetime.combine(start_value, time.min)
    end_bound: date | datetime
    if isinstance(end_value, datetime):
        end_op, end_bound, valid = "<=", end_value, start_dt <= end_value
    else:
        end_op, end_bound = "<", end_value + timedelta(days=1)
        valid = start_dt < datetime.combine(end_bound, time.min)
    if not valid:
        raise QueryValidationError(
            f"{period}_start must not be after {period}_end",
            failed_fields=(f"{period}_start", f"{period}_end"),
        )
    return _as_timestamp(start_value), end_op, _as_timestamp(end_bound)


class SqlGlotQueryCompiler:
    def _validate_field(
        self, table_id: str, field_id: str, schemas: dict[str, TableSchema], *, table_arg: str = "table_id"
    ) -> str:
        if table_id not in schemas:
            raise QueryValidationError(f"Unknown table_id: {table_id!r}", failed_fields=(table_arg,))
        valid_cols = {c.name for c in schemas[table_id].columns}
        if field_id not in valid_cols:
            raise QueryValidationError(
                f"Unknown field_id {field_id!r} on table {table_id!r}",
                failed_fields=(field_id,),
            )
        return _quote_ident(field_id)

    def _compile_filters(
        self,
        table_id: str,
        filters: tuple[FilterPredicate, ...],
        schemas: dict[str, TableSchema],
        params: list[Any],
    ) -> str:
        if not filters:
            return ""
        clauses: list[str] = []
        for f in filters:
            col_sql = self._validate_field(table_id, f.field_id, schemas)
            if f.op == FilterOperator.IS_NULL:
                clauses.append(f"{col_sql} IS NULL")
            elif f.op == FilterOperator.IS_NOT_NULL:
                clauses.append(f"{col_sql} IS NOT NULL")
            elif f.op == FilterOperator.IN:
                if not isinstance(f.value, list) or not f.value:
                    raise QueryValidationError(
                        f"Filter on {f.field_id!r} with 'in' needs a non-empty list value",
                        failed_fields=("filters", f.field_id),
                    )
                placeholders = ", ".join("?" for _ in f.value)
                params.extend(f.value)
                clauses.append(f"{col_sql} IN ({placeholders})")
            else:
                op_map = {
                    FilterOperator.EQ: "=",
                    FilterOperator.NEQ: "!=",
                    FilterOperator.GT: ">",
                    FilterOperator.GTE: ">=",
                    FilterOperator.LT: "<",
                    FilterOperator.LTE: "<=",
                }
                if f.value is None or isinstance(f.value, (list, dict)):
                    raise QueryValidationError(
                        f"Filter on {f.field_id!r} with {f.op.value!r} needs a single value; "
                        "use is_null or is_not_null to test for missing values",
                        failed_fields=("filters", f.field_id),
                    )
                sql_op = op_map[f.op]
                if _is_temporal(schemas[table_id], f.field_id) and isinstance(f.value, str):
                    params.append(_as_timestamp(_parse_bound(f.value, f.field_id)))
                else:
                    params.append(f.value)
                clauses.append(f"{col_sql} {sql_op} ?")
        return " WHERE " + " AND ".join(clauses)

    def _compile_agg_expr(
        self, func: AggregateFunction, col_sql: str, alias: str
    ) -> str:
        safe_alias = _quote_ident(alias)
        if func == AggregateFunction.COUNT_DISTINCT:
            return f"COUNT(DISTINCT {col_sql}) AS {safe_alias}"
        return f"{func.value.upper()}({col_sql}) AS {safe_alias}"

    def compile(
        self,
        ir: QueryIR,
        schemas: dict[str, TableSchema],
        max_rows: int = 1000,
    ) -> CompiledQuery:
        params: list[Any] = []
        row_limit = min(max(1, getattr(ir, "limit", max_rows)), max_rows)
        limit = row_limit + 1
        ir_hash = _hash_ir(ir)

        if isinstance(ir, AggregateQueryIR):
            if not ir.metrics:
                raise QueryValidationError("AggregateQueryIR requires at least one metric", failed_fields=("metrics",))
            tbl_sql = _quote_ident(ir.table_id)
            select_parts = [self._validate_field(ir.table_id, g, schemas) for g in ir.group_by]
            for m in ir.metrics:
                col_sql = self._validate_field(ir.table_id, m.field_id, schemas)
                select_parts.append(self._compile_agg_expr(m.func, col_sql, m.alias))
            where_sql = self._compile_filters(ir.table_id, ir.filters, schemas, params)
            group_sql = (
                " GROUP BY " + ", ".join(self._validate_field(ir.table_id, g, schemas) for g in ir.group_by)
                if ir.group_by
                else ""
            )
            order_sql = ""
            if ir.order_by:
                valid_aliases = {m.alias for m in ir.metrics} | set(ir.group_by)
                order_items = []
                for o in ir.order_by:
                    if o.field_id not in valid_aliases:
                        raise QueryValidationError(
                            f"Unknown order_by field {o.field_id!r}",
                            failed_fields=("order_by", o.field_id),
                        )
                    order_items.append(f"{_quote_ident(o.field_id)} {o.direction.upper()}")
                order_sql = " ORDER BY " + ", ".join(order_items)
            sql = f"SELECT {', '.join(select_parts)} FROM {tbl_sql}{where_sql}{group_sql}{order_sql} LIMIT {limit}"
            template_name = "aggregate_v1"

        elif isinstance(ir, DetailQueryIR):
            if not ir.select_fields:
                raise QueryValidationError("DetailQueryIR requires select_fields", failed_fields=("select_fields",))
            tbl_sql = _quote_ident(ir.table_id)
            cols_sql = ", ".join(self._validate_field(ir.table_id, c, schemas) for c in ir.select_fields)
            where_sql = self._compile_filters(ir.table_id, ir.filters, schemas, params)
            order_sql = ""
            if ir.order_by:
                order_items = [
                    f"{self._validate_field(ir.table_id, o.field_id, schemas)} {o.direction.upper()}"
                    for o in ir.order_by
                ]
                order_sql = " ORDER BY " + ", ".join(order_items)
            sql = f"SELECT {cols_sql} FROM {tbl_sql}{where_sql}{order_sql} LIMIT {limit}"
            template_name = "detail_v1"

        elif isinstance(ir, ComparePeriodsQueryIR):
            tbl_sql = _quote_ident(ir.table_id)
            date_sql = self._validate_field(ir.table_id, ir.date_field, schemas)
            if not _is_temporal(schemas[ir.table_id], ir.date_field):
                date_sql = f"TRY_CAST({date_sql} AS TIMESTAMP)"
            metric_col = self._validate_field(ir.table_id, ir.metric.field_id, schemas)
            agg_fn = ir.metric.func.value.upper()
            if ir.metric.func == AggregateFunction.COUNT_DISTINCT:
                raise QueryValidationError(
                    "compare_periods does not support COUNT_DISTINCT",
                    failed_fields=("metric",),
                )
            current_start, current_op, current_end = _period_bounds(ir.current_start, ir.current_end, "current")
            previous_start, previous_op, previous_end = _period_bounds(ir.previous_start, ir.previous_end, "previous")
            params.extend([current_start, current_end, previous_start, previous_end])
            group_cols = [self._validate_field(ir.table_id, g, schemas) for g in ir.group_by]
            group_prefix = (", ".join(group_cols) + ", ") if group_cols else ""
            group_clause = (" GROUP BY " + ", ".join(group_cols)) if group_cols else ""
            where_sql = self._compile_filters(ir.table_id, ir.filters, schemas, params)
            sql = (
                f"SELECT {group_prefix}"
                f"{agg_fn}(CASE WHEN {date_sql} >= ? AND {date_sql} {current_op} ? THEN {metric_col} ELSE NULL END) AS current_value, "
                f"{agg_fn}(CASE WHEN {date_sql} >= ? AND {date_sql} {previous_op} ? THEN {metric_col} ELSE NULL END) AS previous_value "
                f"FROM {tbl_sql}{where_sql}{group_clause} LIMIT {limit}"
            )
            template_name = "compare_periods_v1"

        elif isinstance(ir, ContributionBreakdownQueryIR):
            tbl_sql = _quote_ident(ir.table_id)
            dim_sql = self._validate_field(ir.table_id, ir.dimension_field, schemas)
            metric_col = self._validate_field(ir.table_id, ir.metric.field_id, schemas)
            agg_sql = self._compile_agg_expr(ir.metric.func, metric_col, ir.metric.alias)
            where_sql = self._compile_filters(ir.table_id, ir.filters, schemas, params)
            sql = (
                f"SELECT {dim_sql}, {agg_sql} FROM {tbl_sql}{where_sql} "
                f"GROUP BY {dim_sql} ORDER BY {_quote_ident(ir.metric.alias)} DESC LIMIT {limit}"
            )
            template_name = "contribution_breakdown_v1"

        elif isinstance(ir, DistributionQueryIR):
            tbl_sql = _quote_ident(ir.table_id)
            col_sql = self._validate_field(ir.table_id, ir.field_id, schemas)
            where_sql = self._compile_filters(ir.table_id, ir.filters, schemas, params)
            if _is_numeric(schemas[ir.table_id], ir.field_id):
                k = max(1, min(int(ir.buckets), 100))
                base_where = f"{where_sql} AND {col_sql} IS NOT NULL" if where_sql else f" WHERE {col_sql} IS NOT NULL"
                sql = (
                    f"WITH __dist_base AS (SELECT CAST({col_sql} AS DOUBLE) AS v FROM {tbl_sql}{base_where}), "
                    f"__dist_stats AS (SELECT MIN(v) AS mn, MAX(v) AS mx, AVG(v) AS av, COUNT(v) AS n FROM __dist_base) "
                    f"SELECT LEAST({k}, COALESCE(CAST(FLOOR((b.v - s.mn) / NULLIF(s.mx - s.mn, 0) * {k}) AS INTEGER), 0) + 1) AS bucket, "
                    f"MIN(b.v) AS bucket_min, MAX(b.v) AS bucket_max, COUNT(*) AS row_count, "
                    f"ANY_VALUE(s.mn) AS min_val, ANY_VALUE(s.mx) AS max_val, ANY_VALUE(s.av) AS avg_val, "
                    f"ANY_VALUE(s.n) AS non_null_count "
                    f"FROM __dist_base AS b CROSS JOIN __dist_stats AS s GROUP BY 1 ORDER BY 1 LIMIT {limit}"
                )
                template_name = "distribution_histogram_v2"
            else:
                sql = (
                    f"SELECT MIN({col_sql}) AS min_val, MAX({col_sql}) AS max_val, "
                    f"COUNT({col_sql}) AS non_null_count, COUNT(DISTINCT {col_sql}) AS distinct_count "
                    f"FROM {tbl_sql}{where_sql} LIMIT {limit}"
                )
                template_name = "distribution_summary_v2"

        elif isinstance(ir, ReconciliationLookupQueryIR):
            missing_tables = tuple(
                arg
                for arg, tid in (("left_table_id", ir.left_table_id), ("right_table_id", ir.right_table_id))
                if tid not in schemas
            )
            if missing_tables:
                raise QueryValidationError(
                    "Unknown table in reconciliation_lookup",
                    failed_fields=missing_tables,
                )
            l_tbl = _quote_ident(ir.left_table_id)
            r_tbl = _quote_ident(ir.right_table_id)
            l_key = self._validate_field(ir.left_table_id, ir.left_key, schemas, table_arg="left_table_id")
            r_key = self._validate_field(ir.right_table_id, ir.right_key, schemas, table_arg="right_table_id")
            l_amt = self._validate_field(ir.left_table_id, ir.left_amount_field, schemas, table_arg="left_table_id")
            r_amt = self._validate_field(ir.right_table_id, ir.right_amount_field, schemas, table_arg="right_table_id")
            left_cols = {c.name for c in schemas[ir.left_table_id].columns}
            right_cols = {c.name for c in schemas[ir.right_table_id].columns}
            unknown = [f.field_id for f in ir.filters if f.field_id not in left_cols | right_cols]
            if unknown:
                raise QueryValidationError(
                    f"Filter fields not found in either table: {unknown}",
                    failed_fields=("filters", *unknown),
                )
            l_where = self._compile_filters(
                ir.left_table_id, tuple(f for f in ir.filters if f.field_id in left_cols), schemas, params
            )
            r_where = self._compile_filters(
                ir.right_table_id, tuple(f for f in ir.filters if f.field_id in right_cols), schemas, params
            )
            sql = (
                f"SELECT COALESCE(l.{l_key}, r.{r_key}) AS match_key, "
                f"COALESCE(l.left_total, 0) AS left_total, "
                f"COALESCE(r.right_total, 0) AS right_total, "
                f"(COALESCE(l.left_total, 0) - COALESCE(r.right_total, 0)) AS variance "
                f"FROM (SELECT {l_key}, SUM({l_amt}) AS left_total FROM {l_tbl}{l_where} GROUP BY {l_key}) l "
                f"FULL OUTER JOIN (SELECT {r_key}, SUM({r_amt}) AS right_total FROM {r_tbl}{r_where} GROUP BY {r_key}) r "
                f"ON l.{l_key} = r.{r_key} LIMIT {limit}"
            )
            template_name = "reconciliation_lookup_v1"
        else:
            raise QueryValidationError(f"Unsupported QueryIR type: {type(ir).__name__}")

        validate_sql_ast(sql)
        return CompiledQuery(
            sql=sql,
            parameters=tuple(params),
            query_ir_hash=ir_hash,
            compiler_version=COMPILER_VERSION,
            operation=ir.operation,
            template_name=template_name,
            row_limit=row_limit,
        )
