import { useCallback, useEffect, useMemo, useState } from "react";
import { API_URL, listFailures } from "../api";
import { CategoryBadge, CopyButton } from "../components";
import { ACTION_LABELS, CATEGORY_LABELS, formatAmount, formatTime } from "../format";
import type { Category, StoredFailure } from "../types";

const POLL_MS = 5000;

export function Dashboard() {
  const [failures, setFailures] = useState<StoredFailure[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Category | null>(null);

  const load = useCallback(async () => {
    try {
      setFailures(await listFailures());
      setError(null);
    } catch {
      setError(`Can't reach the backend at ${API_URL}. Is it running?`);
    }
  }, []);

  useEffect(() => {
    void load();
    const id = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(id);
  }, [load]);

  const counts = useMemo(() => {
    const result = new Map<Category, number>();
    for (const f of failures ?? []) {
      const c = f.explanation.category;
      result.set(c, (result.get(c) ?? 0) + 1);
    }
    return result;
  }, [failures]);

  const visible = (failures ?? []).filter((f) => !filter || f.explanation.category === filter);

  return (
    <div className="dashboard">
      <header className="page-header">
        <div>
          <h1>Failed payments</h1>
          <p className="muted">
            Every explanation is grounded in Stripe's decline-code documentation.
          </p>
        </div>
        <button type="button" className="button-secondary" onClick={() => void load()}>
          Refresh
        </button>
      </header>

      {error && <p className="notice notice-error">{error}</p>}

      {failures && failures.length > 0 && (
        <div className="filters" role="group" aria-label="Filter by category">
          <button type="button" className={`chip ${filter === null ? "chip-active" : ""}`}
            onClick={() => setFilter(null)}>
            All <span className="chip-count">{failures.length}</span>
          </button>
          {[...counts.entries()].map(([category, count]) => (
            <button type="button" key={category}
              className={`chip ${filter === category ? "chip-active" : ""}`}
              onClick={() => setFilter(filter === category ? null : category)}>
              {CATEGORY_LABELS[category]} <span className="chip-count">{count}</span>
            </button>
          ))}
        </div>
      )}

      {failures === null && !error && <p className="muted">Loading…</p>}

      {failures?.length === 0 && (
        <div className="card empty">
          <h2>No failed payments yet</h2>
          <p className="muted">
            Pay on the <a href="#/">store page</a> with a decline test card, or run{" "}
            <code>scripts/simulate_failures.py</code>.
          </p>
        </div>
      )}

      <ol className="failure-list">
        {visible.map((f) => <FailureCard key={f.id} item={f} />)}
      </ol>
    </div>
  );
}

function FailureCard({ item }: { item: StoredFailure }) {
  const { failure, explanation } = item;
  const code = explanation.source_code ?? "no code";
  return (
    <li className="card failure">
      <div className="failure-head">
        <span className="amount">{formatAmount(failure.amount, failure.currency)}</span>
        <code className="code">{code}</code>
        <CategoryBadge category={explanation.category} />
        <time className="muted time" dateTime={item.created_at}>
          {formatTime(item.created_at)}
        </time>
      </div>

      <p className="explanation">{explanation.merchant_explanation}</p>

      <div className="action-row">
        <span className="label">Next step</span>
        <strong>{ACTION_LABELS[explanation.recommended_action]}</strong>
        {explanation.recommended_action !== "do_not_retry" && (
          <span className={`retry ${explanation.retry_safe ? "retry-yes" : "retry-no"}`}>
            {explanation.retry_safe ? "Safe to retry" : "Don't retry as-is"}
          </span>
        )}
      </div>

      <div className="customer-message">
        <div>
          <span className="label">Message for the customer</span>
          <p>{explanation.customer_message}</p>
        </div>
        <CopyButton text={explanation.customer_message} />
      </div>

      {explanation.flags.length > 0 && (
        <p className="notice notice-warning">Guardrails: {explanation.flags.join(", ")}</p>
      )}

      <details>
        <summary>Details</summary>
        <dl className="details">
          <dt>Card</dt>
          <dd>{[failure.card_brand, failure.card_country].filter(Boolean).join(" · ") || "—"}</dd>
          <dt>Stripe codes</dt>
          <dd>
            <code>{failure.error_code ?? "—"}</code> / <code>{failure.decline_code ?? "—"}</code>
            {failure.advice_code && <> · advice <code>{failure.advice_code}</code></>}
          </dd>
          <dt>Explained by</dt>
          <dd>
            {explanation.engine === "rules" ? "Keyword rules" : explanation.engine}
            {explanation.basis.length > 0 && (
              <ul className="basis">{explanation.basis.map((b) => <li key={b}>{b}</li>)}</ul>
            )}
          </dd>
          <dt>Links</dt>
          <dd>
            {explanation.doc_url && (
              <a href={explanation.doc_url} target="_blank" rel="noreferrer">Stripe docs</a>
            )}
            {" · "}
            <a href={`https://dashboard.stripe.com/test/payments/${item.payment_intent_id}`}
              target="_blank" rel="noreferrer">View in Stripe</a>
          </dd>
        </dl>
      </details>
    </li>
  );
}
