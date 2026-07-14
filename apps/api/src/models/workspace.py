from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDMixin


class Workspace(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "workspaces"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    organization_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    target_id: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey(
            "targets.id",
            name="fk_workspaces_target_id_targets",
            ondelete="SET NULL",
            use_alter=True,
        ),
        nullable=True,
    )
    deal_type: Mapped[str] = mapped_column(String(40), nullable=False, default="software_platform")
    investment_question: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    data_classification: Mapped[str] = mapped_column(
        String(30), nullable=False, default="confidential"
    )
    external_llm_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
