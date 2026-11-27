import os
import unittest
from unittest.mock import patch

from app.application.runtime_settings import AgentRuntime, ExecutionMode, agent_runtime, execution_mode, uses_celery
from app.domain.limits import DEEP_BUDGET_LIMITS, QUICK_BUDGET_LIMITS, limits_for
from app.domain.models import TaskKind
from app.domain.providers import AgentRole, role_for_kind


class TestRuntimeSettings(unittest.TestCase):
    def test_defaults_and_valid_values(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RUN_EXECUTION_MODE", None)
            os.environ.pop("AGENT_RUNTIME", None)
            self.assertEqual((execution_mode(), agent_runtime()), (ExecutionMode.INLINE, AgentRuntime.DEEP_AGENTS))
        with patch.dict(os.environ, {"RUN_EXECUTION_MODE": " Celery ", "AGENT_RUNTIME": "LEGACY"}):
            self.assertTrue(uses_celery())
            self.assertEqual(agent_runtime(), AgentRuntime.LEGACY)

    def test_typos_fail_loudly_instead_of_falling_back(self) -> None:
        for name in ("RUN_EXECUTION_MODE", "AGENT_RUNTIME"):
            with patch.dict(os.environ, {name: "celary"}), self.assertRaises(RuntimeError) as raised:
                execution_mode() if name == "RUN_EXECUTION_MODE" else agent_runtime()
            self.assertIn(name, str(raised.exception))


class TestKindsAndModes(unittest.TestCase):
    def test_roles_come_from_the_kind_not_a_substring(self) -> None:
        self.assertEqual(role_for_kind("evidence_reviewer"), AgentRole.EVIDENCE_REVIEWER)
        self.assertEqual(role_for_kind(TaskKind.RECONCILIATION_SPECIALIST), AgentRole.RECONCILIATION_SPECIALIST)
        with self.assertRaises(ValueError):
            role_for_kind("preview_builder")

    def test_unknown_run_modes_are_rejected(self) -> None:
        self.assertIs(limits_for("quick"), QUICK_BUDGET_LIMITS)
        self.assertIs(limits_for("deep"), DEEP_BUDGET_LIMITS)
        with self.assertRaises(ValueError):
            limits_for("quik")


if __name__ == "__main__":
    unittest.main()
