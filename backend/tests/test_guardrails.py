import pytest

from app import guardrails
from app.guardrails import GENERIC_CUSTOMER_MESSAGE
from app.kb import KnowledgeBase
from app.schemas import Action, Category, Explanation, FailurePayload
from tests.conftest import make_output


def run(kb: KnowledgeBase, failure: FailurePayload, **overrides: object) -> Explanation:
    key = failure.decline_code or failure.error_code
    assert key is not None
    entry = kb.get(key)
    assert entry is not None
    return guardrails.apply(make_output(**overrides), failure, key, entry, kb.codes)


def test_clean_output_passes_unchanged(kb: KnowledgeBase) -> None:
    out = run(kb, FailurePayload(decline_code="insufficient_funds"))
    assert out.flags == []
    assert out.grounded is True
    assert out.customer_message == make_output().customer_message
    assert out.doc_url == "https://docs.stripe.com/declines/codes#insufficient_funds"


def test_source_code_mismatch_is_overwritten(kb: KnowledgeBase) -> None:
    out = run(kb, FailurePayload(decline_code="insufficient_funds"), source_code="do_not_honor")
    assert out.source_code == "insufficient_funds"
    assert "source_code_mismatch" in out.flags


def test_foreign_code_in_merchant_text_is_replaced_with_docs_text(kb: KnowledgeBase) -> None:
    out = run(
        kb,
        FailurePayload(decline_code="insufficient_funds"),
        merchant_explanation="This looks like do_not_honor.",
    )
    assert "do_not_honor" not in out.merchant_explanation
    assert out.merchant_explanation.startswith("The card has insufficient funds")
    assert "merchant_explanation_foreign_code" in out.flags


def test_input_codes_may_be_mentioned(kb: KnowledgeBase) -> None:
    out = run(
        kb,
        FailurePayload(decline_code="insufficient_funds", error_code="card_declined"),
        merchant_explanation="Stripe returned insufficient_funds for this card.",
    )
    assert out.flags == []


def test_merchant_explanation_capped_at_two_sentences(kb: KnowledgeBase) -> None:
    out = run(
        kb,
        FailurePayload(decline_code="insufficient_funds"),
        merchant_explanation="One. Two! Three? Four.",
    )
    assert out.merchant_explanation == "One. Two!"
    assert "merchant_explanation_truncated" in out.flags


@pytest.mark.parametrize(
    "code", ["fraudulent", "stolen_card", "lost_card", "merchant_blacklist", "pickup_card"]
)
def test_concealed_codes_get_generic_message_and_no_retry(kb: KnowledgeBase, code: str) -> None:
    out = run(
        kb,
        FailurePayload(decline_code=code, error_code="card_declined"),
        source_code=code,
        category=Category.FRAUD_SUSPECTED,
        customer_message="Your card was reported stolen.",
        retry_safe=True,
    )
    assert out.customer_message == GENERIC_CUSTOMER_MESSAGE
    assert out.retry_safe is False
    assert {"customer_message_concealed", "retry_safe_overridden"} <= set(out.flags)


@pytest.mark.parametrize(
    "message",
    [
        "We suspect fraud on this card.",
        "This payment was flagged as high-risk.",
        "Your bank says the card is lost.",
        "Please retry; the error was generic_decline.",
    ],
)
def test_fraud_wording_or_codes_never_reach_customer(kb: KnowledgeBase, message: str) -> None:
    out = run(
        kb,
        FailurePayload(decline_code="generic_decline"),
        customer_message=message,
        source_code="generic_decline",
    )
    assert out.customer_message == GENERIC_CUSTOMER_MESSAGE
    assert "customer_message_replaced" in out.flags


def test_advice_code_do_not_try_again_forces_no_retry(kb: KnowledgeBase) -> None:
    out = run(
        kb,
        FailurePayload(decline_code="processing_error", advice_code="do_not_try_again"),
        source_code="processing_error",
        retry_safe=True,
        recommended_action=Action.RETRY_LATER,
    )
    assert out.retry_safe is False
    assert "retry_safe_overridden" in out.flags


def test_fallback_unknown_code() -> None:
    out = guardrails.fallback_unknown("brand_new_code")
    assert out.grounded is False
    assert out.source_code == "brand_new_code"
    assert out.recommended_action is Action.DO_NOT_RETRY
    assert out.retry_safe is False
    assert out.flags == ["unknown_code"]


def test_fallback_missing_code() -> None:
    out = guardrails.fallback_unknown(None)
    assert out.source_code is None
    assert out.flags == ["missing_code"]


def test_fallback_from_kb_is_grounded_and_safe(kb: KnowledgeBase) -> None:
    entry = kb.get("stolen_card")
    assert entry is not None
    out = guardrails.fallback_from_kb("stolen_card", entry, "llm_unavailable")
    assert out.category is Category.FRAUD_SUSPECTED
    assert out.recommended_action is Action.DO_NOT_RETRY
    assert out.customer_message == GENERIC_CUSTOMER_MESSAGE
    assert out.grounded is True
