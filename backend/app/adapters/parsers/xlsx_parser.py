import io
import re
import zipfile
from typing import Any

import openpyxl

from app.adapters.parsers.inference import infer_table
from app.adapters.parsers.registry import ParsedTable, serialize_arrow_ipc_checked
from app.domain.errors import LimitExceededError, SecurityPolicyError, ValidationError
from app.domain.limits import DataLimits

_PRINTER_SETTINGS_PART = re.compile(r"xl/printersettings/printersettings\d+\.bin")
_EMBEDDED_OBJECT_DIRS = ("xl/activex/", "xl/embeddings/")


def _reject_unsafe_part(lower_name: str) -> None:
    if "vbaproject" in lower_name or lower_name.startswith("xl/macrosheets/"):
        raise SecurityPolicyError("Macro-enabled workbooks (.xlsm/vbaProject.bin) are rejected")
    if "externallinks" in lower_name:
        raise SecurityPolicyError("Workbooks with external links are rejected")
    is_binary = lower_name.endswith(".bin") and not _PRINTER_SETTINGS_PART.fullmatch(lower_name)
    if is_binary or lower_name.startswith(_EMBEDDED_OBJECT_DIRS):
        raise SecurityPolicyError("Workbooks with embedded binary, ActiveX or OLE objects are rejected")


def _validate_xlsx_zip_safety(raw_bytes: bytes, limits: DataLimits) -> None:
    if len(raw_bytes) > limits.max_uploaded_file_bytes:
        raise LimitExceededError(
            f"Uploaded XLSX exceeds max_uploaded_file_bytes ({len(raw_bytes)} > {limits.max_uploaded_file_bytes})"
        )
    try:
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
            total_uncompressed = 0
            for info in zf.infolist():
                _reject_unsafe_part(info.filename.lower())
                total_uncompressed += info.file_size
                if total_uncompressed > limits.max_expanded_xlsx_bytes:
                    raise LimitExceededError(
                        f"Expanded XLSX size exceeds max_expanded_xlsx_bytes ({limits.max_expanded_xlsx_bytes})"
                    )
    except zipfile.BadZipFile as exc:
        raise ValidationError("Invalid or encrypted XLSX archive") from exc


class XlsxFileParser:
    format_name = "xlsx"

    def parse(
        self,
        raw_bytes: bytes,
        table_name: str,
        limits: DataLimits,
        options: dict[str, Any] | None = None,
    ) -> list[ParsedTable]:
        _validate_xlsx_zip_safety(raw_bytes, limits)
        opts = options or {}
        selected_sheets: set[str] | None = set(opts["sheets"]) if opts.get("sheets") else None

        wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), read_only=True, data_only=True)
        try:
            sheet_names = wb.sheetnames
            if len(sheet_names) > limits.max_workbook_sheets:
                raise LimitExceededError(
                    f"Workbook has {len(sheet_names)} sheets, exceeding limit of {limits.max_workbook_sheets}"
                )

            results: list[ParsedTable] = []
            cumulative_bytes = 0
            for sheet_name in sheet_names:
                if selected_sheets is not None and sheet_name not in selected_sheets:
                    continue
                ws = wb[sheet_name]
                rows = iter(ws.iter_rows(values_only=True))
                raw_headers = next(rows, None)
                if raw_headers is None:
                    continue

                if len(raw_headers) > limits.max_columns_per_dataset:
                    raise LimitExceededError(
                        f"Sheet {sheet_name} exceeds max_columns_per_dataset ({len(raw_headers)})"
                    )

                headers: list[str] = []
                seen: dict[str, int] = {}
                for idx, h in enumerate(raw_headers):
                    name = str(h).strip() if h is not None and str(h).strip() else f"column_{idx + 1}"
                    if name in seen:
                        seen[name] += 1
                        name = f"{name}_{seen[name]}"
                    else:
                        seen[name] = 1
                    headers.append(name)

                col_lists: list[list[str | None]] = [[] for _ in headers]
                for row_count, row in enumerate(rows, start=1):
                    if row_count > limits.max_rows_per_dataset:
                        raise LimitExceededError(f"Sheet {sheet_name} exceeds max_rows_per_dataset")
                    for c_idx in range(len(headers)):
                        val = row[c_idx] if c_idx < len(row) else None
                        if val is None:
                            col_lists[c_idx].append(None)
                        else:
                            s_val = str(val)
                            if len(s_val) > limits.max_chars_per_cell:
                                raise LimitExceededError(
                                    f"Cell at {sheet_name}!R{row_count + 1}C{c_idx + 1} exceeds max_chars_per_cell"
                                )
                            if s_val.startswith("="):
                                col_lists[c_idx].append(None)
                            else:
                                col_lists[c_idx].append(s_val)

                arrow_table, col_schemas = infer_table(headers, col_lists)
                ipc_bytes = serialize_arrow_ipc_checked(arrow_table, limits, cumulative_bytes)
                cumulative_bytes += len(ipc_bytes)
                resolved_name = f"{table_name}__{sheet_name}" if len(sheet_names) > 1 else table_name
                results.append(
                    ParsedTable(
                        table_name=resolved_name,
                        arrow_table=arrow_table,
                        columns=col_schemas,
                        ipc_bytes=ipc_bytes,
                    )
                )

            if not results:
                raise ValidationError("Workbook contains no non-empty selected sheets")
            return results
        finally:
            wb.close()
