from __future__ import annotations

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDMixin


class Memo(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "memos"

    workspace_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    memo_type: Mapped[str] = mapped_column(String(20), nullable=False)  # ic_memo | bear_case
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    markdown_content: Mapped[str] = mapped_column(Text, nullable=False, default="")
