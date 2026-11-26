import argparse
import json
import math
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.application.prompts import ALL_TEMPLATES
from app.application.services.verification import evidence_has_value, mentions_value

REPORT_SCHEMA_VERSION = 1
CORPUS_DIR = Path(__file__).parent / "corpus"
DEFAULT_CORPUS = CORPUS_DIR / "inr_v1.json"
DEFAULT_REPORT_DIR = Path("eval/reports")
DEFAULT_BASELINE_DIR = Path("eval/baselines")
WORKSPACE = "ws_eval"


@dataclass(slots=True)
class QuestionResult:
    question_id: str
    kind: str
    mode: str
    passed: bool
    reasons: list[str]
    status: str
    outcome: str | None
    route: str | None
    latency_s: float
    tokens: int
    cached_input_tokens: int
    cost_usd: str
    model_calls: int
    models: list[str] = field(default_factory=list)
    run_id: str = ""


def load_corpus(path: Path) -> dict[str, Any]:
    corpus: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    ids = [q["id"] for q in corpus["questions"]]
    if len(ids) != len(set(ids)):
        raise ValueError(f"Duplicate question ids in {path}")
    return corpus


def score(question: dict[str, Any], status: str, outcome: str | None, answer: str, evidence: list[dict[str, Any]]) -> list[str]:
    kind = question["kind"]
    text = answer.lower()
    reasons = [f"Forbidden text in the answer: {s!r}" for s in question.get("forbidden", ()) if s.lower() in text]
    if kind == "clarify_or_assume":
        if status != "awaiting_input" and (status != "completed" or outcome == "needs_review"):
            reasons.append(f"Expected a clarification or a grounded answer, got {status}/{outcome}")
        return reasons
    if kind == "safe":
        return reasons
    if status != "completed":
        return [*reasons, f"Run ended {status}"]
    if outcome == "needs_review":
        reasons.append("The answer cites figures its evidence does not support")
    if kind == "figures":
        for raw in question["expect"]:
            value = Decimal(raw)
            if not mentions_value(answer, value):
                reasons.append(f"Expected figure {raw} is not in the answer")
            elif not evidence_has_value(evidence, value):
                reasons.append(f"Expected figure {raw} is not backed by any evidence row")
    elif kind == "contains":
        reasons.extend(f"Expected {s!r} in the answer" for s in question["expect"] if s.lower() not in text)
    elif kind == "not_answerable" and outcome != "insufficient_data" and not any(k in text for k in question["keywords"]):
        reasons.append("The answer does not say the data cannot answer the question")
    if kind != "not_answerable" and question.get("keywords") and not any(k in text for k in question["keywords"]):
        reasons.append(f"None of {question['keywords']} is mentioned")
    return reasons


def _set_model(client: TestClient, model: str) -> None:
    url = f"/api/v1/workspaces/{WORKSPACE}/routing"
    policy = client.get(url).json()
    routed = []
    for role in policy["role_routes"]:
        res = client.put(url, json={"expected_version": policy["policy_version"], "role_routes": {role: [model]}})
        if res.status_code == 200:
            policy = res.json()
            routed.append(role)
    if not routed:
        raise SystemExit(f"Model {model!r} is not enabled for any role")


def run_question(client: TestClient, question: dict[str, Any], snapshot_id: str) -> QuestionResult:
    base = f"/api/v1/workspaces/{WORKSPACE}"
    started = time.perf_counter()
    created = client.post(
        f"{base}/runs",
        json={"question": question["question"], "mode": question["mode"], "snapshot_id": snapshot_id, "execute_inline": True},
    )
    latency = time.perf_counter() - started
    if created.status_code != 202:
        return QuestionResult(question["id"], question["kind"], question["mode"], False, [f"Run was refused: {created.text[:200]}"], "refused", None, None, round(latency, 3), 0, 0, "0", 0)
    run_id = created.json()["run_id"]
    run = client.get(f"{base}/runs/{run_id}").json()
    answer = run.get("answer") or {}
    listed = client.get(f"{base}/runs/{run_id}/evidence").json().get("items", [])
    evidence = [client.get(f"{base}/runs/{run_id}/evidence/{e['evidence_id']}").json() for e in listed]
    calls = client.get(f"{base}/runs/{run_id}/model-calls").json().get("calls", [])
    totals: dict[str, Any] = next((r for r in client.get(f"{base}/usage").json()["runs"] if r["run_id"] == run_id), {})
    reasons = score(question, run["status"], answer.get("outcome"), str(answer.get("summary_markdown") or ""), evidence)
    if run.get("error"):
        reasons.append(str(run["error"].get("message") if isinstance(run["error"], dict) else run["error"])[:200])
    return QuestionResult(
        question_id=question["id"],
        kind=question["kind"],
        mode=question["mode"],
        passed=not reasons,
        reasons=reasons,
        status=run["status"],
        outcome=answer.get("outcome"),
        route=answer.get("route"),
        latency_s=round(latency, 3),
        tokens=int(totals.get("total_tokens") or 0),
        cached_input_tokens=sum(int(c.get("cached_input_tokens") or 0) for c in calls),
        cost_usd=str(totals.get("total_cost_usd") or "0"),
        model_calls=len(calls),
        models=sorted({f"{c['provider_id']}:{c['model_id']}" for c in calls}),
        run_id=run_id,
    )


