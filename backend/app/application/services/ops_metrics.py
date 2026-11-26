import math
from collections import Counter
from typing import Any

from app.domain.models import RunOutcome

VERIFIED_OUTCOMES = {RunOutcome.SUCCESS.value}


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)] if ordered else 0.0


def _labels(**labels: str) -> str:
    inner = ",".join(f'{k}="{str(v).replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"' for k, v in labels.items())
    return "{" + inner + "}" if inner else ""


def render_prometheus(snapshot: dict[str, Any], paused: dict[str, str], window_seconds: int) -> str:
    out: list[str] = []

    def metric(name: str, kind: str, help_text: str, samples: list[tuple[dict[str, str], float]]) -> None:
        out.append(f"# HELP {name} {help_text}")
        out.append(f"# TYPE {name} {kind}")
        out.extend(f"{name}{_labels(**labels)} {value:g}" for labels, value in samples)

    metric(
        "ledgerline_runs",
        "gauge",
        "Runs by current status, mode and outcome.",
        [({"status": r["status"], "mode": r["mode"], "outcome": r["outcome"]}, r["count"]) for r in snapshot["runs"]],
    )
    recent = snapshot["recent_finished"]
    by_outcome = Counter(r["outcome"] for r in recent)
    metric(
        "ledgerline_runs_finished_window",
        "gauge",
        f"Runs finished in the last {window_seconds} seconds by outcome.",
        [({"outcome": o}, n) for o, n in sorted(by_outcome.items())],
    )
    verified = sum(n for o, n in by_outcome.items() if o in VERIFIED_OUTCOMES)
    metric(
        "ledgerline_verified_completion_ratio",
        "gauge",
        f"Share of runs finished in the last {window_seconds} seconds whose answer was fully verified.",
        [({}, verified / len(recent) if recent else 1.0)],
    )
    metric(
        "ledgerline_run_duration_seconds",
        "gauge",
        f"Run duration from creation to finish over the last {window_seconds} seconds.",
        [
            ({"mode": mode, "quantile": label}, _quantile(values, q))
            for mode, values in sorted(snapshot["durations"].items())
            for label, q in (("0.5", 0.5), ("0.95", 0.95))
        ],
    )
    queued = sum(r["count"] for r in snapshot["runs"] if r["status"] == "queued")
    metric("ledgerline_queue_depth", "gauge", "Runs waiting to start.", [({}, queued)])
    metric("ledgerline_queue_oldest_age_seconds", "gauge", "Age of the oldest queued run.", [({}, snapshot["queue_oldest_age_seconds"])])
    metric("ledgerline_outbox_pending", "gauge", "Run jobs not yet handed to the worker queue.", [({}, snapshot["outbox_pending"])])
    metric("ledgerline_stuck_runs", "gauge", "Running runs with no event for longer than the stuck threshold.", [({}, snapshot["stuck_runs"])])
    metric(
        "ledgerline_model_calls_total",
        "counter",
        "Model calls by provider and result.",
        [({"provider": c["provider"], "result": c["result"]}, c["count"]) for c in snapshot["model_calls"]],
    )
    metric(
        "ledgerline_switch_paused",
        "gauge",
        "Operator switches that are paused (1).",
        [({"switch": s}, 1) for s in sorted(paused)],
    )
    return "\n".join(out) + "\n"
