import sys
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.application.services.export_service import ExportService, neutralize_csv_cell
from app.domain.models import Answer, Claim


class TestExportsAndSecurity(unittest.TestCase):
    def test_csv_formula_injection_neutralized(self) -> None:
        self.assertEqual(neutralize_csv_cell("=CMD('calc')"), "'=CMD('calc')")
        self.assertEqual(neutralize_csv_cell("+1+1"), "'+1+1")
        self.assertEqual(neutralize_csv_cell("-2+3"), "'-2+3")
        self.assertEqual(neutralize_csv_cell("@SUM(A1:A2)"), "'@SUM(A1:A2)")
        self.assertEqual(neutralize_csv_cell("normal_text"), "normal_text")

        svc = ExportService()
        csv_bytes = svc.export_csv(("order_id", "note"), (("00101", "=HYPERLINK(\"http://evil\")"),))
        decoded = csv_bytes.decode("utf-8")
        self.assertIn("'=HYPERLINK", decoded)

    def test_html_pdf_docx_exports_preserve_evidence_and_escape_html(self) -> None:
        ans = Answer(
            answer_id="ans_1",
            run_id="run_1",
            summary_markdown="## Summary\n\nNet sales **3,000.00 INR** [ev_1] <script>alert(1)</script>\n\n- Settlement: 2,900.00\n\n| Metric | Value |\n| --- | --- |\n| Net | 3,000 |",
            claims=(
                Claim(
                    claim_id="c1",
                    label="observation",
                    statement="Eligible settlement is 2,900.00 INR with 300.00 INR in transit.",
                    value=Decimal("2900.00"),
                    unit="INR",
                    currency="INR",
                    evidence_ids=("ev_1",),
                ),
            ),
            charts=(),
            limitations=("Missing cost column prevents profit calculation.",),
            snapshot_id="snap_1",
            semantic_version_id="sem_1",
            created_at=datetime.now(timezone.utc),
        )
        svc = ExportService()
        html_out = svc.export_html(ans).decode("utf-8")
        self.assertNotIn("<script>alert(1)</script>", html_out)
        self.assertNotIn("#3E3144", html_out)
        self.assertNotIn("#FAF7F5", html_out)
        self.assertNotIn("#C6613F", html_out)
        self.assertIn("background:#F7F6F2;color:#111111", html_out)
        self.assertIn("<h3>Summary</h3>", html_out)
        self.assertIn("<strong>3,000.00 INR</strong>", html_out)
        self.assertIn("<span class='cite'>ev_1</span>", html_out)
        self.assertIn("<ul><li>Settlement: 2,900.00</li></ul>", html_out)
        self.assertIn("<table><thead><tr><th>Metric</th><th>Value</th></tr></thead>", html_out)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html_out)
        self.assertIn("ev_1", html_out)

        pdf_bytes = svc.export_pdf(ans)
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))

        docx_bytes = svc.export_docx(ans)
        self.assertTrue(docx_bytes.startswith(b"PK"))


if __name__ == "__main__":
    unittest.main()
