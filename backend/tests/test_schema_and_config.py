import pytest
from pydantic import ValidationError

from app.config import Settings
from app.schemas import Explanation, LLMExplanation
from tests.conftest import make_output


def test_llm_output_rejects_unknown_enum_values() -> None:
    with pytest.raises(ValidationError):
        make_output(category="probably_fraud")
    with pytest.raises(ValidationError):
        make_output(recommended_action="call_the_police")


def test_llm_output_requires_every_field() -> None:
    data = make_output().model_dump()
    del data["retry_safe"]
    with pytest.raises(ValidationError):
        LLMExplanation.model_validate(data)


def test_llm_output_schema_lists_enums_for_structured_output() -> None:
    schema = LLMExplanation.model_json_schema()
    enums = {name: d.get("enum") for name, d in schema["$defs"].items()}
    assert enums["Category"] == [
        "insufficient_funds", "card_issue", "fraud_suspected",
        "authentication_required", "processing_error", "other",
    ]  # fmt: skip
    assert "request_3ds" in enums["Action"]


def test_explanation_serialises_enums_as_strings() -> None:
    exp = Explanation(**make_output().model_dump(), grounded=True, engine="rules")
    assert exp.model_dump(mode="json")["category"] == "insufficient_funds"


@pytest.mark.parametrize("key", ["sk_live_abc", "rk_live_abc"])
def test_live_secret_keys_are_refused(key: str) -> None:
    with pytest.raises(ValidationError, match="test mode only"):
        Settings(stripe_secret_key=key, _env_file=None)


def test_live_publishable_key_is_refused() -> None:
    with pytest.raises(ValidationError):
        Settings(stripe_publishable_key="pk_live_abc", _env_file=None)


def test_test_keys_are_accepted() -> None:
    s = Settings(stripe_secret_key="sk_test_abc", _env_file=None)
    assert s.stripe_secret_key == "sk_test_abc"
