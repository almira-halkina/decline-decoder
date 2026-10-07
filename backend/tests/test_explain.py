import json
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.explainer import ClaudeExplainer, ExplainerError, ExplainService
from app.kb import KnowledgeBase
from app.main import app, get_explain_service
from app.sanitize import scrub
from tests.conftest import FakeLLM, make_output

FULL_FAILURE = {
    "decline_code": "insufficient_funds",
    "error_code": "card_declined",
    "message": "Your card has insufficient funds.",
    "advice_code": "try_again_later",
    "payment_method_type": "card",
    "card_brand": "visa",
    "card_country": "US",
    "amount": 2000,
    "currency": "usd",
}


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM(make_output())


@pytest.fixture
def client(kb: KnowledgeBase, fake_llm: FakeLLM) -> Iterator[TestClient]:
    app.dependency_overrides[get_explain_service] = lambda: ExplainService(kb, fake_llm)
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_explain_returns_validated_explanation(client: TestClient) -> None:
    resp = client.post("/explain", json=FULL_FAILURE)
    assert resp.status_code == 200
    body = resp.json()
    assert body["category"] == "insufficient_funds"
    assert body["recommended_action"] == "ask_new_payment_method"
    assert body["source_code"] == "insufficient_funds"
    assert body["grounded"] is True
    assert body["engine"] == "claude"


def test_llm_sees_only_allowlisted_fields(client: TestClient, fake_llm: FakeLLM) -> None:
    client.post("/explain", json=FULL_FAILURE)
    view, entry, key = fake_llm.calls[0]
    assert set(view.model_dump()) == {
        "decline_code", "error_code", "message", "payment_method_type",
        "card_brand", "card_country", "amount", "currency",
    }  # fmt: skip
    assert key == entry.code == "insufficient_funds"


@pytest.mark.parametrize(
    "extra", [{"email": "jane@example.com"}, {"card_number": "4242424242424242"}, {"name": "J"}]
)
def test_identifying_fields_are_rejected(client: TestClient, extra: dict[str, str]) -> None:
    assert client.post("/explain", json={**FULL_FAILURE, **extra}).status_code == 422


def test_unknown_code_skips_llm(client: TestClient, fake_llm: FakeLLM) -> None:
    body = client.post("/explain", json={"decline_code": "not_a_real_code"}).json()
    assert body["flags"] == ["unknown_code"]
    assert body["grounded"] is False
    assert fake_llm.calls == []


def test_missing_code_skips_llm(client: TestClient, fake_llm: FakeLLM) -> None:
    body = client.post("/explain", json={"message": "Something failed."}).json()
    assert body["flags"] == ["missing_code"]
    assert fake_llm.calls == []


def test_error_code_only_failure_is_grounded(client: TestClient, fake_llm: FakeLLM) -> None:
    fake_llm.answer = make_output(source_code="expired_card")
    body = client.post("/explain", json={"error_code": "expired_card"}).json()
    assert body["grounded"] is True
    assert body["source_code"] == "expired_card"


def test_llm_failure_falls_back_to_docs(kb: KnowledgeBase) -> None:
    service = ExplainService(kb, FakeLLM(ExplainerError("stop_reason=refusal")))
    out = service.explain(FakeFailure.insufficient())
    assert out.flags == ["llm_error"]
    assert out.engine == "rules"
    assert out.grounded is True


def test_without_llm_the_rules_engine_answers(kb: KnowledgeBase) -> None:
    out = ExplainService(kb, None).explain(FakeFailure.insufficient())
    assert out.engine == "rules"
    assert out.flags == []
    assert out.category == "insufficient_funds"


def test_scrub_redacts_card_numbers_and_emails() -> None:
    text = "Card 4242 4242 4242 4242 for jane.doe+x@example.co.uk failed"
    assert scrub(text) == "Card [redacted-number] for [redacted-email] failed"


class FakeFailure:
    @staticmethod
    def insufficient() -> Any:
        from app.schemas import FailurePayload

        return FailurePayload(decline_code="insufficient_funds", error_code="card_declined")


class FakeAnthropic:
    """Stands in for anthropic.Anthropic; captures the request ClaudeExplainer sends."""

    def __init__(self, stop_reason: str = "end_turn") -> None:
        self.kwargs: dict[str, Any] = {}
        self.stop_reason = stop_reason
        self.messages = SimpleNamespace(parse=self._parse)

    def _parse(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        output = make_output() if self.stop_reason == "end_turn" else None
        return SimpleNamespace(stop_reason=self.stop_reason, parsed_output=output)


def test_claude_request_contains_no_pii_and_uses_schema(kb: KnowledgeBase) -> None:
    fake = FakeAnthropic()
    explainer = ClaudeExplainer(fake, "claude-opus-5-5", "low")  # type: ignore[arg-type]
    failure = FakeFailure.insufficient()
    failure.message = "Card 4000000000009995 owned by jane@example.com declined"
    ExplainService(kb, explainer).explain(failure)

    sent = json.loads(fake.kwargs["messages"][0]["content"])
    assert "4000000000009995" not in json.dumps(sent)
    assert "jane@example.com" not in json.dumps(sent)
    assert "advice_code" not in sent["failure"]
    assert sent["lookup_code"] == "insufficient_funds"
    assert sent["stripe_documentation"]["code"] == "insufficient_funds"
    assert fake.kwargs["output_format"].__name__ == "LLMExplanation"
    assert fake.kwargs["output_config"] == {"effort": "low"}


def test_claude_refusal_raises(kb: KnowledgeBase) -> None:
    explainer = ClaudeExplainer(FakeAnthropic("refusal"), "m", "low")  # type: ignore[arg-type]
    entry = kb.get("insufficient_funds")
    assert entry is not None
    from app.sanitize import to_llm_view

    with pytest.raises(ExplainerError):
        explainer.explain(to_llm_view(FakeFailure.insufficient()), entry, "insufficient_funds")
