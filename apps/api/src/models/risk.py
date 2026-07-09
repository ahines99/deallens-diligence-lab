from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDMixin


class RiskFinding(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "risk_findings"

    workspace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    risk_category: Mapped[str] = mapped_column(String(40), nullable=False)
    risk_category_label: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    finding: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="medium")
    severity_score: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    likelihood: Mapped[str] = mapped_column(String(20), nullable=False, default="medium")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.6)
    evidence_ref: Mapped[str | None] = mapped_column(String(20), nullable=True)
    follow_up_question: Mapped[str] = mapped_column(Text, nullable=False, default="")
    workstream_owner: Mapped[str] = mapped_column(String(40), nullable=False, default="commercial")
