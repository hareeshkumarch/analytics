import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from app.adapters.persistence.database import create_db_engine, create_session_factory
from app.adapters.persistence.pg_repositories import PgEventLog, PgRunRepository
from app.application.ports.providers import ModelRequest, ModelResponse
from app.bootstrap.container import build_container
from app.domain.events import EventType, RunEvent
from app.domain.models import Answer, RunStatus
from app.domain.providers import ModelProfile, ProviderId, ProviderProfile
from app.jobs import runner


def _event(run_id: str) -> RunEvent:
    return RunEvent(1, "evt_x", 0, "ws1", run_id, EventType.RUN_STATUS, datetime.now(timezone.utc), {"status": "running"})


def _answer(run_id: str, text: str) -> Answer:
    return Answer("ans_" + text, run_id, text, (), (), (), "", "", datetime.now(timezone.utc))


class TestFencedWrites(unittest.TestCase):
    def setUp(self) -> None:
        self.sf = create_session_factory(create_db_engine())
        self.runs = PgRunRepository(self.sf)
        self.events = PgEventLog(self.sf)
        self.runs.create_run(run_id="run_f", workspace_id="ws1", conversation_id="c", mode="quick", status="queued", question="q")

    def test_only_the_latest_claim_can_write(self) -> None:
        old = self.runs.claim_run("run_f")
        new = self.runs.claim_run("run_f")
        assert old is not None and new is not None
        self.assertGreater(new, old)
        self.assertIsNone(self.events.append(_event("run_f"), fence=old))
        self.assertFalse(self.runs.save_plan("run_f", {"tasks": []}, fence=old))
        self.assertEqual(self.runs.pause_for_clarification("run_f", "?", fence=old), ())
        self.assertFalse(self.runs.save_answer(_answer("run_f", "stale"), fence=old))
        self.assertIsNotNone(self.events.append(_event("run_f"), fence=new))
        self.assertTrue(self.runs.save_answer(_answer("run_f", "fresh"), fence=new))
        self.assertEqual(self.runs.get_run("run_f")["answer"]["summary_markdown"], "fresh")

    def test_finished_runs_cannot_be_claimed(self) -> None:
        self.runs.update_status("run_f", RunStatus.COMPLETED)
        self.assertIsNone(self.runs.claim_run("run_f"))


class _Gateway:
    provider_id = ProviderId.OPENAI

    def __init__(self, on_first_call=None) -> None:
        self.on_first_call = on_first_call

    def invoke(self, request: ModelRequest, provider_profile: ProviderProfile, model_profile: ModelProfile, api_key: str) -> ModelResponse:
        if self.on_first_call is not None:
            hook, self.on_first_call = self.on_first_call, None
            hook()
        return ModelResponse(provider_id=self.provider_id, model_id=model_profile.model_id, text="Fees were 100.", input_tokens=5, output_tokens=2)


class TestStaleWorkerStops(unittest.TestCase):
    def test_a_redelivered_run_is_finished_only_by_the_newest_worker(self) -> None:
        sf = create_session_factory(create_db_engine())
        runs = PgRunRepository(sf)
        runs.create_run(run_id="run_w", workspace_id="ws1", conversation_id="c", mode="quick", status="queued", question="Fees?")
        artifacts = tempfile.TemporaryDirectory()
        self.addCleanup(artifacts.cleanup)

        def container_with(gateway: _Gateway):
            def build():
                c = build_container(artifact_root=artifacts.name)
                c.providers.register(gateway)
                c.routing_service._api_keys[ProviderId.OPENAI] = "test-key"
                return c
            return build

        redelivered = _Gateway(on_first_call=lambda: runs.claim_run("run_w"))
        with patch.object(runner, "get_worker_session_factory", return_value=sf), patch.object(runner, "build_container", container_with(redelivered)):
            stale = runner.execute_persisted_run("run_w")
        self.assertNotEqual(stale["status"], "completed")
        self.assertIsNone(runs.get_run("run_w")["answer"].get("summary_markdown"))

        with patch.object(runner, "get_worker_session_factory", return_value=sf), patch.object(runner, "build_container", container_with(_Gateway())):
            fresh = runner.execute_persisted_run("run_w")
        self.assertEqual(fresh["status"], "completed")
        self.assertTrue(runs.get_run("run_w")["answer"]["summary_markdown"])


if __name__ == "__main__":
    unittest.main()
