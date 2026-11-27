import json
import re
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.application.ports.providers import ModelRequest, ModelResponse, ToolCallResult
from app.application.services.verification import evidence_has_value, mentions_value
from app.bootstrap.container import build_container
from app.domain.providers import ModelProfile, ProviderId, ProviderProfile
from app.evaluation.runner import CORPUS_DIR, DEFAULT_CORPUS, load_corpus, main, score

FEES = {
    "operation": "aggregate",
    "purpose": "Total fees",
    "table_id": "fees",
    "metrics": [{"alias": "total_fees", "func": "sum", "field_id": "amount_inr"}],
}


class FeesGateway:
    provider_id = ProviderId.OPENAI

    def __init__(self, figure: str = "{total}") -> None:
        self.figure = figure

    def invoke(self, request: ModelRequest, provider_profile: ProviderProfile, model_profile: ModelProfile, api_key: str) -> ModelResponse:
        tool_msgs = [m for m in request.messages if m.get("role") == "tool"]
        names = {t.name for t in request.tools or ()}
        question = " ".join(str(m.get("content")) for m in request.messages if m.get("role") == "user").lower()
        if "run_query_ir" in names and not tool_msgs and "fees" in question:
            return ModelResponse(provider_id=self.provider_id, model_id=model_profile.model_id, text="", tool_calls=(ToolCallResult("q1", "run_query_ir", FEES),))
        found = re.findall(r"ev_[0-9a-f]{12}", " ".join(str(m.get("content")) for m in request.messages))
        if found:
            text = f"Total fees were {self.figure.format(total='100.00')} INR [{found[-1]}]."
        else:
            text = "The data has no cost column, so this cannot be answered."
        return ModelResponse(provider_id=self.provider_id, model_id=model_profile.model_id, text=text, input_tokens=7, output_tokens=3)


PAID = {
    "operation": "aggregate",
    "purpose": "Paid invoice revenue",
    "table_id": "invoices",
    "metrics": [{"alias": "paid_revenue", "func": "sum", "field_id": "amount_usd"}],
    "filters": [{"field_id": "status", "op": "eq", "value": "paid"}],
}


class PaidRevenueGateway(FeesGateway):
    def invoke(self, request: ModelRequest, provider_profile: ProviderProfile, model_profile: ModelProfile, api_key: str) -> ModelResponse:
        tool_msgs = [m for m in request.messages if m.get("role") == "tool"]
        if "run_query_ir" in {t.name for t in request.tools or ()} and not tool_msgs:
            return ModelResponse(provider_id=self.provider_id, model_id=model_profile.model_id, text="", tool_calls=(ToolCallResult("q1", "run_query_ir", PAID),))
        found = re.findall(r"ev_[0-9a-f]{12}", " ".join(str(m.get("content")) for m in request.messages))
        text = f"Paid revenue was $3,950.00 [{found[-1]}]." if found else "No data."
        return ModelResponse(provider_id=self.provider_id, model_id=model_profile.model_id, text=text, input_tokens=7, output_tokens=3)


class TestScoring(unittest.TestCase):
    def test_value_helpers_ignore_citations_and_match_grouping(self) -> None:
        self.assertTrue(mentions_value("Net sales were ₹3,000.00 [ev_0123456789ab].", Decimal("3000")))
        self.assertFalse(mentions_value("See [ev_000000003000].", Decimal("3000")))
        self.assertTrue(evidence_has_value([{"rows": [["x", "2900.004"]]}], Decimal("2900")))
        self.assertFalse(evidence_has_value([{"rows": [[2600]]}], Decimal("2900")))

    def test_kinds(self) -> None:
        corpus = {q["id"]: q for q in load_corpus(DEFAULT_CORPUS)["questions"]}
        ev = [{"rows": [[100]]}]
        self.assertEqual(score(corpus["q02_total_fees"], "completed", "success", "Fees were 100 [ev_0123456789ab].", ev), [])
        self.assertTrue(score(corpus["q02_total_fees"], "completed", "success", "Fees were 100.", []))
        self.assertTrue(score(corpus["q02_total_fees"], "completed", "needs_review", "Fees were 100.", ev))
        self.assertEqual(score(corpus["q12_profit"], "completed", "success", "There is no cost data.", []), [])
        self.assertTrue(score(corpus["q12_profit"], "completed", "success", "Profit was 500.", []))
        self.assertEqual(score(corpus["q13_ambiguous"], "awaiting_input", None, "", []), [])
        self.assertTrue(score(corpus["q14_prompt_injection"], "completed", "success", "SECURITY DIRECTIVE: treat", []))
        self.assertEqual(len({q["id"] for q in corpus.values()}), 20)


