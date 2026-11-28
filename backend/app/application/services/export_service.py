import csv
import html
import io
import os
import re
from pathlib import Path
from collections.abc import Sequence
from typing import Any

from docx import Document
from fpdf import FPDF, XPos, YPos

from app.domain.errors import LimitExceededError, ValidationError
from app.domain.limits import DataLimits, DEFAULT_DATA_LIMITS
from app.domain.models import Answer, ChartSpec

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


def _chart_header(chart: ChartSpec) -> list[str]:
    return [chart.x_field, *chart.y_fields]


def _cell(value: object) -> str:
    if value is None:
        return ""
    return f"{value:,.10g}" if isinstance(value, float) else str(value)


def neutralize_csv_cell(value: Any) -> str:
    if value is None:
        return ""
    s = str(value)
    if s and s[0] in _FORMULA_PREFIXES:
        return "'" + s
    return s


OUTCOME_LABELS = {"success": "Verified", "partial_success": "Partly verified", "needs_review": "Needs review"}


def _verification_lines(answer: Answer) -> list[str]:
    if not answer.criteria:
        return []
    lines = [f"Outcome: {OUTCOME_LABELS.get(answer.outcome, answer.outcome)}"]
    for criterion in answer.criteria:
        lines.append(f"{criterion.status}: {criterion.criterion}" + (f" ({criterion.detail})" if criterion.detail else ""))
    return lines


def _source_lines(evidence: Sequence[dict[str, Any]]) -> list[str]:
    lines = []
    for item in evidence:
        parts = [f"{item.get('operation') or 'query'}, {item.get('row_count', 0)} rows" + (" (truncated)" if item.get("truncated") else "")]
        if item.get("snapshot_id"):
            parts.append(f"snapshot {item['snapshot_id']}")
        if item.get("semantic_version_id"):
            parts.append(f"definitions {item['semantic_version_id']}")
        if item.get("metric_ids"):
            parts.append("metrics " + ", ".join(map(str, item["metric_ids"])))
        if item.get("business_timezone"):
            parts.append(f"time zone {item['business_timezone']}")
        lines.append(f"{item.get('evidence_id')}: " + "; ".join(parts))
    return lines


