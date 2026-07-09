from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDMixin


class Filing(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "filings"

    workspace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    company_name: Mapped[str] = mapped_column(String(200), nullable=False)
    ticker: Mapped[str | None] = mapped_column(String(20), nullable=True)
    cik: Mapped[str | None] = mapped_column(String(20), nullable=True)
    form_type: Mapped[str] = mapped_column(String(20), nullable=False)
    filing_date: Mapped[str] = mapped_column(String(20), nullable=False)  # 'YYYY-MM-DD'
    accession_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    document_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    local_text_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    section_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
