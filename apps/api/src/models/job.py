"""Durable background job rows: the generic queue behind workspace builds."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDMixin


class BackgroundJob(UUIDMixin, TimestampMixin, Base):
    """One durable unit of background work, claimed atomically by exactly one worker.

    Lifecycle: ``queued`` -> ``running`` -> ``succeeded`` | ``failed`` (retryable, with a
    backoff delay in ``next_attempt_at``) | ``dead`` (attempts exhausted). Crashed workers
    leave ``running`` rows whose ``heartbeat_at`` goes stale; recovery requeues them.
    """

    __tablename__ = "background_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','succeeded','failed','dead')",
            name="ck_background_job_status",
        ),
        Index("ix_background_jobs_status_next", "status", "next_attempt_at"),
    )

    job_type: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