def run_corpus(client: TestClient, corpus: dict[str, Any], model: str | None = None, only: set[str] | None = None) -> list[QuestionResult]:
    imported = client.post(f"/api/v1/workspaces/{WORKSPACE}/samples/{corpus['dataset']}/import")
    if imported.status_code != 201:
        raise SystemExit(f"Could not import the {corpus['dataset']} sample: {imported.text}")
    if model:
        _set_model(client, model)
    snapshot_id = imported.json()["snapshot"]["snapshot_id"]
    return [run_question(client, q, snapshot_id) for q in corpus["questions"] if not only or q["id"] in only]


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)] if ordered else 0.0


def summarize(results: list[QuestionResult]) -> dict[str, Any]:
    passed = sum(r.passed for r in results)
    cost = sum((Decimal(r.cost_usd) for r in results), Decimal(0))
    latencies = [r.latency_s for r in results]
    return {
        "questions": len(results),
        "passed": passed,
        "pass_rate": round(passed / len(results), 4) if results else 0.0,
        "tokens": sum(r.tokens for r in results),
        "cached_input_tokens": sum(r.cached_input_tokens for r in results),
        "model_calls": sum(r.model_calls for r in results),
        "cost_usd": str(cost),
        "cost_per_verified_completion_usd": str((cost / passed).quantize(Decimal("0.000001"))) if passed else None,
        "latency_mean_s": round(statistics.fmean(latencies), 3) if latencies else 0.0,
        "latency_p95_s": round(_p95(latencies), 3),
    }


def _git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def compare(totals: dict[str, Any], results: list[QuestionResult], baseline: dict[str, Any] | None) -> dict[str, Any] | None:
    if not baseline:
        return None
    before = baseline["totals"]
    previous = {q["question_id"]: q["passed"] for q in baseline["results"]}
    delta: dict[str, Any] = {
        "baseline_created_at": baseline.get("created_at"),
        "pass_rate": round(totals["pass_rate"] - before["pass_rate"], 4),
        "tokens": totals["tokens"] - before["tokens"],
        "cost_usd": str(Decimal(totals["cost_usd"]) - Decimal(before["cost_usd"])),
        "latency_p95_s": round(totals["latency_p95_s"] - before["latency_p95_s"], 3),
        "newly_failing": [r.question_id for r in results if previous.get(r.question_id) is True and not r.passed],
        "newly_passing": [r.question_id for r in results if previous.get(r.question_id) is False and r.passed],
    }
    now_cpc, before_cpc = totals["cost_per_verified_completion_usd"], before.get("cost_per_verified_completion_usd")
    delta["cost_per_verified_completion_ratio"] = (
        round(float(Decimal(now_cpc) / Decimal(before_cpc)), 4) if now_cpc and before_cpc and Decimal(before_cpc) > 0 else None
    )
    return delta


def regressions(report: dict[str, Any], pass_margin: float, cost_margin: float) -> list[str]:
    delta = report.get("delta")
    if not delta:
        return []
    found = []
    if delta["pass_rate"] < -pass_margin:
        found.append(f"Pass rate fell by {-delta['pass_rate']:.1%}, beyond the {pass_margin:.0%} margin")
    ratio = delta["cost_per_verified_completion_ratio"]
    if ratio is not None and ratio > 1 + cost_margin:
        found.append(f"Cost per verified completion rose {ratio - 1:.0%}, beyond the {cost_margin:.0%} margin")
    return found


