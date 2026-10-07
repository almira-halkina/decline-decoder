"""Create real failed test-mode payments with Stripe's official test cards.

Run (with the backend and `stripe listen` running, see README):
    uv run --project backend python scripts/simulate_failures.py

Each scenario confirms a PaymentIntent that Stripe's testing docs say will fail
(https://docs.stripe.com/testing#declined-payments). Stripe then sends a
`payment_intent.payment_failed` webhook, which the backend explains and stores.

The script prints the code and decline_code Stripe actually returned next to what the docs
say, so any difference between the docs and the real API shows up immediately.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import stripe
from stripe.params import PaymentIntentCreateParams

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.config import get_settings


@dataclass(frozen=True)
class Scenario:
    name: str
    expected_code: str
    expected_decline: str | None  # None = docs say "n/a"; "?" = docs don't say
    payment_method: str | None = None  # a pm_card_* test PaymentMethod
    token: str | None = None  # a tok_* test token (some declines exist only as tokens)
    off_session: bool = False  # attach to a Customer and charge without the buyer present


SCENARIOS = [
    Scenario("Generic decline", "card_declined", "generic_decline", "pm_card_visa_chargeDeclined"),
    Scenario("Insufficient funds", "card_declined", "insufficient_funds",
             "pm_card_visa_chargeDeclinedInsufficientFunds"),
    Scenario("Lost card", "card_declined", "lost_card", "pm_card_visa_chargeDeclinedLostCard"),
    Scenario("Stolen card", "card_declined", "stolen_card",
             "pm_card_visa_chargeDeclinedStolenCard"),
    Scenario("Velocity limit exceeded", "card_declined", "card_velocity_exceeded",
             "pm_card_visa_chargeDeclinedVelocityLimitExceeded"),
    Scenario("Expired card", "expired_card", None, "pm_card_chargeDeclinedExpiredCard"),
    Scenario("Incorrect CVC", "incorrect_cvc", None, "pm_card_chargeDeclinedIncorrectCvc"),
    Scenario("Processing error", "processing_error", None,
             "pm_card_chargeDeclinedProcessingError"),
    Scenario("Fraudulent (token only)", "card_declined", "fraudulent",
             token="tok_visa_chargeDeclinedFraudulent"),  # noqa: S106 (public test token)
    Scenario("Radar: always blocked", "card_declined", "?", "pm_card_radarBlock"),
    Scenario("3DS required, charged off-session", "authentication_required", "?",
             "pm_card_authenticationRequired", off_session=True),
    Scenario("Decline after attaching", "card_declined", "?", "pm_card_chargeCustomerFail",
             off_session=True),
]  # fmt: skip


def run(client: stripe.StripeClient, scenario: Scenario) -> tuple[str, str | None, str | None]:
    """Returns (error code, decline_code, PaymentIntent id) of the failed attempt."""
    params: PaymentIntentCreateParams = {
        "amount": 2000,
        "currency": "usd",
        # Cards only, never a redirect: the payment fails or succeeds right here.
        "automatic_payment_methods": {"enabled": True, "allow_redirects": "never"},
        "confirm": True,
        "description": f"Decline Decoder simulation: {scenario.name}",
        "metadata": {"simulation": scenario.name},
    }
    if scenario.token:
        # The typings omit `card`, but the API accepts a card token here (verified 2026-10-07).
        params["payment_method_data"] = {
            "type": "card",
            "card": {"token": scenario.token},  # type: ignore[typeddict-unknown-key]
        }
    elif scenario.payment_method:
        params["payment_method"] = scenario.payment_method
    if scenario.off_session:
        customer = client.v1.customers.create(params={"description": "Decline Decoder simulation"})
        client.v1.payment_methods.attach(
            str(scenario.payment_method), params={"customer": customer.id}
        )
        params["customer"] = customer.id
        params["off_session"] = True

    try:
        pi = client.v1.payment_intents.create(params=params)
    except stripe.CardError as exc:
        error = exc.error
        pi_id = error.payment_intent.id if error and error.payment_intent else None
        return str(exc.code), error.decline_code if error else None, pi_id
    return f"no error (status={pi.status})", None, pi.id


def main() -> None:
    settings = get_settings()
    if not settings.stripe_secret_key.startswith("sk_test_"):
        sys.exit("STRIPE_SECRET_KEY must be a test-mode key (sk_test_...).")
    client = stripe.StripeClient(settings.stripe_secret_key)

    print(f"{'Scenario':36} {'code':24} {'decline_code':24} {'matches docs':12} payment_intent")
    mismatches = 0
    for scenario in SCENARIOS:
        code, decline, pi_id = run(client, scenario)
        expected_decline = scenario.expected_decline
        if expected_decline == "?":
            verdict = "docs silent"
        elif code == scenario.expected_code and decline == expected_decline:
            verdict = "yes"
        elif code == scenario.expected_code and expected_decline is None and decline == code:
            # Observed 2026-10-07: docs say "n/a", but the API also sets decline_code = code.
            verdict = "yes*"
        else:
            verdict = "NO"
            mismatches += 1
        print(f"{scenario.name:36} {code:24} {decline or '-':24} {verdict:12} {pi_id or '-'}")

    print(f"\n{len(SCENARIOS)} scenarios run, {mismatches} differ from the docs.")
    print("Webhooks arrive within a few seconds; check GET /failures or the dashboard.")


if __name__ == "__main__":
    main()
