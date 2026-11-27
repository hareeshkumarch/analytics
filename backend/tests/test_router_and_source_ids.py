import unittest

from app.api.routes.connectors import MAX_SOURCE_SUFFIX_CHARS, scoped_source_id
from app.application.services.execution_router import ExecutionRoute, choose_route
from app.domain.models import RunMode

ONE_TABLE = {"table_details": [{"name": "orders"}]}


def route(question: str) -> ExecutionRoute:
    return choose_route(question=question, mode=RunMode.QUICK, snapshot=ONE_TABLE, has_prior_turns=False, has_clarification=False).route


class TestRouterLanguages(unittest.TestCase):
    def test_non_english_questions_take_the_standard_route(self) -> None:
        for question in ("अक्टूबर और सितंबर की फीस की तुलना करें", "¿Cuánto vendimos en octubre comparado con septiembre?", "Comparez les ventes"):
            self.assertEqual(route(question), ExecutionRoute.STANDARD, question)

    def test_short_english_questions_still_go_direct(self) -> None:
        self.assertEqual(route("What were total fees in October?"), ExecutionRoute.DIRECT_QUERY)


class TestScopedSourceIds(unittest.TestCase):
    def test_long_ids_that_share_a_prefix_stay_distinct(self) -> None:
        a = "src_" + "x" * 50 + "_alpha"
        b = "src_" + "x" * 50 + "_bravo"
        self.assertNotEqual(scoped_source_id("src_ws_", a), scoped_source_id("src_ws_", b))
        self.assertEqual(scoped_source_id("src_ws_", a), scoped_source_id("src_ws_", a))
        self.assertLessEqual(len(scoped_source_id("src_0123456789ab_", a)), 64)

    def test_ids_that_fit_are_unchanged(self) -> None:
        short = "src_" + "s" * MAX_SOURCE_SUFFIX_CHARS
        self.assertEqual(scoped_source_id("src_ws_", short), "src_ws_" + "s" * MAX_SOURCE_SUFFIX_CHARS)


if __name__ == "__main__":
    unittest.main()
