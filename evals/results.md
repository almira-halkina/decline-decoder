# Eval results

- **Engine:** rules (keyword rules, no LLM)
- **Golden set:** 32 cases x 3 runs = 96 answers (`evals/golden.jsonl`)
- **Run at:** 2026-10-07 09:09 UTC

| Metric | Result |
| --- | --- |
| Category accuracy | 100.0% |
| Recommended-action accuracy | 100.0% |
| `retry_safe` accuracy | 100.0% |
| Exact match (all three labels) | 100.0% |
| Hallucinated codes | 0 |
| Fraud reason leaked to customer | 0 |
| Consistency across 3 runs | 100.0% |
| Guardrail interventions | 0 |
| `/explain` latency p50 / p95 | 7.1 ms / 9.0 ms |

No mismatches.

Latency is measured in-process through FastAPI's test client, so it excludes network
time. Expected labels were written by hand from Stripe's docs (each case quotes its
basis); the rules engine was designed against the same docs, so treat its score as a
regression guard rather than an independent benchmark.
