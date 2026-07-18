"""G80 — append-only LLM cost-telemetry events.

One row per live provider HTTP response, recorded best-effort at the provider seam
(``llm_provider._report_usage``). ``organization_id`` comes from the request contextvar
(``src.services.request_context``); background paths that never crossed the identity
middleware record ``NULL`` — "untagged" — rather than guessing a tenant. Token counts are
nullable because a provider response may omit usage fields; the call itself still counts.

Rows are append-only by nature: nothing in the codebase updates or deletes them, they are
only inserted (``llm_usage_service.record_call``) and aggregated (``spend_summary``).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, UUIDMixin, now_utc


class LlmUsageEvent(UUIDMixin, Base):
    """Token usage from one live LLM call, attributed to a tenant when one was in scope."""

    __tablename__ = "llm_usage_events"

    # Nullable, no FK: untagged/background usage must still be recordable, and telemetry must
    # never fail on referential grounds (e.g. an organization deleted mid-flight).
    organization_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )
