from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
LIVE_KEY_PREFIXES = ("sk_live_", "pk_live_", "rk_live_")

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    stripe_secret_key: str = ""
    stripe_publishable_key: str = ""
    stripe_webhook_secret: str = ""

    # "rules" (default): keyword rules over Stripe's docs text, no LLM.
    # "claude": Claude writes the explanation; rules are the fallback if a call fails.
    explainer_engine: Literal["rules", "claude"] = "rules"
    anthropic_api_key: str = ""
    explainer_model: str = "claude-opus-5-5"
    explainer_effort: Effort = "low"

    database_path: str = "decline_decoder.sqlite3"
    frontend_url: str = "http://localhost:5173"

    @field_validator("stripe_secret_key", "stripe_publishable_key")
    @classmethod
    def refuse_live_keys(cls, value: str) -> str:
        if value.startswith(LIVE_KEY_PREFIXES):
            raise ValueError("Live Stripe keys are not allowed; this app runs in test mode only.")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
