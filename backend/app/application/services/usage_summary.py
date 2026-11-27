from datetime import datetime, timedelta, timezone
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


USAGE_SORT_KEYS = ("when", "calls", "tokens", "cost")


def _created(row: dict[str, Any]) -> datetime:
    """Run creation time as an aware UTC datetime (SQLite hands back naive values)."""
    try:
        value = datetime.fromisoformat(str(row.get("created_at") or ""))
    except ValueError:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def select_usage_rows(
    rows: list[dict[str, Any]],
    totals: dict[str, dict[str, Any]],
    *,
    status: str | None = None,
    mode: str | None = None,
    q: str | None = None,
    sort: str = "when",
    descending: bool = True,
) -> list[dict[str, Any]]:
    """Filter and order every run in the window, so a page of the result is a page of the whole window."""
    needle = (q or "").strip().lower()
    picked = [
        r
        for r in rows
        if (not status or r["status"] == status)
        and (not mode or r["mode"] == mode)
        and (not needle or needle in r["question"].lower() or needle in r["run_id"].lower())
    ]

    def key(r: dict[str, Any]) -> tuple[Any, str]:
        total = totals.get(r["run_id"], {})
        if sort == "calls":
            value: Any = int(total.get("used_attempts") or 0)
        elif sort == "tokens":
            value = int(total.get("total_tokens") or 0)
        elif sort == "cost":
            value = Decimal(str(total.get("total_cost_usd") or "0"))
        else:
            value = _created(r)
        return (value, r["run_id"])

    return sorted(picked, key=key, reverse=descending)


def bucket_usage(
    rows: list[dict[str, Any]],
    totals: dict[str, dict[str, Any]],
    *,
    hourly: bool = False,
    tz_offset_minutes: int = 0,
) -> list[dict[str, Any]]:
    """Per-hour or per-day totals for the usage chart, in the viewer's local time.

    `tz_offset_minutes` is what to add to UTC to get the viewer's clock. `start` in each bucket is the
    UTC instant at which that local hour or day begins.
    """
    offset = timedelta(minutes=tz_offset_minutes)
    buckets: dict[datetime, dict[str, Any]] = {}
    for r in rows:
        local = _created(r) + offset
        floor = local.replace(minute=0, second=0, microsecond=0) if hourly else local.replace(hour=0, minute=0, second=0, microsecond=0)
        start = floor - offset
        bucket = buckets.setdefault(start, {"runs": 0, "tokens": 0, "cost": Decimal(0)})
        total = totals.get(r["run_id"], {})
        bucket["runs"] += 1
        bucket["tokens"] += int(total.get("total_tokens") or 0)
        bucket["cost"] += Decimal(str(total.get("total_cost_usd") or "0"))
    return [
        {"start": start.isoformat(), "runs": b["runs"], "tokens": b["tokens"], "cost_usd": str(b["cost"])}
        for start, b in sorted(buckets.items())
    ]
