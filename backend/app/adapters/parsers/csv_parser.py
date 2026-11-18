import csv
import io
import re
from typing import Any

import pyarrow as pa

from app.adapters.parsers.registry import ParsedTable, serialize_arrow_ipc_checked
from app.domain.errors import LimitExceededError, ValidationError
from app.domain.limits import DataLimits
from app.domain.models import ColumnSchema

_LEADING_ZERO_RE = re.compile(r"^0[0-9]+$")
_INT_RE = re.compile(r"^-?[1-9][0-9]*$|^0$")
_DECIMAL_RE = re.compile(r"^-?[0-9]+\.[0-9]+$")


def _infer_column_values(values: list[str | None]) -> tuple[pa.Array, str, bool]:
    non_empty = [v for v in values if v is not None and v != ""]
    if not non_empty:
        return pa.array([None if v == "" else v for v in values], type=pa.string()), "string", False

    has_leading_zero = any(_LEADING_ZERO_RE.match(v) for v in non_empty)
    if has_leading_zero:
        return pa.array([None if v == "" else v for v in values], type=pa.string()), "string", True

    if all(_INT_RE.match(v) for v in non_empty):
        parsed = [int(v) if (v is not None and v != "") else None for v in values]
        return pa.array(parsed, type=pa.int64()), "int64", False

    if all(_INT_RE.match(v) or _DECIMAL_RE.match(v) for v in non_empty):
        from decimal import Decimal
        parsed_dec = [Decimal(v) if (v is not None and v != "") else None for v in values]
        max_scale = max((len(v.split(".")[1]) if "." in v else 0) for v in non_empty)
        scale = min(max(max_scale, 2), 18)
        return pa.array(parsed_dec, type=pa.decimal128(38, scale)), f"decimal128(38,{scale})", False

    return pa.array([None if v == "" else v for v in values], type=pa.string()), "string", False


class CsvFileParser:
    format_name = "csv"

    def parse(
        self,
        raw_bytes: bytes,
        table_name: str,
        limits: DataLimits,
        options: dict[str, Any] | None = None,
    ) -> list[ParsedTable]:
        if len(raw_bytes) > limits.max_uploaded_file_bytes:
            raise LimitExceededError(
                f"Uploaded CSV exceeds max_uploaded_file_bytes ({len(raw_bytes)} > {limits.max_uploaded_file_bytes})"
            )
        opts = options or {}
        encoding = opts.get("encoding", "utf-8-sig")
        try:
            text = raw_bytes.decode(encoding)
        except UnicodeDecodeError as exc:
            raise ValidationError(f"Unable to decode CSV using {encoding}: {exc}") from exc

        delimiter = opts.get("delimiter")
        if delimiter is None:
            sample = text[:8192]
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=[",", "\t", ";", "|"])
                delimiter = dialect.delimiter
            except csv.Error:
                delimiter = ","

        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        rows = list(reader)
        if not rows:
            raise ValidationError("CSV file is empty")

        raw_headers = rows[0]
        if len(raw_headers) > limits.max_columns_per_dataset:
            raise LimitExceededError(
                f"CSV has {len(raw_headers)} columns, exceeding limit of {limits.max_columns_per_dataset}"
            )

        headers: list[str] = []
        seen: dict[str, int] = {}
        for idx, h in enumerate(raw_headers):
            name = h.strip() if h.strip() else f"column_{idx + 1}"
            if name in seen:
                seen[name] += 1
                name = f"{name}_{seen[name]}"
            else:
                seen[name] = 1
            headers.append(name)

        data_rows = rows[1:]
        if len(data_rows) > limits.max_rows_per_dataset:
            raise LimitExceededError(
                f"CSV has {len(data_rows)} rows, exceeding limit of {limits.max_rows_per_dataset}"
            )

        col_lists: list[list[str | None]] = [[] for _ in headers]
        for r_idx, row in enumerate(data_rows):
            if len(row) > len(headers):
                raise ValidationError(f"Row {r_idx + 2} has more columns than header ({len(row)} > {len(headers)})")
            for c_idx in range(len(headers)):
                val = row[c_idx] if c_idx < len(row) else ""
                if len(val) > limits.max_chars_per_cell:
                    raise LimitExceededError(f"Cell at row {r_idx + 2}, col {c_idx + 1} exceeds max_chars_per_cell")
                col_lists[c_idx].append(val)

        arrays: list[pa.Array] = []
        col_schemas: list[ColumnSchema] = []
        for header, vals in zip(headers, col_lists):
            arr, arrow_type, is_lz = _infer_column_values(vals)
            arrays.append(arr)
            col_schemas.append(
                ColumnSchema(
                    name=header,
                    arrow_type=arrow_type,
                    nullable=True,
                    is_leading_zero_id=is_lz,
                )
            )

        arrow_table = pa.table(dict(zip(headers, arrays)))
        ipc_bytes = serialize_arrow_ipc_checked(arrow_table, limits)
        return [
            ParsedTable(
                table_name=table_name,
                arrow_table=arrow_table,
                columns=tuple(col_schemas),
                ipc_bytes=ipc_bytes,
            )
        ]


class TsvFileParser(CsvFileParser):
    """Tab-separated values. Same type inference as CSV with a fixed tab delimiter."""

    format_name = "tsv"

    def parse(
        self,
        raw_bytes: bytes,
        table_name: str,
        limits: DataLimits,
        options: dict[str, Any] | None = None,
    ) -> list[ParsedTable]:
        opts = dict(options or {})
        opts.setdefault("delimiter", "\t")
        return super().parse(raw_bytes, table_name, limits, opts)
