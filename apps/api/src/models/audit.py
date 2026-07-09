from __future__ import annotations

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDMixin


class AuditLog(UUIDMixin, TimestampMixin, Base):
    """Lightweight audit trail of agent/service actions per workspace."""

    __tablename__ = "audit_logs"

    workspace_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True, index=True
    )
    agent_name: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    action: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
