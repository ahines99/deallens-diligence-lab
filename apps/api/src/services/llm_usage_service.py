"""G80 — LLM cost telemetry: best-effort usage capture and per-org spend rollups.

``record_call`` is invoked from the provider seam (``llm_provider._report_usage``) after every
live HTTP response. Like the job-heartbeat pattern in ``job_service``, it persists on its OWN
short-lived session and swallows every database error: the caller may be mid-transaction on a
shared session (committing it would break atomic-projection contracts), and a telemetry failure
— missing table, locked SQLite file, broken session factory — must never fail or slow the LLM
call it measured.

``spend_summary`` is the read side: a windowed per-model rollup consumed by the quota-usage
endpoint (``routers/quotas.py``) beside the G39/G58 rate buckets.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.base import now_utc
from src.models.llm_usage import LlmUsageEvent
from src.services import request_context

logger = logging.getLogger("deallens.llm_usage")


def record_call(*, model: str, input_tokens: int | None, output_tokens: int | None) -> None:
    """Persist one usage event; a guaranteed no-op on any failure.

    Tenant attribution reads ``request_context.current_organization_id`` — stamped by the
    identity middleware for request paths, ``None`` for background/worker paths, which are
    recorded untagged rather than guessed. Runs on a dedicated short-lived session (never the
    caller's) and never raises, even with the schema missing or the session factory broken.
    """
    try:
        from src.db.session import SessionLocal

        organization_id = request_context.current_organization_id.get()
        with SessionLocal() as session:
            session.add(
                LlmUsageEvent(
                    organization_id=organization_id,
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
            )
            session.commit()
    except Exception:  # noqa: BLE001 - telemetry is strictly best-effort, never raising
        logger.debug("LLM usage event dropped (telemetry is best-effort)", exc_info=True)


def spend_summary(
    session: Session,
    organization_id: str | None = None,
    *,
    window_hours: int | None = None,
) -> dict[str, Any]:
    """Roll up recorded usage: totals plus a per-model breakdown.

    ``organization_id=None`` is the GLOBAL view. Untagged rows (``organization_id`` NULL —
    background work that never crossed the identity middleware) appear ONLY in the global view:
    attributing them to any specific tenant would fabricate spend, so a per-org summary is
    strictly the rows provably stamped with that org. ``window_hours=None`` means all-time.

    NULL token counts (a provider response without usage fields) sum as 0, but the call still
    counts toward ``calls``/``total_calls``.
    """
    filters = []
    if organization_id is not None:
        filters.append(LlmUsageEvent.organization_id == organization_id)
    if window_hours is not None:
        filters.append(LlmUsageEvent.created_at >= now_utc() - timedelta(hours=window_hours))

    rows = session.execute(
        select(
            LlmUsageEvent.model,
            func.count().label("calls"),
            func.coalesce(func.sum(LlmUsageEvent.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(LlmUsageEvent.output_tokens), 0).label("output_tokens"),
        )
        .where(*filters)
        .group_by(LlmUsageEvent.model)
        .order_by(LlmUsageEvent.model)
    ).all()

    by_model = [
        {
            "model": model,
            "calls": int(calls),
            "input_tokens": int(input_tokens),
            "output_tokens": int(output_tokens),
        }
        for model, calls, input_tokens, output_tokens in rows
    ]
    return {
        "total_calls": sum(entry["calls"] for entry in by_model),
        "input_tokens": sum(entry["input_tokens"] for entry in by_model),
        "output_tokens": sum(entry["output_tokens"] for entry in by_model),
        "by_model": by_model,
    }


__all__ = ["record_call", "spend_summary"]
