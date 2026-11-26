import os
import tempfile
import unittest
import uuid
from collections import Counter
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.adapters.persistence.orm_models import Base
from app.adapters.persistence.pg_repositories import PgEventLog, PgRunRepository
from app.application.ports.providers import ModelRequest, ModelResponse
from app.bootstrap.container import build_container
from app.domain.events import EventType
from app.domain.providers import AgentRole, ModelProfile, ProviderId, ProviderProfile
from app.jobs import runner

PG_URL = os.environ.get("TEST_POSTGRES_URL", "")


class WorkerKilled(BaseException):
    pass


class CountingGateway:
    provider_id = ProviderId.OPENAI

    def __init__(self, die_on: AgentRole | None = None) -> None:
        self.calls: Counter[str] = Counter()
        self.die_on = die_on

    def invoke(self, request: ModelRequest, provider_profile: ProviderProfile, model_profile: ModelProfile, api_key: str) -> ModelResponse:
        self.calls[request.role.value] += 1
        if request.role == self.die_on:
            raise WorkerKilled()
        return ModelResponse(provider_id=self.provider_id, model_id=model_profile.model_id, text="Fees were 100.", input_tokens=5, output_tokens=2)


@unittest.skipUnless(PG_URL, "set TEST_POSTGRES_URL to run against a real PostgreSQL")
class TestWorkerDiesAfterAStepSucceeded(unittest.TestCase):
    def test_redelivery_reuses_the_finished_step(self) -> None:
        engine = create_engine(PG_URL)
        Base.metadata.create_all(engine)
        sf = sessionmaker(bind=engine)
        runs, events = PgRunRepository(sf), PgEventLog(sf)
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        runs.create_run(run_id=run_id, workspace_id="ws_crash", conversation_id=f"c_{run_id}", mode="quick", status="queued", question="Fees?")
        artifacts = tempfile.TemporaryDirectory()
        self.addCleanup(artifacts.cleanup)

        def build_with(gateway: CountingGateway):
            def build():
                c = build_container(artifact_root=artifacts.name)
                c.providers.register(gateway)
                c.routing_service._api_keys[ProviderId.OPENAI] = "test-key"
                return c
            return build

        first, second = CountingGateway(die_on=AgentRole.ANSWER_COMPOSER), CountingGateway()
        with patch.dict(os.environ, {"DATABASE_URL": PG_URL}), patch.object(runner, "get_worker_session_factory", return_value=sf):
            with patch.object(runner, "build_container", build_with(first)), self.assertRaises(WorkerKilled):
                runner.execute_persisted_run(run_id)
            self.assertEqual(runs.get_run(run_id)["status"], "running")
            with patch.object(runner, "build_container", build_with(second)):
                result = runner.execute_persisted_run(run_id)

        self.assertGreater(first.calls["data_specialist"], 0)
        self.assertEqual(second.calls["data_specialist"], 0, second.calls)
        self.assertEqual(second.calls["answer_composer"], 1)
        self.assertEqual(result["status"], "completed")
        finishes = [e for e in events.list_after(run_id, 0) if e.type == EventType.RUN_FINISHED]
        self.assertEqual(len(finishes), 1)


if __name__ == "__main__":
    unittest.main()
