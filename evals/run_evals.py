"""Score the explainer against evals/golden.jsonl.

    uv run --project backend python evals/run_evals.py            # rules engine, writes results.md
    uv run --project backend python evals/run_evals.py --check    # exit 1 below thresholds
    uv run --project backend python evals/run_evals.py --engine claude   # needs ANTHROPIC_API_KEY

Every case goes through the real `POST /explain` endpoint (FastAPI TestClient, in-process), so
the guardrails and request validation are part of what is measured.

Metrics
  category / action / retry_safe accuracy   deterministic comparison with the golden labels
  exact match                               all three labels right in the same answer
  hallucinations                            source_code != input code, or a decline code that
                                            wasn't in the input appears in any text field
  fraud leaks                               a concealed (fraud-related) code whose customer
                                            message isn't the generic one or uses fraud wording
  consistency                               share of cases whose labels agree across all runs
  guardrail interventions                   answers the guardrails had to correct (safe
                                            fallbacks and Stripe's runtime advice excluded)
  latency p50 / p95                         per /explain call, in-process (no network)
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EVALS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVALS_DIR.parent / "backend"))

import anthropic  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.explainer import ClaudeExplainer, ExplainService  # noqa: E402
from app.guardrails import FRAUD_TERMS, GENERIC_CUSTOMER_MESSAGE, mentioned_codes  # noqa: E402
from app.kb import KnowledgeBase, get_kb  # noqa: E402
from app.main import app, get_explain_service  # noqa: E402

LABELS = ("category", "recommended_action", "retry_safe")
# Flags that aren't corrections: safe fallbacks, and Stripe's runtime advice for the charge.
EXPECTED_FLAGS = {"unknown_code", "missing_code", "advice_code_do_not_try_again"}


@dataclass
class CaseResult:
    case: dict[str, Any]
    outputs: list[dict[str, Any]] = field(default_factory=list)
    latencies_ms: list[float] = field(default_factory=list)


def build_service(engine: str, kb: KnowledgeBase) -> ExplainService:
    if engine == "rules":
        return ExplainService(kb, None)
    settings = get_settings()
    if not settings.anthropic_api_key:
        sys.exit("--engine claude needs ANTHROPIC_API_KEY in .env")
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=60.0)
    explainer = ClaudeExplainer(client, settings.explainer_model, settings.explainer_effort)
    return ExplainService(kb, explainer)


def run_cases(cases: list[dict[str, Any]], service: ExplainService, runs: int) -> list[CaseResult]:
    app.dependency_overrides[get_explain_service] = lambda: service
    client = TestClient(app)
    results = []
    try:
        for case in cases:
            result = CaseResult(case)
            for _ in range(runs):
                started = time.perf_counter()
                response = client.post("/explain", json=case["input"])
                result.latencies_ms.append((time.perf_counter() - started) * 1000)
                response.raise_for_status()
                result.outputs.append(response.json())
            results.append(result)
    finally:
        app.dependency_overrides.clear()
    return results


def hallucinated(case: dict[str, Any], out: dict[str, Any], kb: KnowledgeBase) -> bool:
    inp = case["input"]
    allowed = {c for c in (inp.get("decline_code"), inp.get("error_code")) if c}
    expected_source = inp.get("decline_code") or inp.get("error_code")
    if out["source_code"] != expected_source:
        return True
    text = f"{out['merchant_explanation']} {out['customer_message']}"
    return bool(mentioned_codes(text, kb.codes) - allowed)


def fraud_leak(case: dict[str, Any], out: dict[str, Any], kb: KnowledgeBase) -> bool:
    entry = kb.get(case["input"].get("decline_code") or case["input"].get("error_code"))
    if entry is None or not entry.conceal_reason:
        return False
    message = out["customer_message"]
    return message != GENERIC_CUSTOMER_MESSAGE or bool(FRAUD_TERMS.search(message))


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round(pct / 100 * len(ordered)) - 1))
    return ordered[index]


def score(results: list[CaseResult], kb: KnowledgeBase) -> dict[str, Any]:
    answers = [(r.case, out) for r in results for out in r.outputs]
    n = len(answers)

    def accuracy(label: str) -> float:
        correct: int = sum(out[label] == case["expected"][label] for case, out in answers)
        return correct / n

    latencies = [ms for r in results for ms in r.latencies_ms]
    return {
        "cases": len(results),
        "answers": n,
        "category_accuracy": accuracy("category"),
        "action_accuracy": accuracy("recommended_action"),
        "retry_safe_accuracy": accuracy("retry_safe"),
        "exact_match": sum(
            all(out[k] == case["expected"][k] for k in LABELS) for case, out in answers
        )
        / n,
        "hallucinations": sum(hallucinated(case, out, kb) for case, out in answers),
        "fraud_leaks": sum(fraud_leak(case, out, kb) for case, out in answers),
        "consistency": sum(
            len({tuple(out[k] for k in LABELS) for out in r.outputs}) == 1 for r in results
        )
        / len(results),
        "guardrail_interventions": sum(
            bool(set(out["flags"]) - EXPECTED_FLAGS) for _, out in answers
        ),
        "latency_p50_ms": statistics.median(latencies),
        "latency_p95_ms": percentile(latencies, 95),
    }


def mistakes(results: list[CaseResult]) -> list[tuple[str, str, str, str]]:
    rows = []
    for r in results:
        for i, out in enumerate(r.outputs, start=1):
            for label in LABELS:
                want, got = r.case["expected"][label], out[label]
                if want != got:
                    rows.append((f"{r.case['id']} (run {i})", label, str(want), str(got)))
    return rows


def render_markdown(engine: str, model: str | None, runs: int, metrics: dict[str, Any],
                    errors: list[tuple[str, str, str, str]]) -> str:  # fmt: skip

    def pct(value: float) -> str:
        return f"{value:.1%}"

    engine_label = f"claude (`{model}`)" if engine == "claude" else "rules (keyword rules, no LLM)"
    lines = [
        "# Eval results",
        "",
        f"- **Engine:** {engine_label}",
        f"- **Golden set:** {metrics['cases']} cases x {runs} runs = {metrics['answers']} answers"
        " (`evals/golden.jsonl`)",
        f"- **Run at:** {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "| Metric | Result |",
        "| --- | --- |",
        f"| Category accuracy | {pct(metrics['category_accuracy'])} |",
        f"| Recommended-action accuracy | {pct(metrics['action_accuracy'])} |",
        f"| `retry_safe` accuracy | {pct(metrics['retry_safe_accuracy'])} |",
        f"| Exact match (all three labels) | {pct(metrics['exact_match'])} |",
        f"| Hallucinated codes | {metrics['hallucinations']} |",
        f"| Fraud reason leaked to customer | {metrics['fraud_leaks']} |",
        f"| Consistency across {runs} runs | {pct(metrics['consistency'])} |",
        f"| Guardrail interventions | {metrics['guardrail_interventions']} |",
        f"| `/explain` latency p50 / p95 | {metrics['latency_p50_ms']:.1f} ms /"
        f" {metrics['latency_p95_ms']:.1f} ms |",
        "",
    ]
    if errors:
        lines += [
            "## Mismatches",
            "",
            "| Case | Label | Expected | Got |",
            "| --- | --- | --- | --- |",
        ]
        lines += [f"| {c} | {lbl} | `{want}` | `{got}` |" for c, lbl, want, got in errors]
    else:
        lines += ["No mismatches."]
    lines += [
        "",
        "Latency is measured in-process through FastAPI's test client, so it excludes network",
        "time. Expected labels were written by hand from Stripe's docs (each case quotes its",
        "basis); the rules engine was designed against the same docs, so treat its score as a",
        "regression guard rather than an independent benchmark.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Score the explainer on the golden set.")
    parser.add_argument("--engine", choices=["rules", "claude"], default="rules")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--check", action="store_true", help="exit 1 if below thresholds")
    parser.add_argument("--out", type=Path, default=EVALS_DIR / "results.md")
    args = parser.parse_args()

    cases = [
        json.loads(line) for line in (EVALS_DIR / "golden.jsonl").read_text("utf-8").splitlines()
    ]
    kb = get_kb()
    results = run_cases(cases, build_service(args.engine, kb), args.runs)
    metrics = score(results, kb)
    errors = mistakes(results)

    model = get_settings().explainer_model if args.engine == "claude" else None
    args.out.write_text(render_markdown(args.engine, model, args.runs, metrics, errors), "utf-8")
    print(json.dumps(metrics, indent=2))
    print(f"Wrote {args.out}")

    if args.check:
        thresholds = json.loads((EVALS_DIR / "thresholds.json").read_text("utf-8"))[args.engine]
        failed = [
            f"{name}: {metrics[name]} < {minimum}"
            for name, minimum in thresholds["min"].items()
            if metrics[name] < minimum
        ] + [
            f"{name}: {metrics[name]} > {maximum}"
            for name, maximum in thresholds["max"].items()
            if metrics[name] > maximum
        ]
        if failed:
            print("Eval thresholds not met:\n  " + "\n  ".join(failed))
            sys.exit(1)
        print("All eval thresholds met.")


if __name__ == "__main__":
    main()
