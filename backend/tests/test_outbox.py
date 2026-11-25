import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from app.adapters.persistence.database import create_db_engine, create_session_factory
from app.adapters.persistence.orm_models import OutboxJobRow
from app.adapters.persistence.pg_repositories import PgRunRepository
from app.domain.models import RunStatus
from app.jobs.outbox import dispatch_pending


class TestRunOutbox(unittest.TestCase):
    def setUp(self) -> None:
        self.sf = create_session_factory(create_db_engine())
        self.repo = PgRunRepository(self.sf)

    def _jobs(self) -> list[OutboxJobRow]:
        with self.sf() as db:
            return list(db.scalars(select(OutboxJobRow)).all())

    def _run(self, run_id: str, enqueue: bool) -> None:
        self.repo.create_run(
            run_id=run_id, workspace_id="ws1", conversation_id=f"c_{run_id}", mode="quick",
            status=RunStatus.QUEUED.value, question="q", enqueue=enqueue,
        )

    def test_job_is_written_with_the_run_and_survives_a_failed_publish(self) -> None:
        self._run("run_inline", enqueue=False)
        self._run("run_a", enqueue=True)
        self._run("run_b", enqueue=True)
        self.assertEqual(sorted(j.payload_json["run_id"] for j in self._jobs()), ["run_a", "run_b"])

        def broken(job: dict) -> None:
            raise ConnectionError("redis down")

        self.assertEqual(dispatch_pending(self.repo, broken), 0)
        self.assertEqual({j.status for j in self._jobs()}, {"pending"})

        sent: list[str] = []
        self.assertEqual(dispatch_pending(self.repo, lambda job: sent.append(job["payload"]["run_id"])), 2)
        self.assertEqual(sent, ["run_a", "run_b"])
        self.assertEqual(self._jobs(), [])
        self.assertEqual(dispatch_pending(self.repo, lambda job: sent.append("again")), 0)

    def test_a_stalled_claim_is_published_again(self) -> None:
        self._run("run_c", enqueue=True)
        self.assertEqual(len(self.repo.claim_outbox_jobs()), 1)
        self.assertEqual(self.repo.claim_outbox_jobs(), [])
        with self.sf() as db, db.begin():
            db.execute(update(OutboxJobRow).values(claimed_at=datetime.now(timezone.utc) - timedelta(minutes=5)))
        sent: list[str] = []
        self.assertEqual(dispatch_pending(self.repo, lambda job: sent.append(job["payload"]["run_id"])), 1)
        self.assertEqual(sent, ["run_c"])

    def test_clarification_resume_is_enqueued_in_the_same_transaction(self) -> None:
        self._run("run_d", enqueue=False)
        self.repo.update_status("run_d", RunStatus.AWAITING_INPUT)
        updated, event = self.repo.submit_clarification("run_d", expected_revision=0, answer="INR", enqueue=True)
        self.assertIsNotNone(updated)
        self.assertEqual([j.payload_json["run_id"] for j in self._jobs()], ["run_d"])


if __name__ == "__main__":
    unittest.main()
