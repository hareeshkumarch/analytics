import json
import unittest

from app.adapters.orchestration.deep_agent_runtime import _review_verdict
from app.application.services.context_compiler import (
    MAX_COMPLETION_CRITERIA,
    MAX_REVIEW_ITEMS,
    TRIM_MARK,
    bounded_items,
    clip_statement,
    fit_text,
)
from app.application.services.orchestration_service import MAX_TASK_CRITERIA, _plan_from_agent
from app.domain.models import RunMode


def _plan(criteria: int, task_criteria: int) -> str:
    return json.dumps({
        "needs_clarification": False,
        "clarification_question": None,
        "goal": "g",
        "completion_criteria": [f"criterion {i}" for i in range(criteria)],
        "tasks": [{
            "task_id": "t1_analyze", "kind": "data_specialist", "objective": "Sum fees", "dependencies": [], "required_inputs": [],
            "acceptance_criteria": [f"check {i}" for i in range(task_criteria)],
        }],
    })


KW = {"question": "q", "mode": RunMode.QUICK, "snapshot_id": "s", "semantic_version_id": "v", "revision": 1, "max_tasks": 4, "clarification_answered": False}


class TestHelpers(unittest.TestCase):
    def test_clip_statement_prefers_sentence_then_word_boundaries(self) -> None:
        self.assertEqual(clip_statement("Short.", 240), "Short.")
        text = "North leads with 12.5 units this month. " + "Detail " * 60
        self.assertEqual(clip_statement(text, 60), "North leads with 12.5 units this month.")
        clipped = clip_statement("word " * 100, 50)
        self.assertTrue(clipped.endswith("…") and not clipped.endswith(" …") and len(clipped) <= 50)

    def test_bounded_items_says_how_many_were_left_out(self) -> None:
        items = [f"gap {i}" for i in range(MAX_REVIEW_ITEMS + 2)]
        bounded = bounded_items(items, MAX_REVIEW_ITEMS, 200)
        self.assertEqual(len(bounded), MAX_REVIEW_ITEMS + 1)
        self.assertEqual(bounded[-1], "2 more not listed")
        self.assertEqual(bounded_items(["a", " ", "b"], 5, 200), ("a", "b"))

    def test_fit_text_marks_the_cut(self) -> None:
        self.assertEqual(fit_text("abc", 10), "abc")
        self.assertTrue(fit_text("x" * 10_000, 100).endswith(TRIM_MARK))


class TestPlanNotes(unittest.TestCase):
    def test_dropped_criteria_are_reported(self) -> None:
        plan, fell_back, notes = _plan_from_agent(_plan(MAX_COMPLETION_CRITERIA + 2, MAX_TASK_CRITERIA + 1), **KW)
        self.assertFalse(fell_back)
        self.assertEqual(len(plan.completion_criteria), MAX_COMPLETION_CRITERIA)
        self.assertEqual(len(plan.tasks[0].acceptance_criteria), MAX_TASK_CRITERIA)
        self.assertEqual(len(notes), 2)
        self.assertIn(f"listed {MAX_COMPLETION_CRITERIA + 2} completion criteria", notes[1])

    def test_plans_within_limits_have_no_notes(self) -> None:
        _, _, notes = _plan_from_agent(_plan(2, 2), **KW)
        self.assertEqual(notes, [])


class TestReviewVerdict(unittest.TestCase):
    def test_extra_gaps_are_counted_not_dropped(self) -> None:
        verdict, _ = _review_verdict({"approved": False, "gaps": [f"gap {i}" for i in range(7)], "contradictions": [], "summary": "s"})
        self.assertEqual(verdict.gaps[-1], "2 more not listed")
        self.assertFalse(verdict.approved)


if __name__ == "__main__":
    unittest.main()
