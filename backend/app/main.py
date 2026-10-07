import logging
import time
from functools import lru_cache
from typing import Annotated

import anthropic
from fastapi import Depends, FastAPI

from app.config import get_settings
from app.explainer import ClaudeExplainer, ExplainService
from app.kb import get_kb
from app.schemas import Explanation, FailurePayload

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("decline_decoder")


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


ServiceDep = Annotated[ExplainService, Depends(get_explain_service)]

app = FastAPI(title="Decline Decoder", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/explain")
def explain(failure: FailurePayload, service: ServiceDep) -> Explanation:
    started = time.perf_counter()
    result = service.explain(failure)
    log.info(
        "explain code=%s latency_ms=%.0f flags=%s",
        result.source_code,
        (time.perf_counter() - started) * 1000,
        ",".join(result.flags) or "-",
    )
    return result
