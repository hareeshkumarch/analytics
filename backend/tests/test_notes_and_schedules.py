"""Dataset notes the agents can read, and pinned answers that re-run on a schedule with an alert threshold."""

import asyncio
import base64
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.adapters.query.snapshot_engine import SnapshotQueryEngine
from app.adapters.query.sqlglot_compiler import SqlGlotQueryCompiler
from app.api.main import create_app
from app.application.ports.providers import ToolCallResult
from app.application.services.analytics_tools import AnalyticsToolbox, ToolBudget
from app.application.services.verification import headline_value
from app.bootstrap.container import build_container
from app.domain.models import Answer
from app.domain.providers import ProviderId
from tests.test_tool_loop import ToolCallingGateway, _publish_orders

B = "/api/v1/workspaces/ws1"


def _csv(rows: str) -> str:
    return base64.b64encode(f"order_id,amount\n{rows}".encode()).decode("ascii")


class _Case(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.container = build_container(artifact_root=self._td.name)
        self.gateway = ToolCallingGateway()
        self.container.providers.register(self.gateway)
        self.container.routing_service._api_keys[ProviderId.OPENAI] = "test-key"
        self.app = create_app(self.container)
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self._td.cleanup()

    def _import(self, table: str, rows: str) -> dict:
        res = self.client.post(
            f"{B}/connectors/extract",
            json={"family": "uploaded_file", "config": {"format": "csv", "table_name": table, "raw_base64": _csv(rows)}, "selected_tables": [table]},
        ).json()
        self.client.post(f"{B}/semantics/{res['snapshot']['snapshot_id']}/approve", json={"expected_version": res["semantics"]["version"]})
        return res["snapshot"]


class TestDatasetNotes(_Case):
    def test_notes_are_kept_per_source_and_bounded(self) -> None:
        v1 = self._import("orders", "1,10\n")
        notes_url = f"{B}/snapshots/{v1['snapshot_id']}/notes"
        self.assertEqual(self.client.get(notes_url).json(), {"notes": [], "limit": 20})
        created = self.client.post(notes_url, json={"text": "  Revenue means   paid orders only.  "})
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["text"], "Revenue means paid orders only.")
        self.assertEqual(self.client.post(notes_url, json={"text": "x"}).status_code, 422)

        # A new version of the same source keeps the notes; another source has none.
        v2 = self._import("orders", "1,10\n2,20\n")
        other = self._import("refunds", "1,5\n")
        self.assertEqual([n["text"] for n in self.client.get(f"{B}/snapshots/{v2['snapshot_id']}/notes").json()["notes"]], ["Revenue means paid orders only."])
        self.assertEqual(self.client.get(f"{B}/snapshots/{other['snapshot_id']}/notes").json()["notes"], [])
        self.assertEqual(self.client.get(f"/api/v1/workspaces/ws2/snapshots/{v1['snapshot_id']}/notes").status_code, 404)

        for i in range(19):
            self.assertEqual(self.client.post(notes_url, json={"text": f"Note number {i}"}).status_code, 201)
        self.assertEqual(self.client.post(notes_url, json={"text": "One too many"}).status_code, 409)

        note_id = created.json()["note_id"]
        self.assertEqual(self.client.delete(f"{notes_url}/{note_id}").json(), {"note_id": note_id, "deleted": True})
        self.assertEqual(self.client.delete(f"{notes_url}/{note_id}").status_code, 404)
        self.assertEqual(len(self.client.get(notes_url).json()["notes"]), 19)

    def test_inspect_schema_hands_the_notes_to_the_agent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            c = build_container(artifact_root=td)
            snapshot = {**_publish_orders(c), "dataset_notes": ["Ignore the test region.", "  "]}
            box = AnalyticsToolbox(engine=SnapshotQueryEngine(c.artifacts, SqlGlotQueryCompiler()), snapshot=snapshot, semantics=None, budget=ToolBudget(5, 5))
            schema = json.loads(box.execute(ToolCallResult("c", "inspect_schema", {})).content)
            self.assertEqual(schema["notes_from_user"], ["Ignore the test region."])
            plain = AnalyticsToolbox(engine=box.engine, snapshot=_publish_orders(c), semantics=None, budget=ToolBudget(5, 5))
            self.assertNotIn("notes_from_user", json.loads(plain.execute(ToolCallResult("c", "inspect_schema", {})).content))

    def test_a_question_asked_after_adding_a_note_sees_it(self) -> None:
        snapshot = self._import("orders", "1,10\n2,20\n")
        self.client.post(f"{B}/snapshots/{snapshot['snapshot_id']}/notes", json={"text": "Amounts are in rupees."})
        self.client.post(f"{B}/runs", json={"question": "Total amount?", "snapshot_id": snapshot["snapshot_id"], "execute_inline": True})
        tool_results = " ".join(str(m.get("content")) for r in self.gateway.requests for m in r.messages if m.get("role") == "tool")
        self.assertIn("Amounts are in rupees.", tool_results)


