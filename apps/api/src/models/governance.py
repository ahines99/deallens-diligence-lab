"""DEF 14A proxy-derived governance profile: executive comp + governance red flags.

A proxy parse is expensive (fetch the HTML proxy, locate + parse the Summary Compensation
Table, run red-flag heuristics over the full text), so the result is persisted per workspace
and re-run on demand. ``source_status`` preserves the available/partial/unavailable discipline:
a failed fetch is stored as ``unavailable`` with empty comp/flags — never a false-clean result.
"""
from __future__ import annotations

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDMixin


class GovernanceProfile(UUIDMixin, TimestampMixin, Base):
    """Executive compensation + governance red flags parsed from a target's latest DEF 14A."""

    __tablename__ = "governance_profiles"

    workspace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    def14a_accession: Mapped[str | None] = mapped_column(String(30), nullable=True)
    filing_date: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # exec_comp: list of NEO rows {name,title,salary,bonus,stock_awards,total} — missing values None.
    exec_comp: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # red_flags: list of {flag,label,present,evidence} governance heuristics.
    red_flags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    source_status: Mapped[str] = mapped_column(String(20), nullable=False, default="unavailable")
    raw_note: Mapped[str | None] = mapped_column(Text, nullable=True)
