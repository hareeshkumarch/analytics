import io
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from app.adapters.parsers.registry import ParsedTable, serialize_arrow_ipc_checked
from app.domain.errors import LimitExceededError, ValidationError
from app.domain.limits import DataLimits
from app.domain.models import ColumnSchema


class ParquetFileParser:
    format_name = "parquet"

    def parse(
        self,
        raw_bytes: bytes,
        table_name: str,
        limits: DataLimits,
        options: dict[str, Any] | None = None,
    ) -> list[ParsedTable]:
        if len(raw_bytes) > limits.max_uploaded_file_bytes:
            raise LimitExceededError(
                f"Uploaded Parquet exceeds max_uploaded_file_bytes ({len(raw_bytes)} > {limits.max_uploaded_file_bytes})"
            )
        opts = options or {}
        allow_nested = set(opts.get("nested_mappings", {}).keys())

        table = pq.read_table(io.BytesIO(raw_bytes))
        if table.num_rows > limits.max_rows_per_dataset:
            raise LimitExceededError(f"Parquet exceeds max_rows_per_dataset ({table.num_rows})")
        if table.num_columns > limits.max_columns_per_dataset:
            raise LimitExceededError(f"Parquet exceeds max_columns_per_dataset ({table.num_columns})")

        col_schemas: list[ColumnSchema] = []
        for field in table.schema:
            if pa.types.is_nested(field.type) and field.name not in allow_nested:
                raise ValidationError(
                    f"Parquet column {field.name!r} has nested type {field.type}; explicit mapping required.",
                    field_errors={field.name: "Provide nested_mappings entry or exclude column"},
                )
            dec_prec = field.type.precision if pa.types.is_decimal(field.type) else None
            dec_scale = field.type.scale if pa.types.is_decimal(field.type) else None
            col_schemas.append(
                ColumnSchema(
                    name=field.name,
                    arrow_type=str(field.type),
                    nullable=field.nullable,
                    decimal_precision=dec_prec,
                    decimal_scale=dec_scale,
                )
            )

        ipc_bytes = serialize_arrow_ipc_checked(table, limits)
        return [
            ParsedTable(
                table_name=table_name,
                arrow_table=table,
                columns=tuple(col_schemas),
                ipc_bytes=ipc_bytes,
            )
        ]
