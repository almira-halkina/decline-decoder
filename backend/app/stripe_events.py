"""Turns a `payment_intent.payment_failed` event into a FailurePayload.

Only the allowlisted fields are copied out of `last_payment_error`; billing details, emails and
card fingerprints in the event never leave this function.
"""

from datetime import UTC, datetime
from typing import Any

from app.schemas import FailurePayload


def failure_from_payment_intent(pi: dict[str, Any]) -> FailurePayload:
    error = pi.get("last_payment_error") or {}
    payment_method = error.get("payment_method") or {}
    card = payment_method.get("card") or {}
    return FailurePayload(
        decline_code=error.get("decline_code"),
        error_code=error.get("code"),
        message=error.get("message"),
        advice_code=error.get("advice_code"),
        payment_method_type=error.get("payment_method_type") or payment_method.get("type"),
        card_brand=card.get("brand"),
        card_country=card.get("country"),
        amount=pi.get("amount"),
        currency=pi.get("currency"),
    )


def failed_at(event: dict[str, Any]) -> datetime:
    return datetime.fromtimestamp(int(event["created"]), tz=UTC)
