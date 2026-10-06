import pytest

from app.kb import KnowledgeBase, lookup_key
from app.schemas import FailurePayload

# Every code a Stripe test card can trigger (docs.stripe.com/testing), as decline_code or code.
TEST_CARD_CODES = [
    "generic_decline",
    "insufficient_funds",
    "lost_card",
    "stolen_card",
    "card_velocity_exceeded",
    "fraudulent",
    "expired_card",
    "incorrect_cvc",
    "processing_error",
    "incorrect_number",
    "authentication_required",
]


@pytest.mark.parametrize("code", TEST_CARD_CODES)
def test_every_test_card_code_is_in_kb(kb: KnowledgeBase, code: str) -> None:
    entry = kb.get(code)
    assert entry is not None
    assert entry.description and entry.next_steps


def test_kb_has_full_card_table(kb: KnowledgeBase) -> None:
    assert len(kb.codes) == 50


def test_no_markdown_leaks_into_kb_text(kb: KnowledgeBase) -> None:
    for code in kb.codes:
        entry = kb.get(code)
        assert entry is not None
        for text in (entry.description, entry.next_steps):
            assert "](" not in text, code  # markdown link
            assert "*" not in text, code  # glossary emphasis


def test_docs_conceal_codes_match_stripe_guidance(kb: KnowledgeBase) -> None:
    by_basis = {
        c: e.conceal_basis for c in kb.codes if (e := kb.get(c)) is not None and e.conceal_reason
    }
    assert by_basis == {
        "fraudulent": "docs",
        "lost_card": "docs",
        "merchant_blacklist": "docs",
        "stolen_card": "docs",
        "pickup_card": "project_policy",
        "restricted_card": "project_policy",
    }


@pytest.mark.parametrize(
    ("code", "policy"),
    [
        ("processing_error", "same_details"),
        ("issuer_not_available", "same_details"),
        ("incorrect_cvc", "after_customer_action"),
        ("authentication_required", "after_customer_action"),
        ("insufficient_funds", "no"),
        ("generic_decline", "no"),
        ("stolen_card", "no"),
    ],
)
def test_retry_policy(kb: KnowledgeBase, code: str, policy: str) -> None:
    entry = kb.get(code)
    assert entry is not None and entry.retry_policy == policy


def test_unknown_and_missing_codes(kb: KnowledgeBase) -> None:
    assert kb.get("totally_made_up") is None
    assert kb.get(None) is None
    assert kb.get("") is None


def test_lookup_prefers_decline_code_then_error_code() -> None:
    both = FailurePayload(decline_code="insufficient_funds", error_code="card_declined")
    assert lookup_key(both) == "insufficient_funds"
    assert lookup_key(FailurePayload(error_code="expired_card")) == "expired_card"
    assert lookup_key(FailurePayload()) is None
