import { useState } from "react";
import { startCheckout } from "../api";
import { CopyButton } from "../components";

// From docs.stripe.com/testing — each one makes Stripe Checkout decline the payment.
const TEST_CARDS = [
  { number: "4000 0000 0000 9995", outcome: "Insufficient funds" },
  { number: "4000 0000 0000 0002", outcome: "Generic decline" },
  { number: "4000 0000 0000 9987", outcome: "Lost card" },
  { number: "4000 0000 0000 0069", outcome: "Expired card" },
  { number: "4000 0000 0000 0127", outcome: "Incorrect CVC" },
  { number: "4100 0000 0000 0019", outcome: "Blocked by Radar" },
];

export function Store({ checkoutResult }: { checkoutResult: string | null }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function pay() {
    setLoading(true);
    setError(null);
    try {
      window.location.assign(await startCheckout());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start Checkout");
      setLoading(false);
    }
  }

  return (
    <div className="store">
      {checkoutResult === "success" && (
        <p className="notice notice-success">Payment succeeded. Try a decline card next.</p>
      )}
      {checkoutResult === "cancelled" && (
        <p className="notice">
          Checkout closed. Any declined attempts are already on the dashboard.
        </p>
      )}

      <section className="card product">
        <div className="product-image" aria-hidden="true">
          <svg viewBox="0 0 120 120" width="96" height="96">
            <path d="M30 45h60l-6 55H36z" fill="#635bff" opacity="0.9" />
            <path d="M45 45c0-12 6-20 15-20s15 8 15 20" fill="none" stroke="#0a2540"
              strokeWidth="5" strokeLinecap="round" />
          </svg>
        </div>
        <div>
          <p className="eyebrow">Test-mode store</p>
          <h1>Demo tote bag</h1>
          <p className="price">$20.00</p>
          <button type="button" className="button-primary" onClick={pay} disabled={loading}>
            {loading ? "Opening Checkout…" : "Pay with Stripe Checkout"}
          </button>
          {error && <p className="error">{error}</p>}
        </div>
      </section>

      <section className="card">
        <h2>Make it fail</h2>
        <p className="muted">
          This store runs in Stripe test mode, so no real money moves. At Checkout, use one of
          these test cards with any future expiry date, any CVC and any postal code. Each
          declined attempt appears on the dashboard within seconds.
        </p>
        <ul className="test-cards">
          {TEST_CARDS.map((card) => (
            <li key={card.number}>
              <code>{card.number}</code>
              <span className="muted">{card.outcome}</span>
              <CopyButton text={card.number.replaceAll(" ", "")} />
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
