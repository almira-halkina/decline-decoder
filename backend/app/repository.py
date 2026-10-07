"""SQLite storage for payment failures, behind a thin repository."""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from pydantic import BaseModel

from app.schemas import Explanation, FailurePayload

SCHEMA = """
CREATE TABLE IF NOT EXISTS failures (
    id                TEXT PRIMARY KEY,       -- Stripe event id (evt_...), so retries are no-ops
    payment_intent_id TEXT NOT NULL,
    created_at        TEXT NOT NULL,          -- when the PaymentIntent failed (ISO 8601, UTC)
    failure_json      TEXT NOT NULL,          -- FailurePayload
    explanation_json  TEXT NOT NULL           -- Explanation
);
CREATE INDEX IF NOT EXISTS failures_created_at ON failures (created_at DESC);
"""


class StoredFailure(BaseModel):
    id: str
    payment_intent_id: str
    created_at: datetime
    failure: FailurePayload
    explanation: Explanation


class FailureRepository:
    def __init__(self, path: str) -> None:
        self._path = path
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        try:
            with conn:  # commits on success, rolls back on error
                yield conn
        finally:
            conn.close()

    def add(
        self,
        event_id: str,
        payment_intent_id: str,
        created_at: datetime,
        failure: FailurePayload,
        explanation: Explanation,
    ) -> bool:
        """Stores a failure. Returns False if this event was already stored (Stripe retry)."""
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO failures VALUES (?, ?, ?, ?, ?)",
                (
                    event_id,
                    payment_intent_id,
                    created_at.astimezone(UTC).isoformat(),
                    failure.model_dump_json(),
                    explanation.model_dump_json(),
                ),
            )
            return cursor.rowcount == 1

    def list(self, limit: int = 100) -> list[StoredFailure]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM failures ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_to_model(r) for r in rows]

    def get(self, failure_id: str) -> StoredFailure | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM failures WHERE id = ?", (failure_id,)).fetchone()
        return _to_model(row) if row else None


def _to_model(row: sqlite3.Row) -> StoredFailure:
    return StoredFailure(
        id=row["id"],
        payment_intent_id=row["payment_intent_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        failure=FailurePayload.model_validate(json.loads(row["failure_json"])),
        explanation=Explanation.model_validate(json.loads(row["explanation_json"])),
    )
