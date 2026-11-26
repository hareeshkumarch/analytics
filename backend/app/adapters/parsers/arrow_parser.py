import io
from collections.abc import Iterable
from typing import Any

import pyarrow as pa
import pyarrow.ipc as pa_ipc

from app.adapters.parsers.registry import ParsedTable, serialize_arrow_ipc_checked
from app.domain.errors import LimitExceededError, ValidationError
from app.domain.limits import DataLimits
from app.domain.models import ColumnSchema


def _bounded(batches: Iterable[pa.RecordBatch], schema: pa.Schema, limits: DataLimits) -> pa.Table:
    kept: list[pa.RecordBatch] = []
    size = rows = 0
    for batch in batches:
        size += batch.nbytes
        rows += batch.num_rows
        if size > limits.max_expanded_file_bytes:
            raise LimitExceededError(f"Arrow file expands past max_expanded_file_bytes ({limits.max_expanded_file_bytes})")
        if rows > limits.max_rows_per_dataset:
            raise LimitExceededError(f"Arrow file exceeds max_rows_per_dataset ({limits.max_rows_per_dataset})")
        kept.append(batch)
    return pa.Table.from_batches(kept, schema=schema)


def _read_arrow_bytes(raw_bytes: bytes, limits: DataLimits) -> pa.Table:
    """Read an Arrow IPC file (Feather v2) or an Arrow IPC stream."""
    try:
        reader = pa_ipc.open_file(io.BytesIO(raw_bytes))
        return _bounded((reader.get_batch(i) for i in range(reader.num_record_batches)), reader.schema, limits)
    except (pa.ArrowInvalid, OSError):
        pass
    try:
        stream = pa_ipc.open_stream(io.BytesIO(raw_bytes))
        return _bounded(stream, stream.schema, limits)
    except (pa.ArrowInvalid, OSError) as exc:
        raise ValidationError(
            "File is not a readable Arrow IPC / Feather v2 file.",
            remediation="Export with pyarrow.feather.write_feather (version 2) or save as Parquet.",
        ) from exc


class ArrowIpcFileParser:
    """Parses Arrow IPC files (.arrow, .feather v2) and Arrow IPC streams (.arrows)."""

    format_name = "arrow"

    def parse(
        self,
        raw_bytes: bytes,
        table_name: str,
        limits: DataLimits,
        options: dict[str, Any] | None = None,
    ) -> list[ParsedTable]:
        if len(raw_bytes) > limits.max_uploaded_file_bytes:
            raise LimitExceededError(
                f"Uploaded Arrow file exceeds max_uploaded_file_bytes ({len(raw_bytes)} > {limits.max_uploaded_file_bytes})"
            )
        table = _read_arrow_bytes(raw_bytes, limits)
        if table.num_rows > limits.max_rows_per_dataset:
            raise LimitExceededError(f"Arrow file exceeds max_rows_per_dataset ({table.num_rows})")
        if table.num_columns > limits.max_columns_per_dataset:
            raise LimitExceededError(f"Arrow file exceeds max_columns_per_dataset ({table.num_columns})")

        col_schemas: list[ColumnSchema] = []
        for field in table.schema:
            if pa.types.is_nested(field.type):
                raise ValidationError(
                    f"Arrow column {field.name!r} has nested type {field.type}; flatten it before upload.",
                    field_errors={field.name: "Nested columns are not supported"},
                )
            col_schemas.append(
                ColumnSchema(
                    name=field.name,
                    arrow_type=str(field.type),
                    nullable=field.nullable,
                    decimal_precision=field.type.precision if pa.types.is_decimal(field.type) else None,
                    decimal_scale=field.type.scale if pa.types.is_decimal(field.type) else None,
                )
            )

        ipc_bytes = serialize_arrow_ipc_checked(table, limits)
        return [ParsedTable(table_name=table_name, arrow_table=table, columns=tuple(col_schemas), ipc_bytes=ipc_bytes)]
