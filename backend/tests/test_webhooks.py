import hashlib
import hmac
import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.explainer import ExplainService
from app.kb import KnowledgeBase
from app.main import app, get_explain_service, get_repository
from app.repository import FailureRepository

SECRET = "whsec_test_secret"


def sign(payload: bytes, secret: str = SECRET, timestamp: int | None = None) -> str:
    """Builds a Stripe-Signature header the same way Stripe does."""
    t = int(time.time()) if timestamp is None else timestamp
    digest = hmac.new(secret.encode(), f"{t}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={t},v1={digest}"


def failed_event(
    decline_code: str | None = "insufficient_funds",
    code: str = "card_declined",
    event_id: str = "evt_1",
    **overrides: Any,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "id": event_id,
        "object": "event",
        "type": "payment_intent.payment_failed",
        "livemode": False,
        "created": 1_791_000_000,
        "data": {
            "object": {
                "id": "pi_123",
                "object": "payment_intent",
                "amount": 2000,
                "currency": "usd",
                "receipt_email": "buyer@example.com",
                "last_payment_error": {
                    "type": "card_error",
                    "code": code,
                    "decline_code": decline_code,
                    "advice_code": "try_again_later",
                    "message": "Your card has insufficient funds.",
                    "payment_method_type": "card",
                    "payment_method": {
                        "id": "pm_123",
                        "type": "card",
                        "billing_details": {"email": "buyer@example.com", "name": "Jane Doe"},
                        "card": {"brand": "visa", "country": "US", "last4": "9995"},
                    },
                },
            }
        },
    }
    event.update(overrides)
    return event


@pytest.fixture
def repo(tmp_path: Path) -> FailureRepository:
    return FailureRepository(str(tmp_path / "test.sqlite3"))


@pytest.fixture
def client(kb: KnowledgeBase, repo: FailureRepository) -> Iterator[TestClient]:
    app.dependency_overrides[get_settings] = lambda: Settings(
        stripe_webhook_secret=SECRET,
        _env_file=None,
    )
    app.dependency_overrides[get_explain_service] = lambda: ExplainService(kb, None)
    app.dependency_overrides[get_repository] = lambda: repo
    yield TestClient(app)
    app.dependency_overrides.clear()


def post_event(client: TestClient, event: dict[str, Any], header: str | None = None) -> Any:
    payload = json.dumps(event).encode()
    headers = {"Stripe-Signature": header if header is not None else sign(payload)}
    return client.post("/webhooks/stripe", content=payload, headers=headers)


def test_valid_failed_event_is_explained_and_stored(
    client: TestClient, repo: FailureRepository
) -> None:
    resp = post_event(client, failed_event())
    assert resp.json() == {"status": "stored"}

    [stored] = repo.list()
    assert stored.id == "evt_1"
    assert stored.payment_intent_id == "pi_123"
    assert stored.failure.decline_code == "insufficient_funds"
    assert stored.failure.card_brand == "visa"
    assert stored.failure.card_country == "US"
    assert stored.failure.advice_code == "try_again_later"
    assert stored.explanation.category == "insufficient_funds"


def test_identifying_data_in_event_is_not_stored(client: TestClient, tmp_path: Path) -> None:
    post_event(client, failed_event())
    raw = (tmp_path / "test.sqlite3").read_bytes()
    for secret in (b"buyer@example.com", b"Jane Doe", b"9995", b"pm_123"):
        assert secret not in raw


def test_error_code_only_failure(client: TestClient, repo: FailureRepository) -> None:
    post_event(client, failed_event(decline_code=None, code="expired_card"))
    [stored] = repo.list()
    assert stored.explanation.source_code == "expired_card"
    assert stored.explanation.grounded is True


@pytest.mark.parametrize(
    "header",
    [
        "",  # missing
        "t=1,v1=deadbeef",  # garbage
        sign(b"different payload"),  # signature for other content
        sign(json.dumps(failed_event()).encode(), secret="whsec_wrong"),  # wrong secret
    ],
)
def test_bad_signatures_are_rejected(
    client: TestClient, repo: FailureRepository, header: str
) -> None:
    resp = post_event(client, failed_event(), header=header)
    assert resp.status_code == 400
    assert repo.list() == []


def test_tampered_payload_is_rejected(client: TestClient, repo: FailureRepository) -> None:
    original = json.dumps(failed_event()).encode()
    tampered = original.replace(b"insufficient_funds", b"processing_error")
    resp = client.post(
        "/webhooks/stripe", content=tampered, headers={"Stripe-Signature": sign(original)}
    )
    assert resp.status_code == 400
    assert repo.list() == []


def test_stale_timestamp_is_rejected(client: TestClient) -> None:
    payload = json.dumps(failed_event()).encode()
    old = sign(payload, timestamp=int(time.time()) - 600)  # tolerance is 300 s
    assert post_event(client, failed_event(), header=old).status_code == 400


def test_redelivered_event_is_stored_once(client: TestClient, repo: FailureRepository) -> None:
    assert post_event(client, failed_event()).json() == {"status": "stored"}
    assert post_event(client, failed_event()).json() == {"status": "duplicate"}
    assert len(repo.list()) == 1


def test_other_event_types_are_ignored(client: TestClient, repo: FailureRepository) -> None:
    event = failed_event(type="payment_intent.succeeded")
    assert post_event(client, event).json() == {"status": "ignored"}
    assert repo.list() == []


def test_live_mode_events_are_ignored(client: TestClient, repo: FailureRepository) -> None:
    assert post_event(client, failed_event(livemode=True)).json() == {"status": "ignored"}
    assert repo.list() == []


def test_failures_endpoints(client: TestClient) -> None:
    post_event(client, failed_event(event_id="evt_a"))
    post_event(client, failed_event(event_id="evt_b", created=1_791_000_100))

    listed = client.get("/failures").json()
    assert [f["id"] for f in listed] == ["evt_b", "evt_a"]  # newest first

    one = client.get("/failures/evt_a").json()
    assert one["explanation"]["recommended_action"] == "ask_new_payment_method"
    assert client.get("/failures/evt_missing").status_code == 404