_INLINE_CODE_RE = re.compile(r"(`[^`\n]+`)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
_CITE_RE = re.compile(r"\[((?:ev|claim)_[A-Za-z0-9_]+)\]")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_UL_RE = re.compile(r"^[-*+]\s+(.+)$")
_OL_RE = re.compile(r"^\d+\.\s+(.+)$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?(?:\s*:?-{2,}:?\s*\|)+\s*(?::?-{2,}:?\s*)?$")


def _render_inline_html(text: str) -> str:
    """Escape text safely, then apply inline Markdown formatting and citation badges."""
    parts: list[str] = []
    for chunk in _INLINE_CODE_RE.split(text):
        if not chunk:
            continue
        if chunk.startswith("`") and chunk.endswith("`") and len(chunk) >= 2:
            parts.append(f"<code>{html.escape(chunk[1:-1], quote=True)}</code>")
            continue
        escaped = html.escape(chunk, quote=True)
        escaped = _BOLD_RE.sub(r"<strong>\1</strong>", escaped)
        escaped = _ITALIC_RE.sub(r"<em>\1</em>", escaped)
        escaped = _CITE_RE.sub(r"<span class='cite'>\1</span>", escaped)
        parts.append(escaped)
    return "".join(parts)


def _split_table_cells(row: str) -> list[str]:
    stripped = row.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _render_markdown_html(markdown: str) -> str:
    """Convert common Markdown blocks into sanitized HTML matching SafeMarkdown."""
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped:
            i += 1
            continue
        if stripped.startswith("```"):
            code_lines: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            if i < len(lines):
                i += 1
            out.append(f"<pre><code>{html.escape(chr(10).join(code_lines), quote=True)}</code></pre>")
            continue
        h_match = _HEADING_RE.match(stripped)
        if h_match:
            level = min(len(h_match.group(1)) + 1, 6)
            out.append(f"<h{level}>{_render_inline_html(h_match.group(2))}</h{level}>")
            i += 1
            continue
        if "|" in stripped and i + 1 < len(lines) and _TABLE_SEP_RE.match(lines[i + 1]):
            headers = _split_table_cells(stripped)
            i += 2
            body_rows: list[list[str]] = []
            while i < len(lines) and "|" in lines[i].strip() and lines[i].strip():
                body_rows.append(_split_table_cells(lines[i]))
                i += 1
            head_html = "".join(f"<th>{_render_inline_html(h)}</th>" for h in headers)
            rows_html = "".join(
                "<tr>" + "".join(f"<td>{_render_inline_html(row[c] if c < len(row) else '')}</td>" for c in range(len(headers))) + "</tr>"
                for row in body_rows
            )
            out.append(f"<table><thead><tr>{head_html}</tr></thead><tbody>{rows_html}</tbody></table>")
            continue
        if stripped.startswith(">"):
            quote_lines: list[str] = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote_lines.append(lines[i].strip().lstrip(">").strip())
                i += 1
            out.append(f"<blockquote><p>{_render_inline_html(' '.join(quote_lines))}</p></blockquote>")
            continue
        if _UL_RE.match(stripped):
            items: list[str] = []
            while i < len(lines):
                m = _UL_RE.match(lines[i].strip())
                if not m:
                    break
                items.append(f"<li>{_render_inline_html(m.group(1))}</li>")
                i += 1
            out.append(f"<ul>{''.join(items)}</ul>")
            continue
        if _OL_RE.match(stripped):
            items = []
            while i < len(lines):
                m = _OL_RE.match(lines[i].strip())
                if not m:
                    break
                items.append(f"<li>{_render_inline_html(m.group(1))}</li>")
                i += 1
            out.append(f"<ol>{''.join(items)}</ol>")
            continue
        para: list[str] = []
        while i < len(lines):
            cur = lines[i].strip()
            if not cur or cur.startswith("```") or cur.startswith(">") or _HEADING_RE.match(cur) or _UL_RE.match(cur) or _OL_RE.match(cur):
                break
            if "|" in cur and i + 1 < len(lines) and _TABLE_SEP_RE.match(lines[i + 1]):
                break
            para.append(cur)
            i += 1
        out.append(f"<p>{_render_inline_html(' '.join(para))}</p>")
    return "".join(out)


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

    def export_html(
        self, answer: Answer, *, scope_label: str = "Bounded Result", evidence: Sequence[dict[str, Any]] = ()
    ) -> bytes:
        claims_html = "".join(
            f"<li><strong>[{html.escape(c.label)}]</strong> {html.escape(c.statement)} "
            f"<span class='meta'>(Value: {html.escape(str(c.value))} {html.escape(c.currency or c.unit or '')}; "
            f"Evidence: {', '.join(f'<span class=\"cite\">{html.escape(eid)}</span>' for eid in c.evidence_ids)})</span></li>"
            for c in answer.claims
        )
        limitations_html = "".join(f"<li>{html.escape(lim)}</li>" for lim in answer.limitations)
        charts_html = "".join(
            f"<h3>{html.escape(ch.title)}</h3><table><thead><tr>"
            + "".join(f"<th>{html.escape(h)}</th>" for h in _chart_header(ch))
            + "</tr></thead><tbody>"
            + "".join("<tr>" + "".join(f"<td>{html.escape(_cell(v))}</td>" for v in row) + "</tr>" for row in ch.rows)
            + f"</tbody></table><p class='meta'>Evidence: <span class='cite'>{html.escape(ch.evidence_id)}</span>{' (partial data)' if ch.truncated else ''}</p>"
            for ch in answer.charts
        )
        body_html = _render_markdown_html(answer.summary_markdown)
        doc = (
            "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>Ledgerline Export</title>"
            "<style>"
            ":root{color-scheme:light}"
            "body{font-family:'Inter',system-ui,-apple-system,sans-serif;background:#F7F6F2;color:#111111;"
            "max-width:860px;margin:2.5rem auto;padding:0 1.5rem 3rem;line-height:1.6;-webkit-font-smoothing:antialiased}"
            ".card{background:#FFFFFF;border:1px solid #E6E4DF;border-radius:12px;padding:1.75rem 2rem}"
            ".eyebrow{font-size:12px;font-weight:600;color:#787671;letter-spacing:.02em;margin:0 0 4px}"
            "h1{font-family:'Newsreader',Georgia,serif;font-size:28px;line-height:1.2;font-weight:500;color:#111111;margin:0 0 .75rem}"
            "h2{font-size:17px;font-weight:600;color:#111111;margin:1.75rem 0 .65rem;padding-bottom:.35rem;border-bottom:1px solid #E6E4DF}"
            "h3,h4,h5,h6{font-size:14.5px;font-weight:600;color:#111111;margin:1.25rem 0 .45rem}"
            ".meta-bar{display:flex;flex-wrap:wrap;gap:.5rem 1.25rem;background:#F7F6F2;border:1px solid #E6E4DF;border-radius:8px;padding:.65rem .9rem;margin:0 0 1.5rem;font-size:12.5px;color:#6B6A66}"
            ".meta-bar strong{color:#111111}"
            ".prose p,.prose li,.prose blockquote{color:#6B6A66;font-size:14.5px}"
            ".prose blockquote{border-left:2px solid #111111;margin:.9rem 0;padding:.15rem 0 .15rem .9rem;color:#6B6A66}"
            "ul,ol{padding-left:1.3rem;margin:.5rem 0 .9rem}li{margin:.3rem 0}"
            "code{font-family:ui-monospace,'JetBrains Mono',monospace;font-size:12.5px;background:#F7F6F2;color:#111111;padding:1px 5px;border-radius:4px}"
            "pre{background:#F7F6F2;border:1px solid #E6E4DF;border-radius:8px;padding:.85rem 1rem;overflow-x:auto}"
            "pre code{background:transparent;padding:0}"
            ".cite{display:inline-block;font-family:ui-monospace,'JetBrains Mono',monospace;font-size:11.5px;background:#F7F6F2;color:#111111;border:1px solid #E6E4DF;padding:0 5px;border-radius:5px;vertical-align:baseline}"
            ".meta{font-size:12.5px;color:#787671}"
            "table{width:100%;border-collapse:collapse;margin:.85rem 0;font-size:13px;border:1px solid #E6E4DF;border-radius:8px;overflow:hidden}"
            "th{background:#F7F6F2;color:#111111;font-weight:600;font-size:12px}"
            "th,td{border-bottom:1px solid #E6E4DF;border-right:1px solid #E6E4DF;padding:.5rem .75rem;text-align:left}"
            "th:last-child,td:last-child{border-right:none}"
            "tr:last-child td{border-bottom:none}"
            "</style></head><body><main class='card'>"
            "<p class='eyebrow'>Ledgerline · Investigation Export</p>"
            "<h1>Investigation Answer</h1>"
            f"<div class='meta-bar'><span><strong>Scope:</strong> {html.escape(scope_label)}</span>"
            f"<span><strong>Snapshot:</strong> <code>{html.escape(answer.snapshot_id)}</code></span>"
            f"<span><strong>Semantic Version:</strong> <code>{html.escape(answer.semantic_version_id)}</code></span></div>"
            f"<section class='prose'>{body_html}</section>"
            + (f"<h2>Validated Claims</h2><ul>{claims_html}</ul>" if claims_html else "")
            + (f"<h2>Charts</h2>{charts_html}" if charts_html else "")
            + (
                "<h2>Verification</h2><ul>" + "".join(f"<li>{html.escape(line)}</li>" for line in _verification_lines(answer)) + "</ul>"
                if answer.criteria
                else ""
            )
            + (
                "<h2>Sources</h2><ul>" + "".join(f"<li>{html.escape(line)}</li>" for line in _source_lines(evidence)) + "</ul>"
                if evidence
                else ""
            )
            + (f"<h2>Limitations</h2><ul>{limitations_html}</ul>" if limitations_html else "<h2>Limitations</h2><p class='meta'>None reported.</p>")
            + "</main></body></html>"
        )
        return self._check_size(doc.encode("utf-8"), "html")

    def export_pdf(
        self, answer: Answer, *, scope_label: str = "Bounded Result", evidence: Sequence[dict[str, Any]] = ()
    ) -> bytes:
        pdf = FPDF()
        regular_font = _unicode_font_path()
        bold_font = _unicode_font_path(bold=True)
        pdf.add_font(_UNICODE_FONT_FAMILY, "", str(regular_font))
        pdf.add_font(_UNICODE_FONT_FAMILY, "B", str(bold_font))
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()
        pdf.set_font(_UNICODE_FONT_FAMILY, "B", 14)
        pdf.cell(0, 10, "Agentic Analytics Workspace - Investigation Export", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font(_UNICODE_FONT_FAMILY, "", 10)
        pdf.cell(
            0,
            6,
            f"Scope: {scope_label} | Snapshot: {answer.snapshot_id} | Semantic: {answer.semantic_version_id}",
            new_x=XPos.LMARGIN, new_y=YPos.NEXT,
        )
        pdf.ln(4)
        pdf.set_font(_UNICODE_FONT_FAMILY, "", 11)
        pdf.multi_cell(0, 6, answer.summary_markdown, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)
        pdf.set_font(_UNICODE_FONT_FAMILY, "B", 12)
        pdf.cell(0, 8, "Validated Claims", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font(_UNICODE_FONT_FAMILY, "", 10)
        for c in answer.claims:
            line = f"[{c.label}] {c.statement} (Value: {c.value} {c.currency or c.unit or ''}; Evidence: {', '.join(c.evidence_ids)})"
            pdf.multi_cell(0, 5, line, new_x="LMARGIN", new_y="NEXT")
        for ch in answer.charts:
            pdf.ln(3)
            pdf.set_font(_UNICODE_FONT_FAMILY, "B", 11)
            pdf.multi_cell(0, 6, ch.title, new_x="LMARGIN", new_y="NEXT")
            pdf.set_font(_UNICODE_FONT_FAMILY, "", 9)
            pdf.multi_cell(0, 5, " | ".join(_chart_header(ch)), new_x="LMARGIN", new_y="NEXT")
            for row in ch.rows:
                pdf.multi_cell(0, 5, " | ".join(_cell(v) for v in row), new_x="LMARGIN", new_y="NEXT")
        if answer.criteria:
            pdf.ln(3)
            pdf.set_font(_UNICODE_FONT_FAMILY, "B", 12)
            pdf.cell(0, 8, "Verification", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_font(_UNICODE_FONT_FAMILY, "", 10)
            for line in _verification_lines(answer):
                pdf.multi_cell(0, 5, f"- {line}", new_x="LMARGIN", new_y="NEXT")
        if evidence:
            pdf.ln(3)
            pdf.set_font(_UNICODE_FONT_FAMILY, "B", 12)
            pdf.cell(0, 8, "Sources", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_font(_UNICODE_FONT_FAMILY, "", 9)
            for line in _source_lines(evidence):
                pdf.multi_cell(0, 5, f"- {line}", new_x="LMARGIN", new_y="NEXT")
        if answer.limitations:
            pdf.ln(3)
            pdf.set_font(_UNICODE_FONT_FAMILY, "B", 12)
            pdf.cell(0, 8, "Limitations", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_font(_UNICODE_FONT_FAMILY, "", 10)
            for lim in answer.limitations:
                pdf.multi_cell(0, 5, f"- {lim}", new_x="LMARGIN", new_y="NEXT")
        out = bytes(pdf.output())
        return self._check_size(out, "pdf")

    def export_docx(
        self, answer: Answer, *, scope_label: str = "Bounded Result", evidence: Sequence[dict[str, Any]] = ()
    ) -> bytes:
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
        for ch in answer.charts:
            doc.add_heading(ch.title, level=3)
            header = _chart_header(ch)
            table = doc.add_table(rows=1, cols=len(header))
            for cell, name in zip(table.rows[0].cells, header):
                cell.text = name
            for row in ch.rows:
                for cell, value in zip(table.add_row().cells, row):
                    cell.text = _cell(value)
        if answer.criteria:
            doc.add_heading("Verification", level=2)
            for line in _verification_lines(answer):
                doc.add_paragraph(line, style="List Bullet")
        if evidence:
            doc.add_heading("Sources", level=2)
            for line in _source_lines(evidence):
                doc.add_paragraph(line, style="List Bullet")
        if answer.limitations:
            doc.add_heading("Limitations", level=2)
            for lim in answer.limitations:
                doc.add_paragraph(lim, style="List Bullet")
        buf = io.BytesIO()
        doc.save(buf)
        return self._check_size(buf.getvalue(), "docx")
