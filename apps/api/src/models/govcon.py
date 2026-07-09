from __future__ import annotations

from sqlalchemy import JSON, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDMixin


class GovConProfile(UUIDMixin, TimestampMixin, Base):
    """Federal contract profile for a target, derived from USAspending.gov (Release 0.5)."""

    __tablename__ = "govcon_profiles"

    workspace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    recipient_name: Mapped[str] = mapped_column(String(200), nullable=False)
    total_obligations: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    award_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    top_agency: Mapped[str | None] = mapped_column(String(200), nullable=True)
    top_agency_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    agency_concentration: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    top_awards: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    recompete: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
