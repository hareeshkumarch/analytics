import io
from typing import Any, Protocol

import pyarrow as pa
import pyarrow.ipc as pa_ipc

from app.domain.errors import LimitExceededError, ValidationError
from app.domain.limits import DataLimits
from app.domain.models import ColumnSchema


class ParsedTable:
    def __init__(
        self,
        table_name: str,
        arrow_table: pa.Table,
        columns: tuple[ColumnSchema, ...],
        ipc_bytes: bytes,
    ) -> None:
        self.table_name = table_name
        self.arrow_table = arrow_table
        self.columns = columns
        self.ipc_bytes = ipc_bytes
        self.row_count = arrow_table.num_rows
        self.normalized_bytes = len(ipc_bytes)


class FileParser(Protocol):
    format_name: str

    def parse(
        self,
        raw_bytes: bytes,
        table_name: str,
        limits: DataLimits,
        options: dict[str, Any] | None = None,
    ) -> list[ParsedTable]: ...


def serialize_arrow_ipc_checked(
    arrow_table: pa.Table,
    limits: DataLimits,
    cumulative_normalized_bytes: int = 0,
) -> bytes:
    if arrow_table.num_rows > limits.max_rows_per_dataset:
        raise LimitExceededError(
            f"Dataset exceeds max_rows_per_dataset ({arrow_table.num_rows} > {limits.max_rows_per_dataset})",
            remediation="Select a narrower date range or subset of records.",
        )
    if arrow_table.num_columns > limits.max_columns_per_dataset:
        raise LimitExceededError(
            f"Dataset exceeds max_columns_per_dataset ({arrow_table.num_columns} > {limits.max_columns_per_dataset})",
            remediation=f"Select at most {limits.max_columns_per_dataset} columns.",
        )

    sink = io.BytesIO()
    with pa_ipc.new_stream(sink, arrow_table.schema) as writer:
        for batch in arrow_table.to_batches(max_chunksize=limits.arrow_batch_max_rows):
            writer.write_batch(batch)
    ipc_bytes = sink.getvalue()

    if cumulative_normalized_bytes + len(ipc_bytes) > limits.max_normalized_source_bytes:
        raise LimitExceededError(
            f"Normalized Arrow IPC size ({cumulative_normalized_bytes + len(ipc_bytes)} bytes) exceeds "
            f"limit ({limits.max_normalized_source_bytes} bytes).",
            remediation="Choose narrower table/column selections or a labelled sample.",
        )
    return ipc_bytes


class ParserRegistry:
    def __init__(self) -> None:
        self._parsers: dict[str, FileParser] = {}

    def register(self, format_name: str, parser: FileParser) -> None:
        self._parsers[format_name.lower()] = parser

    def get(self, format_name: str) -> FileParser:
        key = format_name.lower()
        if key not in self._parsers:
            raise ValidationError(
                f"Unsupported file format: {format_name}",
                remediation=f"Supported formats: {', '.join(self.supported_formats())}.",
            )
        return self._parsers[key]

    def supported_formats(self) -> tuple[str, ...]:
        return tuple(sorted(self._parsers.keys()))