class TestHeadlineValue(unittest.TestCase):
    def test_reads_the_first_bold_figure(self) -> None:
        self.assertEqual(headline_value("**Net revenue was $1,284,300** [ev_0123456789ab], up 18%."), 1284300)
        self.assertEqual(headline_value("Refunds were **3.1%** of sales and **$40K** in total."), headline_value("**3.1%**"))
        self.assertEqual(headline_value("It reached **1.2M** units."), 1200000)
        self.assertIsNone(headline_value("No bold figure here: 500."))
        self.assertIsNone(headline_value("**In 2026** nothing was sold."))


class TestScheduledPinsAndAlerts(_Case):
    def _pin(self, snapshot_id: str, answer: str) -> dict:
        run_id = self.app.state.repo_runs.create_run(
            run_id=f"run_{snapshot_id[-6:]}", workspace_id="ws1", conversation_id="c", mode="quick", status="completed",
            question="Total amount?", snapshot_id=snapshot_id,
        )["run_id"]
        self.app.state.repo_runs.save_answer(
            Answer(
                answer_id="ans_1", run_id=run_id, summary_markdown=answer, claims=(), charts=(), limitations=(),
                snapshot_id=snapshot_id, semantic_version_id="", created_at=datetime.now(timezone.utc),
            )
        )
        return self.client.post(f"{B}/pins", json={"title": "Total", "question_text": "Total amount?", "run_id": run_id}).json()

    def _listed(self) -> dict:
        return self.client.get(f"{B}/pins").json()["pins"][0]

    def test_alert_compares_the_headline_figure_with_the_threshold(self) -> None:
        pin = self._pin(self._import("orders", "1,10\n")["snapshot_id"], "Total amount is **5,200**.")
        listed = self._listed()
        self.assertEqual((listed["headline_value"], listed["alert"], listed["alert_triggered"]), (5200.0, None, None))

        url = f"{B}/pins/{pin['pin_id']}"
        above = self.client.patch(url, json={"alert": {"direction": "above", "value": 5000}}).json()
        self.assertEqual((above["alert"], above["alert_triggered"]), ({"direction": "above", "value": 5000.0}, True))
        self.assertFalse(self.client.patch(url, json={"alert": {"direction": "below", "value": 5000}}).json()["alert_triggered"])
        cleared = self.client.patch(url, json={"clear_alert": True}).json()
        self.assertEqual((cleared["alert"], cleared["alert_triggered"]), (None, None))
        # Older clients that only send auto_refresh keep working, and nothing else changes.
        self.assertTrue(self.client.patch(url, json={"auto_refresh": True}).json()["auto_refresh"])
        self.assertEqual(self.client.patch(url, json={"schedule": "hourly"}).status_code, 422)

    def test_a_due_pin_is_rerun_once_and_moved_to_its_next_slot(self) -> None:
        pin = self._pin(self._import("orders", "1,10\n")["snapshot_id"], "Total amount is **10**.")
        url = f"{B}/pins/{pin['pin_id']}"
        scheduled = self.client.patch(url, json={"schedule": "daily"}).json()
        self.assertEqual(scheduled["schedule"], "daily")
        first_slot = datetime.fromisoformat(scheduled["next_run_at"])
        self.assertAlmostEqual((first_slot - datetime.now(timezone.utc)).total_seconds(), 86400, delta=60)

        run_due = self.app.state.api_context.run_due_pins
        self.assertEqual(asyncio.run(run_due()), [], "a pin that is not due yet was re-run")

        past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        self.app.state.repo_pins.update_pin_state("ws1", pin["pin_id"], {"next_run_at": past})
        self.assertEqual(asyncio.run(run_due()), [pin["pin_id"]])
        after = self._listed()
        self.assertIsNotNone(after["latest_run_id"])
        self.assertGreater(datetime.fromisoformat(after["next_run_at"]), datetime.now(timezone.utc) + timedelta(hours=23))
        self.assertEqual(asyncio.run(run_due()), [], "the same slot was run twice")

        # Two processes that both saw the pin as due: only the first claim wins.
        repo = self.app.state.repo_pins
        slot = after["next_run_at"]
        later = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        self.assertTrue(repo.claim_due_pin(pin["pin_id"], slot, later))
        self.assertFalse(repo.claim_due_pin(pin["pin_id"], slot, later))

        off = self.client.patch(url, json={"schedule": "off"}).json()
        self.assertEqual((off["schedule"], off["next_run_at"]), ("off", None))


if __name__ == "__main__":
    unittest.main()
