import json
import logging
import time
from functools import lru_cache
from typing import Annotated, Any

import anthropic
import stripe
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings, get_settings
from app.explainer import ClaudeExplainer, ExplainService
from app.kb import get_kb
from app.repository import FailureRepository, StoredFailure
from app.schemas import Explanation, FailurePayload
from app.stripe_events import failed_at, failure_from_payment_intent

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("decline_decoder")

DEMO_PRODUCT_NAME = "Decline Decoder demo tote bag"
DEMO_AMOUNT = 2000  # cents
DEMO_CURRENCY = "usd"


@lru_cache
def get_explain_service() -> ExplainService:
    settings = get_settings()
    llm = None
    if settings.explainer_engine == "claude":
        if not settings.anthropic_api_key:
            raise RuntimeError("EXPLAINER_ENGINE=claude requires ANTHROPIC_API_KEY")
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=30.0)
        llm = ClaudeExplainer(client, settings.explainer_model, settings.explainer_effort)
    return ExplainService(get_kb(), llm)


@lru_cache
def get_repository() -> FailureRepository:
    return FailureRepository(get_settings().database_path)


@lru_cache
def get_stripe() -> stripe.StripeClient:
    return stripe.StripeClient(get_settings().stripe_secret_key)


SettingsDep = Annotated[Settings, Depends(get_settings)]
ServiceDep = Annotated[ExplainService, Depends(get_explain_service)]
RepoDep = Annotated[FailureRepository, Depends(get_repository)]
StripeDep = Annotated[stripe.StripeClient, Depends(get_stripe)]

app = FastAPI(title="Decline Decoder", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().frontend_url],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/explain")
def explain(failure: FailurePayload, service: ServiceDep) -> Explanation:
    started = time.perf_counter()
    result = service.explain(failure)
    log.info(
        "explain code=%s engine=%s latency_ms=%.1f flags=%s",
        result.source_code,
        result.engine,
        (time.perf_counter() - started) * 1000,
        ",".join(result.flags) or "-",
    )
    return result


@app.post("/checkout")
def create_checkout_session(settings: SettingsDep, client: StripeDep) -> dict[str, str]:
    session = client.v1.checkout.sessions.create(
        params={
            "mode": "payment",
            "line_items": [
                {
                    "quantity": 1,
                    "price_data": {
                        "currency": DEMO_CURRENCY,
                        "unit_amount": DEMO_AMOUNT,
                        "product_data": {"name": DEMO_PRODUCT_NAME},
                    },
                }
            ],
            "success_url": f"{settings.frontend_url}/?checkout=success",
            "cancel_url": f"{settings.frontend_url}/?checkout=cancelled",
        }
    )
    if not session.url:
        raise HTTPException(502, "Stripe did not return a Checkout URL")
    return {"url": session.url}


@app.post("/webhooks/stripe")
async def stripe_webhook(
    request: Request, settings: SettingsDep, service: ServiceDep, repo: RepoDep
) -> dict[str, str]:
    payload = await request.body()  # must be the raw bytes Stripe signed
    try:
        stripe.Webhook.construct_event(
            payload, request.headers.get("stripe-signature"), settings.stripe_webhook_secret
        )
    except (ValueError, stripe.SignatureVerificationError) as exc:
        log.warning("Rejected webhook: %s", exc)
        raise HTTPException(400, "Invalid signature") from exc

    event: dict[str, Any] = json.loads(payload)
    if event.get("livemode"):
        log.warning("Ignoring live-mode event %s; this app is test mode only", event.get("id"))
        return {"status": "ignored"}
    if event.get("type") != "payment_intent.payment_failed":
        return {"status": "ignored"}

    payment_intent = event["data"]["object"]
    failure = failure_from_payment_intent(payment_intent)
    explanation = service.explain(failure)
    stored = repo.add(event["id"], payment_intent["id"], failed_at(event), failure, explanation)
    log.info(
        "payment_failed pi=%s code=%s stored=%s",
        payment_intent["id"],
        explanation.source_code,
        stored,
    )
    return {"status": "stored" if stored else "duplicate"}


@app.get("/failures")
def list_failures(repo: RepoDep, limit: int = 100) -> list[StoredFailure]:
    return repo.list(min(max(limit, 1), 500))


@app.get("/failures/{failure_id}")
def get_failure(failure_id: str, repo: RepoDep) -> StoredFailure:
    failure = repo.get(failure_id)
    if failure is None:
        raise HTTPException(404, "Failure not found")
    return failure
