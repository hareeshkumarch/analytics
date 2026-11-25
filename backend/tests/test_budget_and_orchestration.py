import sys
import threading
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.adapters.orchestration.supervisor import (
    DeterministicSupervisor,
    TaskAttemptResult,
    merge_task_results_reducer,
)
from app.adapters.orchestration.tool_manifest import verify_restricted_tool_manifest
from app.application.services.budget_service import AtomicBudgetLedger
from app.domain.errors import BudgetExhaustedError, SecurityPolicyError, ValidationError
from app.domain.models import PlanDAG, RunMode, TaskNode


class TestBudgetAndOrchestration(unittest.TestCase):
    def test_parallel_last_slot_budget_admission(self) -> None:
        ledger = AtomicBudgetLedger()
        ledger.configure_run("run1", max_attempts=2, max_tokens=100_000, max_cost_usd=Decimal("1.00"))

        successes: list[str] = []
        failures: list[str] = []
        lock = threading.Lock()

        def worker(idx: int) -> None:
            try:
                res = ledger.reserve(
                    "run1", f"op_{idx}", f"att_{idx}",
                    estimated_tokens=1000, estimated_cost_usd=Decimal("0.40"),
                    cost_is_unknown=False, hard_cost_ceiling_usd=Decimal("1.00"),
                )
                with lock:
                    successes.append(res.reservation_id)
            except BudgetExhaustedError as exc:
                with lock:
                    failures.append(str(exc))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(successes), 2)
        self.assertEqual(len(failures), 3)

    def test_unknown_pricing_rejected_under_hard_ceiling(self) -> None:
        ledger = AtomicBudgetLedger()
        with self.assertRaises(BudgetExhaustedError):
            ledger.reserve("run2", "op1", "att1", 1000, None, True, Decimal("1.00"))

    def test_restricted_tool_manifest_rejects_unsafe_defaults(self) -> None:
        verify_restricted_tool_manifest(["run_query_ir", "inspect_schema"])
        for bad in ("task", "bash", "execute", "browser", "write_file", "mcp_discover"):
            with self.assertRaises(SecurityPolicyError):
                verify_restricted_tool_manifest(["run_query_ir", bad])

    def test_supervisor_quick_forbids_replan_and_reducer_rejects_stale_and_conflict(self) -> None:
        sup = DeterministicSupervisor(RunMode.QUICK, "run_q")
        t1 = TaskNode("t1", "data", "step 1", (), "snap1", "sem1", (), "schema")
        t2 = TaskNode("t2", "review", "step 2", ("t1",), "snap1", "sem1", (), "schema")
        sup.accept_plan(PlanDAG(1, "goal", ("done",), (t1, t2)))

        ready = sup.next_ready_tasks()
        self.assertEqual([t.task_id for t in ready], ["t1"])

        res1 = TaskAttemptResult("t1", 1, 1, "hash_a", {"ok": True})
        sup.record_task_result(res1)
        ready2 = sup.next_ready_tasks()
        self.assertEqual([t.task_id for t in ready2], ["t2"])

        with self.assertRaises(BudgetExhaustedError):
            sup.accept_plan(PlanDAG(2, "replan", ("done",), (t1,)), is_replan=True)

        with self.assertRaises(ValidationError):
            merge_task_results_reducer(
                sup.state.results,
                TaskAttemptResult("t1", 1, 1, "hash_different", {"ok": False}),
                active_generation=1,
            )


if __name__ == "__main__":
    unittest.main()
