"""The AI layer: one structured-output call to Claude, wrapped in lookup + guardrails."""

import json
import logging
from typing import Protocol

import anthropic
from anthropic.types import OutputConfigParam

from app import guardrails, rules
from app.config import Effort
from app.kb import KBEntry, KnowledgeBase, lookup_key
from app.sanitize import to_llm_view
from app.schemas import Explanation, FailurePayload, LLMExplanation, LLMFailureView

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You explain failed Stripe card payments to merchants. You receive a payment failure from \
Stripe's API and the matching entry from Stripe's official decline-code documentation. Base \
every statement on that documentation entry and do not add causes it does not support.

Fill in the fields as follows.

category (pick the closest):
- insufficient_funds: the card's balance, credit limit, or spending/withdrawal limits.
- card_issue: a problem with the card or the details entered (expired, wrong number, CVC, \
postal code or address, card not supported), or the issuer declined without giving a reason.
- fraud_suspected: Stripe or the issuer suspects fraud, or the card is lost, stolen, \
blocked, or restricted.
- authentication_required: 3D Secure / strong customer authentication or a PIN is needed.
- processing_error: a temporary processing, network, or issuer-availability problem.
- other: anything else, such as duplicates, invalid amounts, or test-mode issues.

recommended_action (what the merchant should do next):
- retry_later: retry the same payment later, unchanged.
- ask_new_payment_method: ask the customer to re-enter correct details or use another card or \
payment method.
- request_3ds: have the customer complete authentication (3D Secure / SCA), then retry.
- contact_issuer: the customer needs to contact their card issuer.
- do_not_retry: do not retry with this card.
- check_integration: the merchant should check their own integration or records.

retry_safe: true only if re-attempting the same payment with exactly the same details can \
reasonably succeed. False if the customer must change something, authenticate, use another \
card, or contact their issuer.

merchant_explanation: at most 2 plain-language sentences for the merchant.

customer_message: a polite message of 1-3 sentences the merchant can send the buyer. Never \
include codes, and never mention fraud, theft, loss, block lists, or risk. If \
`do_not_reveal_reason_to_customer` is true, write a generic decline message instead.

source_code: copy `lookup_code` exactly.

Never mention any decline or error code other than `lookup_code`."""


class ExplainerError(Exception):
    """The model did not return a usable structured answer."""


class LLMClient(Protocol):
    def explain(self, view: LLMFailureView, entry: KBEntry, key: str) -> LLMExplanation: ...


def build_user_message(view: LLMFailureView, entry: KBEntry, key: str) -> str:
    return json.dumps(
        {
            "lookup_code": key,
            "failure": view.model_dump(),
            "stripe_documentation": {
                "code": entry.code,
                "description": entry.description,
                "next_steps": entry.next_steps,
                "deprecated": entry.deprecated,
                "do_not_reveal_reason_to_customer": entry.conceal_reason,
            },
        },
        indent=2,
    )


class ClaudeExplainer:
    def __init__(self, client: anthropic.Anthropic, model: str, effort: Effort) -> None:
        self._client = client
        self.model = model
        self._effort = effort

    def explain(self, view: LLMFailureView, entry: KBEntry, key: str) -> LLMExplanation:
        output_config: OutputConfigParam = {"effort": self._effort}
        response = self._client.messages.parse(
            model=self.model,
            max_tokens=4000,
            system=SYSTEM_PROMPT,
            output_config=output_config,
            messages=[{"role": "user", "content": build_user_message(view, entry, key)}],
            output_format=LLMExplanation,
        )
        if response.stop_reason != "end_turn" or response.parsed_output is None:
            raise ExplainerError(f"stop_reason={response.stop_reason}")
        return response.parsed_output


class ExplainService:
    def __init__(self, kb: KnowledgeBase, llm: LLMClient | None) -> None:
        self._kb = kb
        self._llm = llm

    def explain(self, failure: FailurePayload) -> Explanation:
        return self.explain_with_raw(failure)[0]

    def explain_with_raw(
        self, failure: FailurePayload
    ) -> tuple[Explanation, LLMExplanation | None]:
        """Returns the final explanation and the raw model output (None if no model ran)."""
        key = lookup_key(failure)
        entry = self._kb.get(key)
        if key is None or entry is None:
            return guardrails.fallback_unknown(key), None
        if self._llm is not None:
            try:
                raw = self._llm.explain(to_llm_view(failure), entry, key)
            except (ExplainerError, anthropic.APIError) as exc:
                log.warning("LLM explanation failed for %s, using rules: %s", key, exc)
            else:
                return guardrails.apply(raw, failure, key, entry, self._kb.codes, "claude"), raw
            result = self._explain_with_rules(failure, key, entry)
            result.flags.append("llm_error")
            return result, None
        return self._explain_with_rules(failure, key, entry), None

    def _explain_with_rules(self, failure: FailurePayload, key: str, entry: KBEntry) -> Explanation:
        # Rules output still goes through the guardrails, which also covers the runtime
        # advice_code check (e.g. Stripe saying do_not_try_again for this particular charge).
        explanation, basis = rules.explain(entry, key)
        return guardrails.apply(explanation, failure, key, entry, self._kb.codes, "rules", basis)
