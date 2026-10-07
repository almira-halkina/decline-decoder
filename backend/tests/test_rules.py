import pytest

from app import rules
from app.explainer import ExplainService
from app.guardrails import FRAUD_TERMS, GENERIC_CUSTOMER_MESSAGE, mentioned_codes
from app.kb import KnowledgeBase
from app.schemas import Action, Category, FailurePayload

# Expected labels for every code a Stripe test card can trigger, checked by hand against the
# "Next steps" column of docs.stripe.com/declines/codes.
EXPECTED = [
    ("generic_decline", Category.CARD_ISSUE, Action.CONTACT_ISSUER, False),
    ("insufficient_funds", Category.INSUFFICIENT_FUNDS, Action.ASK_NEW_PAYMENT_METHOD, False),
    ("lost_card", Category.FRAUD_SUSPECTED, Action.DO_NOT_RETRY, False),
    ("stolen_card", Category.FRAUD_SUSPECTED, Action.DO_NOT_RETRY, False),
    ("fraudulent", Category.FRAUD_SUSPECTED, Action.DO_NOT_RETRY, False),
    ("card_velocity_exceeded", Category.INSUFFICIENT_FUNDS, Action.CONTACT_ISSUER, False),
    ("expired_card", Category.CARD_ISSUE, Action.ASK_NEW_PAYMENT_METHOD, False),
    ("incorrect_cvc", Category.CARD_ISSUE, Action.ASK_NEW_PAYMENT_METHOD, False),
    ("incorrect_number", Category.CARD_ISSUE, Action.ASK_NEW_PAYMENT_METHOD, False),
    ("processing_error", Category.PROCESSING_ERROR, Action.RETRY_LATER, True),
    ("authentication_required", Category.AUTHENTICATION_REQUIRED, Action.REQUEST_3DS, False),
    ("issuer_not_available", Category.PROCESSING_ERROR, Action.RETRY_LATER, True),
    ("duplicate_transaction", Category.OTHER, Action.CHECK_INTEGRATION, False),
    ("testmode_decline", Category.OTHER, Action.CHECK_INTEGRATION, False),
    ("pickup_card", Category.FRAUD_SUSPECTED, Action.DO_NOT_RETRY, False),
]


@pytest.mark.parametrize(("code", "category", "action", "retry_safe"), EXPECTED)
def test_rules_label_test_card_codes(
    kb: KnowledgeBase, code: str, category: Category, action: Action, retry_safe: bool
) -> None:
    entry = kb.get(code)
    assert entry is not None
    out, basis = rules.explain(entry, code)
    assert (out.category, out.recommended_action, out.retry_safe) == (category, action, retry_safe)
    assert out.source_code == code
    assert len(basis) == 3


def test_every_code_passes_guardrails_untouched(kb: KnowledgeBase) -> None:
    """The rules engine must never need a guardrail correction, for any code in the KB."""
    service = ExplainService(kb, llm=None)
    for code in sorted(kb.codes):
        out = service.explain(FailurePayload(decline_code=code))
        assert out.flags == [], code
        assert out.engine == "rules"
        assert out.grounded is True
        assert out.source_code == code
        assert not FRAUD_TERMS.search(out.customer_message), code
        assert not mentioned_codes(out.customer_message, kb.codes), code


def test_basis_explains_which_rule_fired(kb: KnowledgeBase) -> None:
    entry = kb.get("insufficient_funds")
    assert entry is not None
    _, basis = rules.explain(entry, "insufficient_funds")
    assert basis[0] == "category=insufficient_funds: mentions funds, balance or credit limit"


@pytest.mark.parametrize(
    ("code", "detail"),
    [("incorrect_cvc", "security code (CVC)"), ("invalid_expiry_month", "expiry date"),
     ("incorrect_zip", "billing postal code"), ("incorrect_number", "card number")],
)  # fmt: skip
def test_customer_message_names_the_detail_to_fix(
    kb: KnowledgeBase, code: str, detail: str
) -> None:
    entry = kb.get(code)
    assert entry is not None
    out, _ = rules.explain(entry, code)
    assert detail in out.customer_message


def test_contact_issuer_codes_use_generic_message(kb: KnowledgeBase) -> None:
    entry = kb.get("do_not_honor")
    assert entry is not None
    assert rules.explain(entry, "do_not_honor")[0].customer_message == GENERIC_CUSTOMER_MESSAGE
