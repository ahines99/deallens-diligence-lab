from __future__ import annotations

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDMixin


class DiligenceQuestion(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "diligence_questions"

    workspace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workstream: Mapped[str] = mapped_column(String(40), nullable=False)
    workstream_label: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    question: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default="medium")
    evidence_ref: Mapped[str | None] = mapped_column(String(20), nullable=True)
