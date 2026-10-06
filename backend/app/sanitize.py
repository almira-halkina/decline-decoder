"""Builds the only payload the LLM is allowed to see."""

import re

from app.schemas import FailurePayload, LLMFailureView

# Defence in depth: Stripe's messages don't contain these, but a hand-made /explain request might.
_CARD_NUMBER = re.compile(r"\b\d(?:[ -]?\d){11,18}\b")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def scrub(text: str | None) -> str | None:
    if text is None:
        return None
    return _EMAIL.sub("[redacted-email]", _CARD_NUMBER.sub("[redacted-number]", text))


def to_llm_view(failure: FailurePayload) -> LLMFailureView:
    return LLMFailureView(
        decline_code=failure.decline_code,
        error_code=failure.error_code,
        message=scrub(failure.message),
        payment_method_type=failure.payment_method_type,
        card_brand=failure.card_brand,
        card_country=failure.card_country,
        amount=failure.amount,
        currency=failure.currency,
    )
