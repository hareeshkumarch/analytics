import re
import tempfile
import unittest

from app.application.ports.providers import ModelRequest, ModelResponse
from app.application.services.orchestration_service import InvestigationOrchestrator
from app.application.services.verification import GROUNDING_CRITERION, assess_answer, ungrounded_figures
from app.bootstrap.container import build_container
from app.domain.models import RunMode
from app.domain.providers import ModelProfile, ProviderId, ProviderProfile
from tests.test_tool_loop import ToolCallingGateway, _publish_orders

EV = "ev_0123456789ab"
EVIDENCE = [{"evidence_id": EV, "rows": [["north", 12.5, 1234.5], ["south", 0.1234, 1234567]]}]


class TestUngroundedFigures(unittest.TestCase):
    def test_matching_formats(self) -> None:
        text = (
            f"North sold 12.5 [{EV}]. Revenue was $1,234.50 [{EV}]. Margin was 12.3% [{EV}]. "
            f"Volume reached 1.2M [{EV}].\n1. In 2024 on 2024-01-31 there were 2 regions."
        )
        self.assertEqual(ungrounded_figures(text, EVIDENCE), [])

    def test_small_counts_and_year_like_figures_are_checked(self) -> None:
        self.assertEqual(ungrounded_figures(f"There were 3 regions [{EV}].", EVIDENCE), ["3"])
        self.assertEqual(ungrounded_figures("We had 42 orders.", EVIDENCE), ["42"])
        self.assertEqual(ungrounded_figures("We had 2000 orders.", EVIDENCE), ["2000"])
        self.assertEqual(ungrounded_figures(f"We had 2 regions [{EV}].", EVIDENCE), [])

    def test_years_ordinals_and_structural_numbers_are_not_figures(self) -> None:
        evidence = [{"evidence_id": EV, "rows": [["2026-10-01", 3000]]}]
        for text in (
            f"Sales in 2026 were ₹3,000 [{EV}].",
            f"FY 2025-26 sales were ₹3,000 [{EV}].",
            f"October 2026 sales were ₹3,000 [{EV}].",
            f"In Q3 2026 the top 3 regions made ₹3,000 [{EV}].",
            f"The 2nd step found ₹3,000 [{EV}].",
            f"2026 revenue was ₹3,000 [{EV}].",
        ):
            self.assertEqual(ungrounded_figures(text, evidence), [], text)

    def test_zero_padded_identifiers_are_not_figures(self) -> None:
        self.assertEqual(ungrounded_figures(f"Order 00102 appears twice [{EV}].", EVIDENCE), [])
        self.assertEqual(ungrounded_figures(f"Customer 0042 paid 999.99 [{EV}].", EVIDENCE), ["999.99"])
        self.assertEqual(ungrounded_figures("Churn was 0.5 overall.", EVIDENCE), ["0.5"])

    def test_indian_grouped_currency_formats(self) -> None:
        evidence = [{"evidence_id": EV, "rows": [[100000, 1234567.89]]}]
        self.assertEqual(ungrounded_figures(f"Revenue was ₹1,00,000 [{EV}] and ₹12,34,567.89 [{EV}].", evidence), [])
        self.assertEqual(ungrounded_figures(f"Revenue was ₹9,00,000 [{EV}].", evidence), ["₹9,00,000"])

    def test_mismatch_and_uncited_figures(self) -> None:
        self.assertEqual(ungrounded_figures(f"North sold 999.99 [{EV}].", EVIDENCE), ["999.99"])
        self.assertEqual(ungrounded_figures("Total was 1,234.5 overall.", EVIDENCE), [])
        self.assertEqual(ungrounded_figures("Total was 4,321.0 overall.", EVIDENCE), ["4,321.0"])

    def test_cited_figure_must_come_from_the_cited_evidence(self) -> None:
        other = {"evidence_id": "ev_ba9876543210", "rows": [[777.5]]}
        self.assertEqual(ungrounded_figures("It was 777.5 [ev_0123456789ab].", [*EVIDENCE, other]), ["777.5"])


class TestOutcome(unittest.TestCase):
    def _assess(self, text: str, **kw) -> tuple[str, tuple]:
        args = {"review_approved": None, "review_contradictions": False, "steps_failed": False, "stopped_early": False, **kw}
        return assess_answer(answer_markdown=text, evidence=EVIDENCE, criteria=("Answer the question",), **args)

    def test_rules(self) -> None:
        outcome, criteria = self._assess(f"North sold 12.5 [{EV}].")
        self.assertEqual(outcome, "success")
        self.assertEqual([(c.criterion, c.status) for c in criteria], [(GROUNDING_CRITERION, "verified"), ("Answer the question", "verified")])
        self.assertEqual(criteria[0].evidence_ids, (EV,))
        self.assertEqual(self._assess(f"North sold 12.5 [{EV}].", steps_failed=True)[0], "partial_success")
        self.assertEqual(self._assess(f"North sold 12.5 [{EV}].", review_approved=False)[0], "partial_success")
        self.assertEqual(self._assess(f"North sold 12.5 [{EV}].", review_contradictions=True)[0], "needs_review")
        outcome, criteria = self._assess(f"North sold 99.75 [{EV}].", steps_failed=True)
        self.assertEqual(outcome, "needs_review")
        self.assertIn("99.75", criteria[0].detail)


class CitingGateway(ToolCallingGateway):
    def __init__(self, figure: str) -> None:
        super().__init__()
        self.figure = figure

    def invoke(self, request: ModelRequest, provider_profile: ProviderProfile, model_profile: ModelProfile, api_key: str) -> ModelResponse:
        response = super().invoke(request, provider_profile, model_profile, api_key)
        found = re.findall(r"ev_[0-9a-f]{12}", " ".join(str(m.get("content")) for m in request.messages))
        if response.tool_calls or request.structured_schema or not found:
            return response
        return ModelResponse(provider_id=self.provider_id, model_id=model_profile.model_id, text=f"North leads with {self.figure} [{found[-1]}].")


class TestRunOutcome(unittest.TestCase):
    def _run(self, figure: str):
        with tempfile.TemporaryDirectory() as td:
            c = build_container(artifact_root=td)
            c.providers.register(CitingGateway(figure))
            c.routing_service._api_keys[ProviderId.OPENAI] = "test-key"
            events = []
            answer = InvestigationOrchestrator(c).execute_local(
                run_id="run_v", workspace_id="ws1", question="Which region sells most?", mode=RunMode.QUICK,
                snapshot_id="snap1", snapshot=_publish_orders(c), emit_event=events.append,
            )
            finished = [e for e in events if e.type.value == "run.finished"][-1].payload
            return answer, finished

    def test_grounded_answer_is_success(self) -> None:
        answer, finished = self._run("12.50")
        self.assertIn("North leads with 12.50 [ev_", answer.summary_markdown)
        self.assertEqual((answer.outcome, finished["outcome"], finished["status"]), ("success", "success", "completed"))

    def test_answer_citing_a_number_not_in_evidence_needs_review(self) -> None:
        answer, finished = self._run("99.75")
        self.assertEqual((answer.outcome, finished["outcome"]), ("needs_review", "needs_review"))
        self.assertEqual(answer.criteria[0].status, "failed")
        self.assertTrue(any("could not be matched" in lim for lim in answer.limitations))
        self.assertIn("North leads with 99.75", answer.summary_markdown)


if __name__ == "__main__":
    unittest.main()
