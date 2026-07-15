"""G05 — persisted LLM-as-judge faithfulness runs.

Each row is one graded (question, answer, context) case, carrying the judge name and the
model/prompt provenance so a quality view per model and per prompt version is possible. Rows are
append-only evidence artifacts, like the other governed eval records.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    Index,
    String,
    Text,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, UUIDMixin, now_utc


class JudgeEvalRun(UUIDMixin, Base):
    """One persisted faithfulness judgment over a single answer."""

    __tablename__ = "judge_eval_runs"
    __table_args__ = (
        Index("ix_judge_eval_runs_model_prompt", "model_version", "prompt_version"),
        Index("ix_judge_eval_runs_workspace_created", "workspace_id", "created_at"),
    )

    # Nullable: golden-set evals are not tied to a workspace. No FK so an eval can outlive/precede
    # any workspace and still be queryable in the quality view.
    workspace_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[str] = mapped_column(Text, nullable=False)
    judge_name: Mapped[str] = mapped_column(String(60), nullable=False)
    model_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(60), nullable=True)
    prompt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    faithful: Mapped[bool] = mapped_column(Boolean, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )


def _reject_immutable_mutation(mapper, connection, target) -> None:  # pragma: no cover - hook
    del mapper, connection
    raise ValueError(f"{type(target).__name__} records are append-only")


event.listen(JudgeEvalRun, "before_update", _reject_immutable_mutation)
event.listen(JudgeEvalRun, "before_delete", _reject_immutable_mutation)
