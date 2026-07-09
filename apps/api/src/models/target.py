from __future__ import annotations

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDMixin


class Target(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "targets"

    workspace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    target_type: Mapped[str] = mapped_column(String(30), nullable=False, default="public_company")
    ticker: Mapped[str | None] = mapped_column(String(20), nullable=True)
    cik: Mapped[str | None] = mapped_column(String(20), nullable=True)
    sector: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Financials (real, from SEC XBRL for public targets). Money in USD.
    revenue: Mapped[float | None] = mapped_column(Float, nullable=True)
    revenue_growth: Mapped[float | None] = mapped_column(Float, nullable=True)
    gross_margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    operating_margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_income: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    rnd_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    rule_of_40: Mapped[float | None] = mapped_column(Float, nullable=True)
    cash: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_debt: Mapped[float | None] = mapped_column(Float, nullable=True)
    headcount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fiscal_year_end: Mapped[str | None] = mapped_column(String(20), nullable=True)

    data_source: Mapped[str] = mapped_column(String(60), nullable=False, default="SEC EDGAR (XBRL)")
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Full financial extract with per-metric XBRL source points (for evidence citations).
    financials: Mapped[dict | None] = mapped_column(JSON, nullable=True)
