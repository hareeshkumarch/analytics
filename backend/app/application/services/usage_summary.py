from decimal import Decimal
from typing import Any

from app.domain.models import RunMode, RunStatus

FINISHED_STATUSES = {RunStatus.COMPLETED.value, RunStatus.FAILED.value, RunStatus.CANCELLED.value}


def summarize_usage(runs: list[tuple[str, str, str]], totals: dict[str, dict[str, Any]]) -> dict[str, Any]:
    def cost(run_id: str) -> Decimal:
        return Decimal(str(totals.get(run_id, {}).get("total_cost_usd") or "0"))

    def tokens(run_id: str) -> int:
        return int(totals.get(run_id, {}).get("total_tokens") or 0)

    modes = [m.value for m in RunMode] + sorted({mode for _, mode, _ in runs} - {m.value for m in RunMode})
    return {
        "runs": len(runs),
        "attempts": sum(int(totals.get(r, {}).get("used_attempts") or 0) for r, _, _ in runs),
        "tokens": sum(tokens(r) for r, _, _ in runs),
        "cost_usd": str(sum((cost(r) for r, _, _ in runs), Decimal(0))),
        "incomplete": sum(1 for r, _, _ in runs if totals.get(r, {}).get("incomplete")),
        "finished": sum(1 for _, _, status in runs if status in FINISHED_STATUSES),
        "completed": sum(1 for _, _, status in runs if status == RunStatus.COMPLETED.value),
        "by_mode": [
            {
                "mode": mode,
                "runs": sum(1 for _, m, _ in runs if m == mode),
                "tokens": sum(tokens(r) for r, m, _ in runs if m == mode),
                "cost_usd": str(sum((cost(r) for r, m, _ in runs if m == mode), Decimal(0))),
            }
            for mode in modes
        ],
    }
