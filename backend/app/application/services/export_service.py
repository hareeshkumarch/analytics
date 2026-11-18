import csv
import html
import io
import os
from pathlib import Path
from typing import Any

from docx import Document
from fpdf import FPDF

from app.domain.errors import LimitExceededError, ValidationError
from app.domain.limits import DataLimits, DEFAULT_DATA_LIMITS
from app.domain.models import Answer

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_UNICODE_FONT_FAMILY = "AnalyticsUnicode"


def _unicode_font_path(*, bold: bool = False) -> Path:
    configured = os.environ.get("APP_UNICODE_FONT_BOLD" if bold else "APP_UNICODE_FONT")
    candidates = ([Path(configured)] if configured else []) + [
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise ValidationError(
        "A Unicode TrueType font is required for PDF export. Install DejaVu Sans or set APP_UNICODE_FONT."
    )


def neutralize_csv_cell(value: Any) -> str:
    if value is None:
        return ""
    s = str(value)
    if s and s[0] in _FORMULA_PREFIXES:
        return "'" + s
    return s


class ExportService:
    def __init__(self, limits: DataLimits = DEFAULT_DATA_LIMITS) -> None:
        self._limits = limits

    def _check_size(self, data: bytes, fmt: str) -> bytes:
        if len(data) > self._limits.max_export_bytes:
            raise LimitExceededError(
                f"Exported {fmt.upper()} ({len(data)} bytes) exceeds max_export_bytes ({self._limits.max_export_bytes})"
            )
        return data

    def export_csv(
        self,
        columns: tuple[str, ...],
        rows: tuple[tuple[Any, ...], ...],
        *,
        metadata_header: dict[str, str] | None = None,
    ) -> bytes:
        buf = io.StringIO()
        writer = csv.writer(buf)
        if metadata_header:
            for k, v in metadata_header.items():
                writer.writerow([f"# {k}: {neutralize_csv_cell(v)}"])
        writer.writerow([neutralize_csv_cell(c) for c in columns])
        for row in rows:
            writer.writerow([neutralize_csv_cell(cell) for cell in row])
        return self._check_size(buf.getvalue().encode("utf-8"), "csv")

    def export_html(self, answer: Answer, *, scope_label: str = "Bounded Result") -> bytes:
        claims_html = "".join(
            f"<li><strong>[{html.escape(c.label)}]</strong> {html.escape(c.statement)} "
            f"(Value: {html.escape(str(c.value))} {html.escape(c.currency or c.unit or '')}; "
            f"Evidence: {html.escape(', '.join(c.evidence_ids))})</li>"
            for c in answer.claims
        )
        limitations_html = "".join(f"<li>{html.escape(lim)}</li>" for lim in answer.limitations)
        doc = (
            "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
            "<title>Analytics Export</title>"
            "<style>body{font-family:system-ui,sans-serif;background:#FAF7F5;color:#111111;max-width:860px;margin:2rem auto;padding:1.5rem;border:1px solid #D8CFCA}"
            "h1,h2{color:#3E3144}code{background:#F5F1EE;padding:2px 4px}</style></head><body>"
            f"<h1>Investigation Answer</h1>"
            f"<p><strong>Scope:</strong> {html.escape(scope_label)} | "
            f"<strong>Snapshot:</strong> <code>{html.escape(answer.snapshot_id)}</code> | "
            f"<strong>Semantic Version:</strong> <code>{html.escape(answer.semantic_version_id)}</code></p>"
            f"<div>{html.escape(answer.summary_markdown)}</div>"
            f"<h2>Validated Claims</h2><ul>{claims_html}</ul>"
            f"<h2>Limitations</h2><ul>{limitations_html}</ul>"
            "</body></html>"
        )
        return self._check_size(doc.encode("utf-8"), "html")

    def export_pdf(self, answer: Answer, *, scope_label: str = "Bounded Result") -> bytes:
        pdf = FPDF()
        regular_font = _unicode_font_path()
        bold_font = _unicode_font_path(bold=True)
        pdf.add_font(_UNICODE_FONT_FAMILY, "", str(regular_font))
        pdf.add_font(_UNICODE_FONT_FAMILY, "B", str(bold_font))
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()
        pdf.set_font(_UNICODE_FONT_FAMILY, "B", 14)
        pdf.cell(0, 10, "Agentic Analytics Workspace - Investigation Export", ln=1)
        pdf.set_font(_UNICODE_FONT_FAMILY, "", 10)
        pdf.cell(
            0,
            6,
            f"Scope: {scope_label} | Snapshot: {answer.snapshot_id} | Semantic: {answer.semantic_version_id}",
            ln=1,
        )
        pdf.ln(4)
        pdf.set_font(_UNICODE_FONT_FAMILY, "", 11)
        pdf.multi_cell(0, 6, answer.summary_markdown)
        pdf.ln(4)
        pdf.set_font(_UNICODE_FONT_FAMILY, "B", 12)
        pdf.cell(0, 8, "Validated Claims", ln=1)
        pdf.set_font(_UNICODE_FONT_FAMILY, "", 10)
        for c in answer.claims:
            line = f"[{c.label}] {c.statement} (Value: {c.value} {c.currency or c.unit or ''}; Evidence: {', '.join(c.evidence_ids)})"
            pdf.multi_cell(0, 5, line)
        if answer.limitations:
            pdf.ln(3)
            pdf.set_font(_UNICODE_FONT_FAMILY, "B", 12)
            pdf.cell(0, 8, "Limitations", ln=1)
            pdf.set_font(_UNICODE_FONT_FAMILY, "", 10)
            for lim in answer.limitations:
                pdf.multi_cell(0, 5, f"- {lim}")
        out = bytes(pdf.output())
        return self._check_size(out, "pdf")

    def export_docx(self, answer: Answer, *, scope_label: str = "Bounded Result") -> bytes:
        doc = Document()
        doc.add_heading("Agentic Analytics Workspace - Investigation Export", level=1)
        doc.add_paragraph(
            f"Scope: {scope_label} | Snapshot: {answer.snapshot_id} | Semantic: {answer.semantic_version_id}"
        )
        doc.add_paragraph(answer.summary_markdown)
        doc.add_heading("Validated Claims", level=2)
        for c in answer.claims:
            doc.add_paragraph(
                f"[{c.label}] {c.statement} (Value: {c.value} {c.currency or c.unit or ''}; Evidence: {', '.join(c.evidence_ids)})",
                style="List Bullet",
            )
        if answer.limitations:
            doc.add_heading("Limitations", level=2)
            for lim in answer.limitations:
                doc.add_paragraph(lim, style="List Bullet")
        buf = io.BytesIO()
        doc.save(buf)
        return self._check_size(buf.getvalue(), "docx")
