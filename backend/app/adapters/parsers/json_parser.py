import json
from typing import Any

import pyarrow as pa

from app.adapters.parsers.registry import ParsedTable, serialize_arrow_ipc_checked
from app.domain.errors import LimitExceededError, ValidationError
from app.domain.limits import DataLimits
from app.domain.models import ColumnSchema


class JsonFileParser:
    format_name = "json"

    def parse(
        self,
        raw_bytes: bytes,
        table_name: str,
        limits: DataLimits,
        options: dict[str, Any] | None = None,
    ) -> list[ParsedTable]:
        if len(raw_bytes) > limits.max_uploaded_file_bytes:
            raise LimitExceededError(
                f"Uploaded JSON exceeds max_uploaded_file_bytes ({len(raw_bytes)} > {limits.max_uploaded_file_bytes})"
            )
        opts = options or {}
        array_mappings: dict[str, str] = opts.get("array_mappings", {})

        text = raw_bytes.decode("utf-8").strip()
        if not text:
            raise ValidationError("JSON file is empty")

        records: list[dict[str, Any]]
        if opts.get("ndjson") or (not text.startswith("[") and "\n" in text):
            records = []
            for idx, line in enumerate(text.splitlines()):
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                if not isinstance(obj, dict):
                    raise ValidationError(f"NDJSON line {idx + 1} must be a JSON object")
                records.append(obj)
        else:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                collection_path = opts.get("collection_path")
                if collection_path:
                    cur: Any = parsed
                    for part in collection_path.split("."):
                        if not isinstance(cur, dict) or part not in cur:
                            raise ValidationError(f"Collection path {collection_path!r} not found")
                        cur = cur[part]
                    parsed = cur
                else:
                    parsed = [parsed]
            if not isinstance(parsed, list) or not all(isinstance(r, dict) for r in parsed):
                raise ValidationError("JSON root or collection_path must resolve to a list of objects")
            records = parsed

        if not records:
            raise ValidationError("JSON contains zero records")
        if len(records) > limits.max_rows_per_dataset:
            raise LimitExceededError(f"JSON exceeds max_rows_per_dataset ({len(records)})")

        all_keys: list[str] = []
        seen_keys: set[str] = set()
        nested_array_fields: set[str] = set()
        for rec in records:
            for k, v in rec.items():
                if k not in seen_keys:
                    seen_keys.add(k)
                    all_keys.append(k)
                if isinstance(v, list):
                    nested_array_fields.add(k)

        unmapped = [f for f in sorted(nested_array_fields) if f not in array_mappings]
        if unmapped:
            raise ValidationError(
                f"Nested JSON array fields require explicit child-table mapping or exclusion: {', '.join(unmapped)}",
                field_errors={f: "Specify 'child_table' or 'exclude' in array_mappings" for f in unmapped},
                remediation="Map each nested array to a child table or explicitly exclude it.",
            )

        scalar_keys = [k for k in all_keys if k not in nested_array_fields]
        if len(scalar_keys) > limits.max_columns_per_dataset:
            raise LimitExceededError(f"JSON exceeds max_columns_per_dataset ({len(scalar_keys)})")

        columns_data: dict[str, list[Any]] = {k: [] for k in scalar_keys}
        for rec in records:
            for k in scalar_keys:
                val = rec.get(k)
                if isinstance(val, dict):
                    s = json.dumps(val, separators=(",", ":"))
                    if len(s) > limits.max_chars_per_cell:
                        raise LimitExceededError(f"JSON cell in field {k!r} exceeds max_chars_per_cell")
                    columns_data[k].append(s)
                else:
                    if val is not None and len(str(val)) > limits.max_chars_per_cell:
                        raise LimitExceededError(f"JSON cell in field {k!r} exceeds max_chars_per_cell")
                    columns_data[k].append(None if val is None else str(val) if isinstance(val, bool) is False and not isinstance(val, (int, float)) else val)

        arrays = {k: pa.array(v) for k, v in columns_data.items()}
        main_table = pa.table(arrays)
        main_ipc = serialize_arrow_ipc_checked(main_table, limits)
        col_schemas = tuple(
            ColumnSchema(name=field.name, arrow_type=str(field.type), nullable=True)
            for field in main_table.schema
        )
        results = [
            ParsedTable(
                table_name=table_name,
                arrow_table=main_table,
                columns=col_schemas,
                ipc_bytes=main_ipc,
            )
        ]

        cumulative_bytes = len(main_ipc)
        for arr_field, mode in array_mappings.items():
            if mode == "exclude":
                continue
            if mode != "child_table":
                raise ValidationError(f"Invalid array_mappings mode {mode!r} for {arr_field!r}")
            child_rows: list[dict[str, Any]] = []
            for idx, rec in enumerate(records):
                items = rec.get(arr_field) or []
                for item_idx, item in enumerate(items):
                    if isinstance(item, dict):
                        row: dict[str, Any] = {"_parent_row_index": idx, "_item_index": item_idx}
                        for ck, cv in item.items():
                            row[ck] = json.dumps(cv) if isinstance(cv, (dict, list)) else cv
                        child_rows.append(row)
                    else:
                        child_rows.append({"_parent_row_index": idx, "_item_index": item_idx, "value": item})
            if child_rows:
                child_keys = list(dict.fromkeys(k for r in child_rows for k in r.keys()))
                child_table = pa.table({k: pa.array([r.get(k) for r in child_rows]) for k in child_keys})
                child_ipc = serialize_arrow_ipc_checked(child_table, limits, cumulative_bytes)
                cumulative_bytes += len(child_ipc)
                child_schemas = tuple(
                    ColumnSchema(name=f.name, arrow_type=str(f.type), nullable=True)
                    for f in child_table.schema
                )
                results.append(
                    ParsedTable(
                        table_name=f"{table_name}__{arr_field}",
                        arrow_table=child_table,
                        columns=child_schemas,
                        ipc_bytes=child_ipc,
                    )
                )

        return results
