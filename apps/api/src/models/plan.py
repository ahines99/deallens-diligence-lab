from __future__ import annotations

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDMixin


class DiligencePlan(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "diligence_plans"

    workspace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    investment_question: Mapped[str] = mapped_column(Text, nullable=False, default="")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # List[{workstream, workstream_label, objective, key_questions[], evidence_needed[], status}]
    workstreams: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
