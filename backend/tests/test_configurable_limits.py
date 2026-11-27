import dataclasses
import os
import unittest
from unittest.mock import patch

from app.domain import limits as limits_module
from app.domain.limits import DataLimits, GlobalLimits, RunBudgetLimits, global_limits_from_env


class TestConfigurableLimits(unittest.TestCase):
    def test_run_capacity_comes_from_the_environment(self) -> None:
        with patch.dict(os.environ, {"MAX_ACTIVE_RUNS": "2", "MAX_QUEUED_RUNS": "25"}):
            got = global_limits_from_env()
        self.assertEqual((got.max_active_root_runs, got.max_queued_runs), (2, 25))
        with patch.dict(os.environ, {"MAX_ACTIVE_RUNS": "", "MAX_QUEUED_RUNS": ""}):
            self.assertEqual(global_limits_from_env(), GlobalLimits())

    def test_invalid_capacity_is_rejected(self) -> None:
        for bad in ("0", "-3", "many"):
            with patch.dict(os.environ, {"MAX_ACTIVE_RUNS": bad}), self.assertRaises(ValueError):
                global_limits_from_env()

    def test_the_api_semaphore_follows_the_configured_capacity(self) -> None:
        import tempfile

        from app.api import main as main_module
        from app.bootstrap.container import build_container

        with tempfile.TemporaryDirectory() as td, patch.object(main_module, "DEFAULT_GLOBAL_LIMITS", dataclasses.replace(GlobalLimits(), max_active_root_runs=2)):
            app = main_module.create_app(build_container(artifact_root=td))
        self.assertEqual(app.state.active_run_semaphore._value, 2)

    def test_no_declared_limit_is_left_unused(self) -> None:
        for removed in ("max_chart_points",):
            self.assertNotIn(removed, {f.name for f in dataclasses.fields(DataLimits)})
        self.assertNotIn("max_depth", {f.name for f in dataclasses.fields(RunBudgetLimits)})
        for removed in ("max_active_query_runners", "query_terminate_grace_seconds", "clarification_expiry_days", "sse_event_retention_days"):
            self.assertNotIn(removed, {f.name for f in dataclasses.fields(GlobalLimits)})
        self.assertFalse(hasattr(limits_module, "QUERY_EXPORT_RUNNER_PROFILE"))

    def test_connector_settings_live_in_data_limits(self) -> None:
        names = {f.name for f in dataclasses.fields(DataLimits)}
        self.assertTrue({"connector_connect_timeout_seconds", "connector_read_timeout_seconds", "connector_max_attempts", "warehouse_max_tables"} <= names)


if __name__ == "__main__":
    unittest.main()
