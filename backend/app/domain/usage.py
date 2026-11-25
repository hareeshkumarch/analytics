from collections.abc import Iterable
from decimal import Decimal
from typing import Any

UsageRow = tuple[str, str, int, Decimal | None, bool, bool, int | None]


def summarize_model_usage(rows: Iterable[UsageRow]) -> list[dict[str, object]]:
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for provider_id, model_id, tokens, cost, cost_unknown, settled, latency_ms in rows:
        g = groups.setdefault(
            (provider_id or "unknown", model_id or "unknown"),
            {"calls": 0, "unsettled_calls": 0, "tokens": 0, "cost": Decimal("0"), "cost_unknown": False, "latencies": []},
        )
        g["calls"] += 1
        g["unsettled_calls"] += 0 if settled else 1
        g["tokens"] += int(tokens or 0)
        g["cost"] += cost or Decimal("0")
        g["cost_unknown"] = g["cost_unknown"] or cost_unknown
        if latency_ms is not None:
            g["latencies"].append(latency_ms)
    return sorted(
        (
            {
                "provider_id": provider,
                "model_id": model,
                "calls": g["calls"],
                "unsettled_calls": g["unsettled_calls"],
                "total_tokens": g["tokens"],
                "total_cost_usd": str(g["cost"]),
                "cost_unknown": g["cost_unknown"],
                "avg_latency_ms": round(sum(g["latencies"]) / len(g["latencies"])) if g["latencies"] else None,
            }
            for (provider, model), g in groups.items()
        ),
        key=lambda item: (-Decimal(str(item["total_cost_usd"])), -int(str(item["total_tokens"]))),
    )
