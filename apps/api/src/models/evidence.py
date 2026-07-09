from __future__ import annotations

from sqlalchemy import Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDMixin


class Evidence(UUIDMixin, TimestampMixin, Base):
    """An auditable evidence record backing a material claim.

    `ref` is a stable human key (e.g. "EV-010") that risks, questions, and memos cite.
    `claim_type` is one of fact | calculation | inference | assumption.
    """

    __tablename__ = "evidence"

    workspace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ref: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    claim_type: Mapped[str] = mapped_column(String(20), nullable=False, default="fact")
    source_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    source_type: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_date: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source_section: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.6)
    agent_name: Mapped[str] = mapped_column(String(60), nullable=False, default="")
