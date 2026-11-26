import threading

from app.application.ports.tools import ToolExecution


class InMemoryToolExecutionLedger:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: list[ToolExecution] = []

    def record(self, execution: ToolExecution) -> None:
        with self._lock:
            self._rows.append(execution)

    def list_for_runs(self, run_ids: list[str]) -> list[ToolExecution]:
        wanted = set(run_ids)
        with self._lock:
            return [row for row in self._rows if row.run_id in wanted]
