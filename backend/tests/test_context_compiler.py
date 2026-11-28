import unittest

from app.application.services.context_compiler import (
    FINDING_FLOOR_CHARS,
    TRIM_MARK,
    ContextSection,
    compile_context,
    composer_findings,
    specialist_context,
)
from app.domain.limits import CHARS_PER_TOKEN


class TestCompileContext(unittest.TestCase):
    def test_sections_that_fit_are_left_alone(self) -> None:
        compiled = compile_context([ContextSection("a", "x" * 30), ContextSection("b", "y" * 30, priority=1)], budget_tokens=100)
        self.assertEqual((compiled.trimmed, compiled.dropped), ((), ()))
        self.assertEqual(compiled.text, "x" * 30 + "\n\n" + "y" * 30)
        self.assertEqual(compiled.tokens, {"a": 10, "b": 10})

    def test_lowest_priority_is_trimmed_first_and_required_sections_are_kept(self) -> None:
        sections = [
            ContextSection("question", "q" * 3000),
            ContextSection("recent", "r" * 3000, priority=1, floor_chars=100),
            ContextSection("old", "o" * 3000, priority=2, floor_chars=100),
        ]
        compiled = compile_context(sections, budget_tokens=2500)
        by_name = {s.name: s.text for s in compiled.sections}
        self.assertEqual(by_name["question"], "q" * 3000)
        self.assertEqual(by_name["recent"], "r" * 3000)
        self.assertTrue(by_name["old"].endswith(TRIM_MARK))
        self.assertEqual(compiled.trimmed, ("old",))
        self.assertLessEqual(len(compiled.text) - 4, 2500 * CHARS_PER_TOKEN)

    def test_floors_hold_and_sections_without_a_floor_can_be_dropped(self) -> None:
        sections = [
            ContextSection("question", "q" * 300),
            ContextSection("keep", "k" * 3000, priority=1, floor_chars=200),
            ContextSection("optional", "o" * 3000, priority=2),
        ]
        compiled = compile_context(sections, budget_tokens=100)
        by_name = {s.name: s.text for s in compiled.sections}
        self.assertEqual(compiled.dropped, ("optional",))
        self.assertEqual(by_name["keep"], "k" * 200 + TRIM_MARK)
        self.assertIn("question", by_name)


class TestPromptBuilders(unittest.TestCase):
    def test_specialist_context_keeps_the_question_and_trims_findings_to_the_budget(self) -> None:
        findings = [(f"t{i}", f"Finding {i} " + "z" * 9000) for i in range(4)]
        compiled = specialist_context(task_summary="Sum fees", criteria="Cite evidence", question="What were fees?", prior_findings=findings, tool_calls=5)
        self.assertIn("<user_query>\nWhat were fees?\n</user_query>", compiled.text)
        self.assertIn("Tool calls for this step: 5", compiled.text)
        self.assertIn("Acceptance criteria for this step: Cite evidence", compiled.text)
        self.assertEqual(compiled.dropped, ())
        self.assertTrue(compiled.trimmed)
        self.assertTrue(all(f"Finding {i}" in compiled.text for i in range(4)))
        self.assertLessEqual(len(compiled.text), 6_000 * CHARS_PER_TOKEN + 4 * len(TRIM_MARK) + 100)

    def test_composer_shares_the_cut_between_long_findings(self) -> None:
        compiled = composer_findings(["old " + "a" * 12_000, "new " + "b" * 12_000, "short"])
        old, new, short = (s.text for s in compiled.sections)
        # Neither long finding is reduced to a stub so the other can stay whole.
        self.assertLess(abs(len(old) - len(new)), 50)
        self.assertGreater(len(old), 4_000)
        self.assertEqual(short, "short")
        self.assertEqual(set(compiled.trimmed), {"finding:0", "finding:1"})

    def test_composer_cuts_reviewer_notes_first(self) -> None:
        compiled = composer_findings(["analysis " + "a" * 12_000, "review " + "r" * 12_000], review_indexes=frozenset({1}))
        analysis, review = (s.text for s in compiled.sections)
        self.assertLess(len(review), len(analysis))
        self.assertGreaterEqual(len(review), FINDING_FLOOR_CHARS - 40)
        self.assertEqual(compiled.trimmed[0], "finding:1")


if __name__ == "__main__":
    unittest.main()
