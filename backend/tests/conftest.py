from collections.abc import Callable

import pytest

from app.kb import KBEntry, KnowledgeBase, get_kb
from app.schemas import Action, Category, LLMExplanation, LLMFailureView


@pytest.fixture(scope="session")
def kb() -> KnowledgeBase:
    return get_kb()


def make_output(**overrides: object) -> LLMExplanation:
    base: dict[str, object] = {
        "category": Category.INSUFFICIENT_FUNDS,
        "merchant_explanation": "The card doesn't have enough funds. Ask for another card.",
        "recommended_action": Action.ASK_NEW_PAYMENT_METHOD,
        "customer_message": "Your payment didn't go through. Could you try another card?",
        "retry_safe": False,
        "source_code": "insufficient_funds",
    }
    base.update(overrides)
    return LLMExplanation.model_validate(base)


class FakeLLM:
    """Records what it was sent and returns a canned (or per-call computed) answer."""

    def __init__(
        self, answer: LLMExplanation | Callable[[str], LLMExplanation] | Exception
    ) -> None:
        self.answer = answer
        self.calls: list[tuple[LLMFailureView, KBEntry, str]] = []

    def explain(self, view: LLMFailureView, entry: KBEntry, key: str) -> LLMExplanation:
        self.calls.append((view, entry, key))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer(key) if callable(self.answer) else self.answer