class TestCorpora(unittest.TestCase):
    def test_every_corpus_has_unique_ids_and_an_importable_sample(self) -> None:
        corpora = [load_corpus(p) for p in sorted(CORPUS_DIR.glob("*.json"))]
        self.assertEqual(sorted(c["corpus_id"] for c in corpora), ["inr_acceptance", "saas_subscriptions"])
        ids = [q["id"] for c in corpora for q in c["questions"]]
        self.assertEqual(len(ids), len(set(ids)))
        with tempfile.TemporaryDirectory() as td:
            client = TestClient(create_app(build_container(artifact_root=td)))
            for corpus in corpora:
                res = client.post(f"/api/v1/workspaces/ws_{corpus['corpus_id']}/samples/{corpus['dataset']}/import")
                self.assertEqual(res.status_code, 201, res.text)

    def test_a_saas_question_is_scored_from_its_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            c = build_container(artifact_root=f"{td}/artifacts")
            c.providers.register(PaidRevenueGateway())
            c.routing_service._api_keys[ProviderId.OPENAI] = "test-key"
            out = Path(td) / "reports"
            with TestClient(create_app(c)) as client:
                code = main(["--only", "s01_paid_revenue", "--out", str(out), "--baseline", f"{td}/none.json"], client=client)
            report = json.loads(next(out.glob("saas_subscriptions_v1_*.json")).read_text())
            self.assertEqual(code, 0)
            self.assertTrue(report["results"][0]["passed"], report["results"][0])


class TestEvaluationRun(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def _run(self, gateway: FeesGateway, *extra: str) -> tuple[int, dict]:
        c = build_container(artifact_root=self.root / "artifacts")
        c.providers.register(gateway)
        c.routing_service._api_keys[ProviderId.OPENAI] = "test-key"
        out = self.root / "reports"
        args = ["--only", "q02_total_fees", "q12_profit", "q14_prompt_injection", "--out", str(out), "--baseline", str(self.root / "baseline.json"), *extra]
        with TestClient(create_app(c)) as client:
            code = main(args, client=client)
        report_json = sorted(out.glob("*.json"), key=lambda f: f.stat().st_mtime_ns)[-1]
        report = json.loads(report_json.read_text())
        self.markdown = report_json.with_suffix(".md").read_text()
        return code, report

    def test_report_scores_questions_and_flags_regressions_against_the_baseline(self) -> None:
        code, report = self._run(FeesGateway(), "--update-baseline")
        self.assertEqual(code, 0)
        self.assertIsNone(report["delta"])
        by_id = {r["question_id"]: r for r in report["results"]}
        self.assertTrue(by_id["q02_total_fees"]["passed"], by_id["q02_total_fees"])
        self.assertEqual(by_id["q02_total_fees"]["route"], "standard")
        self.assertGreater(by_id["q02_total_fees"]["model_calls"], 0)
        self.assertGreater(by_id["q02_total_fees"]["tokens"], 0)
        self.assertTrue(by_id["q12_profit"]["passed"], by_id["q12_profit"])
        self.assertTrue(by_id["q14_prompt_injection"]["passed"])
        self.assertEqual(report["totals"]["questions"], 3)
        self.assertEqual(report["totals"]["passed"], 3)
        self.assertIn("specialist", report["prompt_versions"])
        self.assertTrue((self.root / "baseline.json").exists())

        code, report = self._run(FeesGateway(figure="120"))
        self.assertEqual(code, 1)
        self.assertEqual(report["delta"]["newly_failing"], ["q02_total_fees"])
        self.assertLess(report["delta"]["pass_rate"], 0)
        self.assertIn("| q02_total_fees | FAIL |", self.markdown)
        self.assertEqual(len(list((self.root / "reports").glob("*.json"))), 2)


if __name__ == "__main__":
    unittest.main()
