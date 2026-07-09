from __future__ import annotations

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDMixin


class RedTeamReport(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "red_team_reports"

    workspace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    bear_case_markdown: Mapped[str] = mapped_column(Text, nullable=False, default="")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    unsupported_claims: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    missing_evidence: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    high_priority_questions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
