"""Request/response models. The LLM never sees anything outside `LLMFailureView`."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Category(StrEnum):
    INSUFFICIENT_FUNDS = "insufficient_funds"
    CARD_ISSUE = "card_issue"
    FRAUD_SUSPECTED = "fraud_suspected"
    AUTHENTICATION_REQUIRED = "authentication_required"
    PROCESSING_ERROR = "processing_error"
    OTHER = "other"


class Action(StrEnum):
    RETRY_LATER = "retry_later"
    ASK_NEW_PAYMENT_METHOD = "ask_new_payment_method"
    REQUEST_3DS = "request_3ds"
    CONTACT_ISSUER = "contact_issuer"
    DO_NOT_RETRY = "do_not_retry"
    CHECK_INTEGRATION = "check_integration"


class FailurePayload(BaseModel):
    """A payment failure, as extracted from `PaymentIntent.last_payment_error`.

    Only non-identifying fields are accepted; `extra="forbid"` makes a stray email or card
    number in a request a 422 instead of something that could reach the LLM.
    """

    model_config = ConfigDict(extra="forbid")

    decline_code: str | None = Field(None, max_length=64)
    error_code: str | None = Field(None, max_length=64, description="last_payment_error.code")
    message: str | None = Field(None, max_length=500)
    advice_code: str | None = Field(
        None, max_length=64, description="Used by guardrails only; never sent to the LLM."
    )
    payment_method_type: str | None = Field(None, max_length=64)
    card_brand: str | None = Field(None, max_length=32)
    card_country: str | None = Field(None, max_length=2)
    amount: int | None = Field(None, ge=0, description="Smallest currency unit")
    currency: str | None = Field(None, max_length=3)


class LLMFailureView(BaseModel):
    """Exactly the fields the project brief allows the LLM to see."""

    decline_code: str | None
    error_code: str | None
    message: str | None
    payment_method_type: str | None
    card_brand: str | None
    card_country: str | None
    amount: int | None
    currency: str | None


class LLMExplanation(BaseModel):
    """Structured output requested from the model (constraints are enforced in guardrails)."""

    category: Category
    merchant_explanation: str = Field(description="At most 2 sentences, for the merchant.")
    recommended_action: Action
    customer_message: str = Field(description="Polite message the merchant can send the buyer.")
    retry_safe: bool = Field(
        description="True only if re-attempting the same payment with the same details may succeed."
    )
    source_code: str | None = Field(description="Echo of the code you were given, unchanged.")


class Explanation(LLMExplanation):
    """What the API returns: the validated explanation plus provenance."""

    grounded: bool = Field(description="True if a knowledge-base entry backed this explanation.")
    engine: Literal["rules", "claude", "none"] = Field(
        description="What produced the labels: keyword rules, Claude, or nothing (unknown code)."
    )
    basis: list[str] = Field(default_factory=list, description="Rules that fired (rules engine).")
    flags: list[str] = Field(default_factory=list, description="Guardrail interventions, if any.")
    doc_url: str | None = None
