import codecs
import csv
import io
from typing import Any


from app.adapters.parsers.inference import infer_table
from app.adapters.parsers.registry import ParsedTable, serialize_arrow_ipc_checked
from app.domain.errors import LimitExceededError, ValidationError
from app.domain.limits import DataLimits

_BOM_ENCODINGS = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)
_FALLBACK_ENCODINGS = ("utf-8", "cp1252", "latin-1")


def _candidate_encodings(raw_bytes: bytes, requested: str | None) -> tuple[str, ...]:
    if requested:
        return (requested,)
    bom_encoding = next((enc for bom, enc in _BOM_ENCODINGS if raw_bytes.startswith(bom)), None)
    return (bom_encoding,) if bom_encoding else _FALLBACK_ENCODINGS


def _decode(raw_bytes: bytes, requested: str | None) -> str:
    encodings = _candidate_encodings(raw_bytes, requested)
    for encoding in encodings:
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError as exc:
            failure = exc
    raise ValidationError(f"Unable to decode CSV using {encodings[-1]}: {failure}") from failure


SNIFF_LINES = 50
SNIFF_DELIMITERS = ",\t;|"
DEFAULT_DELIMITER = ","


def _consistent(lines: list[str], delimiter: str) -> bool:
    counts = {len(row) for row in csv.reader(lines, delimiter=delimiter) if row}
    return len(counts) == 1 and counts.pop() > 1


def _sniff_delimiter(text: str) -> str:
    lines = [line for line in text.splitlines()[:SNIFF_LINES] if line.strip()]
    if not lines:
        return DEFAULT_DELIMITER
    try:
        guess = csv.Sniffer().sniff("\n".join(lines), delimiters=SNIFF_DELIMITERS).delimiter
    except csv.Error:
        guess = DEFAULT_DELIMITER
    if _consistent(lines, guess):
        return guess
    return next((d for d in SNIFF_DELIMITERS if _consistent(lines, d)), guess)


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
        text = _decode(raw_bytes, opts.get("encoding"))

        delimiter = opts.get("delimiter")
        if delimiter is None:
            delimiter = _sniff_delimiter(text)

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

        arrow_table, col_schemas = infer_table(headers, col_lists, opts.get("date_order"))
        ipc_bytes = serialize_arrow_ipc_checked(arrow_table, limits)
        return [
            ParsedTable(
                table_name=table_name,
                arrow_table=arrow_table,
                columns=col_schemas,
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
