"""Deterministic checks applied to every model output, plus the no-LLM fallback responses.

Rules (each intervention is recorded in `Explanation.flags`):
  - `source_code` must equal the code we looked up; otherwise it is overwritten.
  - No decline/error code other than the input's may appear in the text fields.
  - `merchant_explanation` is capped at 2 sentences.
  - Concealed (fraud-related) codes always get the fixed generic customer message and
    `retry_safe=False` — Stripe: "present it in the same manner as `generic_decline`".
  - Any customer message containing fraud wording is replaced by the generic message.
  - A runtime `advice_code=do_not_try_again` from Stripe forces `retry_safe=False`, and turns
    a "retry later" action into "contact issuer".
"""

import re
from typing import Literal

from app.kb import KBEntry
from app.schemas import Action, Category, Explanation, FailurePayload, LLMExplanation

# Wording of generic_decline's next steps in Stripe's docs, phrased for the buyer.
GENERIC_CUSTOMER_MESSAGE = (
    "Unfortunately, your payment was declined by your card issuer. Please contact your card "
    "issuer for more information, or try a different payment method."
)

FRAUD_TERMS = re.compile(
    r"\b(fraud\w*|stolen|steal\w*|theft|lost|black ?list\w*|block ?list\w*|suspicious|"
    r"suspect\w*|radar|high[- ]risk|risk level|pick ?up)\b",
    re.IGNORECASE,
)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def mentioned_codes(text: str, known_codes: frozenset[str]) -> set[str]:
    words = set(re.findall(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+", text.lower()))
    return words & known_codes


def first_sentences(text: str, n: int) -> str:
    return " ".join(_SENTENCE_END.split(text.strip())[:n])


def _own_sentences(text: str, code: str) -> list[str]:
    """Docs sentences that don't name a different code (e.g. "Related to `other_code`.")."""
    return [
        s
        for s in _SENTENCE_END.split(text.strip())
        if all(c == code for c in re.findall(r"`([a-z0-9_]+)`", s))
    ]


def kb_merchant_text(entry: KBEntry) -> str:
    """Two-sentence merchant explanation built straight from the docs entry."""
    description = _own_sentences(entry.description, entry.code)[:1]
    next_step = _own_sentences(entry.next_steps, entry.code)[:1]
    return " ".join(description + next_step)


def apply(
    raw: LLMExplanation,
    failure: FailurePayload,
    key: str,
    entry: KBEntry,
    known_codes: frozenset[str],
    engine: Literal["rules", "claude"],
    basis: list[str] | None = None,
) -> Explanation:
    flags: list[str] = []
    out = raw.model_copy()
    allowed = {c for c in (failure.decline_code, failure.error_code) if c}

    if out.source_code != key:
        flags.append("source_code_mismatch")
        out.source_code = key

    if mentioned_codes(out.merchant_explanation, known_codes) - allowed:
        flags.append("merchant_explanation_foreign_code")
        out.merchant_explanation = kb_merchant_text(entry)

    if len(_SENTENCE_END.split(out.merchant_explanation.strip())) > 2:
        flags.append("merchant_explanation_truncated")
        out.merchant_explanation = first_sentences(out.merchant_explanation, 2)

    if entry.conceal_reason:
        if out.customer_message != GENERIC_CUSTOMER_MESSAGE:
            flags.append("customer_message_concealed")
            out.customer_message = GENERIC_CUSTOMER_MESSAGE
    elif FRAUD_TERMS.search(out.customer_message) or mentioned_codes(
        out.customer_message, known_codes
    ):
        flags.append("customer_message_replaced")
        out.customer_message = GENERIC_CUSTOMER_MESSAGE

    if out.retry_safe and entry.conceal_reason:
        flags.append("retry_safe_overridden")
        out.retry_safe = False

    # Stripe's runtime advice for *this* charge beats the code's general guidance: "you
    # shouldn't use it again for the same transaction" (docs.stripe.com/declines/card).
    # Flagged separately: this is new information from Stripe, not a mistake being corrected.
    if failure.advice_code == "do_not_try_again" and (
        out.retry_safe or out.recommended_action is Action.RETRY_LATER
    ):
        flags.append("advice_code_do_not_try_again")
        out.retry_safe = False
        if out.recommended_action is Action.RETRY_LATER:
            out.recommended_action = Action.CONTACT_ISSUER
            out.customer_message = GENERIC_CUSTOMER_MESSAGE

    return Explanation(
        **out.model_dump(),
        grounded=True,
        engine=engine,
        basis=basis or [],
        flags=flags,
        doc_url=entry.doc_url,
    )


def fallback_unknown(key: str | None) -> Explanation:
    """Safe response when there is no code, or the code is not in the knowledge base."""
    if key is None:
        text = (
            "Stripe did not return a decline or error code for this payment, so it can't be "
            "explained automatically. Check the payment in the Stripe Dashboard."
        )
        flag = "missing_code"
    else:
        text = (
            f"Stripe returned `{key}`, which isn't in this app's decline-code knowledge base, so "
            "no explanation was generated. Check the payment in the Stripe Dashboard."
        )
        flag = "unknown_code"
    return Explanation(
        category=Category.OTHER,
        merchant_explanation=text,
        recommended_action=Action.DO_NOT_RETRY,
        customer_message=GENERIC_CUSTOMER_MESSAGE,
        retry_safe=False,
        source_code=key,
        grounded=False,
        engine="none",
        flags=[flag],
    )
