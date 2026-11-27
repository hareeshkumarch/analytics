import base64
import tempfile
import unittest

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.bootstrap.container import build_container

B = "/api/v1/workspaces/ws1"


def _csv(rows: str) -> str:
    return base64.b64encode(f"order_id,amount\n{rows}".encode()).decode("ascii")


class TestPinsFollowNewData(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.app = create_app(build_container(artifact_root=self._td.name))
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self._td.cleanup()

    def _import(self, table: str, rows: str, approve: bool = True) -> dict:
        res = self.client.post(
            f"{B}/connectors/extract",
            json={"family": "uploaded_file", "config": {"format": "csv", "table_name": table, "raw_base64": _csv(rows)}, "selected_tables": [table]},
        ).json()
        if approve:
            self.client.post(f"{B}/semantics/{res['snapshot']['snapshot_id']}/approve", json={"expected_version": res["semantics"]["version"]})
        return res

    def _pin(self, snapshot_id: str, auto: bool) -> dict:
        run_id = self.app.state.repo_runs.create_run(
            run_id=f"run_{snapshot_id[-6:]}", workspace_id="ws1", conversation_id="c", mode="quick", status="completed",
            question="Total amount?", snapshot_id=snapshot_id,
        )["run_id"]
        pin = self.client.post(f"{B}/pins", json={"title": "Total", "question_text": "Total amount?", "run_id": run_id}).json()
        if auto:
            pin = self.client.patch(f"{B}/pins/{pin['pin_id']}", json={"auto_refresh": True}).json()
        return pin

    def test_approving_newer_data_reruns_only_opted_in_pins_of_that_source(self) -> None:
        orders_v1 = self._import("orders", "1,10\n")["snapshot"]
        other = self._import("refunds", "1,5\n")["snapshot"]
        auto = self._pin(orders_v1["snapshot_id"], auto=True)
        manual = self._pin(other["snapshot_id"], auto=False)
        self.assertTrue(auto["auto_refresh"])

        orders_v2 = self._import("orders", "1,10\n2,20\n", approve=False)
        approved = self.client.post(
            f"{B}/semantics/{orders_v2['snapshot']['snapshot_id']}/approve", json={"expected_version": orders_v2["semantics"]["version"]}
        ).json()
        self.assertEqual(approved["refreshed_pin_ids"], [auto["pin_id"]])
        pins = {p["pin_id"]: p for p in self.client.get(f"{B}/pins").json()["pins"]}
        latest = self.client.get(f"{B}/runs/{pins[auto['pin_id']]['latest_run_id']}").json()
        self.assertEqual(latest["snapshot_id"], orders_v2["snapshot"]["snapshot_id"])
        self.assertIsNone(pins[manual["pin_id"]]["latest_run_id"])

        again = self.client.post(f"{B}/semantics/{orders_v2['snapshot']['snapshot_id']}/approve", json={"expected_version": 2})
        self.assertEqual(again.json().get("refreshed_pin_ids", []), [])

    def test_manual_refresh_uses_the_newest_version_of_the_pins_source(self) -> None:
        orders_v1 = self._import("orders", "1,10\n")["snapshot"]
        orders_v2 = self._import("orders", "1,10\n2,20\n")["snapshot"]
        self._import("refunds", "1,5\n")
        pin = self._pin(orders_v1["snapshot_id"], auto=False)
        res = self.client.post(f"{B}/pins/{pin['pin_id']}/refresh").json()
        self.assertEqual(self.client.get(f"{B}/runs/{res['refresh_run_id']}").json()["snapshot_id"], orders_v2["snapshot_id"])
        self.assertEqual(self.client.patch(f"{B}/pins/pin_missing", json={"auto_refresh": True}).status_code, 404)

    def test_pin_list_describes_the_card_and_flags_newer_data(self) -> None:
        orders_v1 = self._import("orders", "1,10\n")["snapshot"]
        pin = self._pin(orders_v1["snapshot_id"], auto=False)
        listed = self.client.get(f"{B}/pins").json()["pins"][0]
        self.assertEqual(listed["shown_run"]["run_id"], pin["run_id"])
        self.assertEqual(listed["snapshot"]["snapshot_id"], orders_v1["snapshot_id"])
        self.assertIsNone(listed["newer_version"])
        self.assertIsNone(listed["latest_run_status"])

        orders_v2 = self._import("orders", "1,10\n2,20\n")["snapshot"]
        listed = self.client.get(f"{B}/pins").json()["pins"][0]
        self.assertEqual(listed["newer_version"], orders_v2["version"])

    def test_refresh_returns_the_rerun_already_in_flight(self) -> None:
        orders_v1 = self._import("orders", "1,10\n")["snapshot"]
        pin = self._pin(orders_v1["snapshot_id"], auto=False)
        self.app.state.repo_runs.create_run(
            run_id="run_inflight", workspace_id="ws1", conversation_id="c", mode="quick", status="running",
            question="Total amount?", snapshot_id=orders_v1["snapshot_id"],
        )
        self.app.state.repo_pins.update_pin_state("ws1", pin["pin_id"], {"latest_run_id": "run_inflight"})
        before = len(self.client.get(f"{B}/runs").json()["runs"])
        res = self.client.post(f"{B}/pins/{pin['pin_id']}/refresh").json()
        self.assertEqual((res["refresh_run_id"], res["already_running"]), ("run_inflight", True))
        self.assertEqual(len(self.client.get(f"{B}/runs").json()["runs"]), before)
        self.assertEqual(self.client.get(f"{B}/pins").json()["pins"][0]["latest_run_status"], "running")

    def test_batch_refresh_reruns_only_pins_with_newer_data(self) -> None:
        orders_v1 = self._import("orders", "1,10\n")["snapshot"]
        refunds = self._import("refunds", "1,5\n")["snapshot"]
        stale = self._pin(orders_v1["snapshot_id"], auto=False)
        self._pin(refunds["snapshot_id"], auto=False)
        orders_v2 = self._import("orders", "1,10\n2,20\n")["snapshot"]

        res = self.client.post(f"{B}/pins/refresh", json={})
        self.assertEqual(res.status_code, 202, res.text)
        body = res.json()
        self.assertEqual([r["pin_id"] for r in body["refreshed"]], [stale["pin_id"]])
        self.assertEqual(body["failed"], [])
        rerun = self.client.get(f"{B}/runs/{body['refreshed'][0]['refresh_run_id']}").json()
        self.assertEqual(rerun["snapshot_id"], orders_v2["snapshot_id"])

        named = self.client.post(f"{B}/pins/refresh", json={"pin_ids": ["pin_missing"]}).json()
        self.assertEqual(named, {"refreshed": [], "failed": []})


if __name__ == "__main__":
    unittest.main()