def build_report(corpus: dict[str, Any], results: list[QuestionResult], model: str | None, baseline: dict[str, Any] | None) -> dict[str, Any]:
    totals = summarize(results)
    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_commit": _git_commit(),
        "corpus": {"id": corpus["corpus_id"], "version": corpus["version"], "dataset": corpus["dataset"]},
        "model": model,
        "prompt_versions": {t.template_id: t.version for t in ALL_TEMPLATES},
        "totals": totals,
        "delta": compare(totals, results, baseline),
        "results": [asdict(r) for r in results],
    }


def render_markdown(report: dict[str, Any], problems: list[str]) -> str:
    t = report["totals"]
    lines = [
        f"# Evaluation: {report['corpus']['id']} v{report['corpus']['version']}",
        "",
        f"Model: {report['model'] or 'workspace default'} · commit {report['git_commit'] or 'unknown'} · {report['created_at']}",
        "",
        f"**{t['passed']}/{t['questions']} passed ({t['pass_rate']:.0%})** · tokens {t['tokens']} (cached {t['cached_input_tokens']}) · "
        f"cost ${t['cost_usd']} · per verified completion ${t['cost_per_verified_completion_usd'] or 'n/a'} · "
        f"latency mean {t['latency_mean_s']}s, p95 {t['latency_p95_s']}s",
        "",
    ]
    delta = report["delta"]
    if delta:
        lines += [
            f"Against the baseline of {delta['baseline_created_at']}: pass rate {delta['pass_rate']:+.1%}, tokens {delta['tokens']:+d}, "
            f"cost {Decimal(delta['cost_usd']):+} USD, p95 latency {delta['latency_p95_s']:+}s.",
            f"Newly failing: {', '.join(delta['newly_failing']) or 'none'}. Newly passing: {', '.join(delta['newly_passing']) or 'none'}.",
            "",
        ]
    else:
        lines += ["No baseline to compare against.", ""]
    lines += [*(f"**Regression:** {p}" for p in problems), *([""] if problems else [])]
    lines += ["| Question | Result | Route | Outcome | Tokens | Cost | Latency | Notes |", "|---|---|---|---|---|---|---|---|"]
    for r in report["results"]:
        notes = "; ".join(r["reasons"]).replace("|", "\\|")
        lines.append(
            f"| {r['question_id']} | {'pass' if r['passed'] else 'FAIL'} | {r['route'] or '-'} | {r['outcome'] or r['status']} | "
            f"{r['tokens']} | ${r['cost_usd']} | {r['latency_s']}s | {notes} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None, client: TestClient | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.evaluation", description="Run the evaluation corpus and compare with the baseline.")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--model", help="Model id to route every role to, from config/models.yaml; default is the workspace routing")
    parser.add_argument("--only", nargs="*", help="Question ids to run")
    parser.add_argument("--out", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--baseline", type=Path, help="Baseline report; default eval/baselines/<corpus>_v<version>.json")
    parser.add_argument("--update-baseline", action="store_true", help="Save this report as the new baseline unless it regressed")
    parser.add_argument("--pass-margin", type=float, default=0.1, help="Allowed drop in pass rate")
    parser.add_argument("--cost-margin", type=float, default=0.25, help="Allowed rise in cost per verified completion")
    args = parser.parse_args(argv)

    corpus = load_corpus(args.corpus)
    baseline_path = args.baseline or DEFAULT_BASELINE_DIR / f"{corpus['corpus_id']}_v{corpus['version']}.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8")) if baseline_path.exists() else None
    if client is None:
        from app.api.main import create_app
        from app.bootstrap.container import build_container

        with tempfile.TemporaryDirectory() as td, TestClient(create_app(build_container(artifact_root=td))) as fresh:
            results = run_corpus(fresh, corpus, args.model, set(args.only or ()))
    else:
        results = run_corpus(client, corpus, args.model, set(args.only or ()))

    report = build_report(corpus, results, args.model, baseline)
    problems = regressions(report, args.pass_margin, args.cost_margin)
    stamp = report["created_at"].replace(":", "").replace("-", "").replace("+0000", "Z")
    args.out.mkdir(parents=True, exist_ok=True)
    name = f"{corpus['corpus_id']}_v{corpus['version']}_{stamp}"
    (args.out / f"{name}.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    markdown = render_markdown(report, problems)
    (args.out / f"{name}.md").write_text(markdown, encoding="utf-8")
    if args.update_baseline and not problems:
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(markdown)
    return 1 if problems else 0
