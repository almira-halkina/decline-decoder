"""Knowledge-base lookup. The lookup happens here, in code — never by the model."""

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from app.schemas import FailurePayload

KB_PATH = Path(__file__).resolve().parents[1] / "data" / "decline_codes.json"


class KBEntry(BaseModel):
    code: str
    deprecated: bool
    description: str
    next_steps: str
    conceal_reason: bool
    conceal_basis: Literal["docs", "project_policy"] | None
    retry_policy: Literal["same_details", "after_customer_action", "no"]
    retry_basis: str
    doc_url: str


class KnowledgeBase:
    def __init__(self, entries: dict[str, KBEntry]) -> None:
        self._entries = entries

    @classmethod
    def load(cls, path: Path = KB_PATH) -> "KnowledgeBase":
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls({code: KBEntry.model_validate(e) for code, e in raw["codes"].items()})

    @property
    def codes(self) -> frozenset[str]:
        return frozenset(self._entries)

    def get(self, code: str | None) -> KBEntry | None:
        return self._entries.get(code) if code else None


def lookup_key(failure: FailurePayload) -> str | None:
    """`decline_code` when present, else the error `code`.

    Stripe sets only `code` for several failures (expired_card, incorrect_cvc,
    processing_error, incorrect_number); those names are also entries in the decline-code table.
    """
    return failure.decline_code or failure.error_code


@lru_cache
def get_kb() -> KnowledgeBase:
    return KnowledgeBase.load()
