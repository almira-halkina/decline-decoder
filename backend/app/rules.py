"""Rule-based explainer: classifies a decline by matching words in Stripe's own docs text.

Every rule reads the knowledge-base entry (code name, description, next steps — all verbatim
from docs.stripe.com/declines/codes) and the first matching rule wins. The rule that fired is
returned in `Explanation.basis`, so every label can be traced to a phrase in Stripe's docs.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

from app.guardrails import GENERIC_CUSTOMER_MESSAGE, kb_merchant_text
from app.kb import KBEntry
from app.schemas import Action, Category, LLMExplanation


@dataclass(frozen=True)
class Rule[T]:
    label: T
    why: str
    test: Callable[[KBEntry], bool]


def words(pattern: str) -> Callable[[KBEntry], bool]:
    """True if the pattern appears in the code name, description or next steps."""
    regex = re.compile(pattern, re.IGNORECASE)
    return lambda e: bool(
        regex.search(f"{e.code.replace('_', ' ')} {e.description} {e.next_steps}")
    )


def concealed(e: KBEntry) -> bool:
    return e.conceal_reason


def retry_policy(policy: str) -> Callable[[KBEntry], bool]:
    return lambda e: e.retry_policy == policy


CATEGORY_RULES: list[Rule[Category]] = [
    Rule(Category.FRAUD_SUSPECTED, "docs say not to reveal the reason (fraud-related)", concealed),
    Rule(Category.AUTHENTICATION_REQUIRED, "mentions authentication, 3D Secure or a PIN",
         words(r"authenticat|3d secure|\bpin\b")),
    Rule(Category.INSUFFICIENT_FUNDS, "mentions funds, balance or credit limit",
         words(r"insufficient funds|balance|credit limit")),
    Rule(Category.PROCESSING_ERROR, "next steps say to attempt the payment again",
         retry_policy("same_details")),
    Rule(Category.OTHER, "about duplicates, the amount or test cards",
         words(r"duplicate|identical amount|payment amount|test card")),
]  # fmt: skip
DEFAULT_CATEGORY = Rule(Category.CARD_ISSUE, "no specific rule matched: a card-level decline",
                        lambda e: True)  # fmt: skip

ACTION_RULES: list[Rule[Action]] = [
    Rule(Action.DO_NOT_RETRY, "fraud-related: don't retry this card", concealed),
    Rule(Action.RETRY_LATER, "next steps say to attempt the payment again",
         retry_policy("same_details")),
    Rule(Action.REQUEST_3DS, "next steps involve authentication / 3D Secure",
         words(r"authenticat|3d secure|\bsca\b")),
    Rule(Action.CHECK_INTEGRATION, "check for a recent duplicate or a test card",
         words(r"recent payment already exists|genuine card")),
    Rule(Action.ASK_NEW_PAYMENT_METHOD, "customer must re-enter details or use another card",
         words(r"try again using the correct|another card|alternative payment method|"
               r"another card or payment method|inserting their card")),
    Rule(Action.CONTACT_ISSUER, "next steps say to contact or check with the card issuer",
         words(r"contact their card issuer|check with (their card issuer|the issuer)")),
]  # fmt: skip
DEFAULT_ACTION = Rule(Action.DO_NOT_RETRY, "no specific rule matched", lambda e: True)


def first_match[T](rules: list[Rule[T]], default: Rule[T], entry: KBEntry) -> Rule[T]:
    return next((r for r in rules if r.test(entry)), default)


# Which detail to ask the customer to re-check, keyed on words in the code name.
_DETAIL_NAMES = [
    ("cvc", "security code (CVC)"),
    ("number", "card number"),
    ("expiry", "expiry date"),
    ("zip", "billing postal code"),
    ("address", "billing address"),
    ("pin", "PIN"),
]


def customer_message(entry: KBEntry, action: Action) -> str:
    if entry.conceal_reason or action in (Action.CONTACT_ISSUER, Action.DO_NOT_RETRY):
        return GENERIC_CUSTOMER_MESSAGE
    if action is Action.RETRY_LATER:
        return (
            "Something went wrong while processing your payment. Please try again in a few minutes."
        )
    if action is Action.REQUEST_3DS:
        return (
            "Your bank needs you to confirm this payment. Please try again and complete the "
            "verification step from your bank."
        )
    if action is Action.CHECK_INTEGRATION:
        return "We couldn't complete your payment. Please try again later or contact us for help."
    if entry.code == "expired_card":
        return "Your card has expired. Please try again with a different card."
    if entry.code.endswith("pin_required"):  # in-person (Terminal) payments only
        return "Please insert your card and enter your PIN to complete the payment."
    if entry.code.startswith(("incorrect_", "invalid_")):
        for word, name in _DETAIL_NAMES:
            if word in entry.code:
                return (
                    f"Some of your card details don't look right. Please check your {name} and "
                    "try again, or use a different card."
                )
    return (
        "Your payment couldn't be completed with this card. "
        "Please try again with a different card or payment method."
    )


def explain(entry: KBEntry, key: str) -> tuple[LLMExplanation, list[str]]:
    """Returns the explanation and the rules that produced it."""
    category = first_match(CATEGORY_RULES, DEFAULT_CATEGORY, entry)
    action = first_match(ACTION_RULES, DEFAULT_ACTION, entry)
    explanation = LLMExplanation(
        category=category.label,
        merchant_explanation=kb_merchant_text(entry),
        recommended_action=action.label,
        customer_message=customer_message(entry, action.label),
        retry_safe=entry.retry_policy == "same_details",
        source_code=key,
    )
    basis = [
        f"category={category.label.value}: {category.why}",
        f"action={action.label.value}: {action.why}",
        f"retry_safe: docs retry wording '{entry.retry_basis}'",
    ]
    return explanation, basis
