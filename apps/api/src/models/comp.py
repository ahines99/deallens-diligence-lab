from __future__ import annotations

from sqlalchemy import Boolean, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDMixin


class ComparableCompany(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "comparable_companies"

    workspace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ticker: Mapped[str] = mapped_column(String(20), nullable=False)
    company_name: Mapped[str] = mapped_column(String(200), nullable=False)
    sector: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    business_description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    revenue: Mapped[float | None] = mapped_column(Float, nullable=True)
    gross_margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    operating_margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    revenue_growth: Mapped[float | None] = mapped_column(Float, nullable=True)
    rnd_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_cap: Mapped[float | None] = mapped_column(Float, nullable=True)
    enterprise_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    ev_revenue_multiple: Mapped[float | None] = mapped_column(Float, nullable=True)

    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    data_source: Mapped[str] = mapped_column(String(60), nullable=False, default="SEC EDGAR (XBRL)")
    is_illustrative: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
