"""Comment threads with @mentions on any governed artifact (G41).

Unlike :class:`ICComment` (bound specifically to an ``ic_packets`` row), a ``Comment`` is a general,
tenant-scoped thread that can hang off *any* governed artifact — a risk finding, a QoE adjustment, a
memo section, an IC packet, or a whole workspace — addressed by ``(entity_type, entity_id)``. The
artifact reference is intentionally opaque: the organization boundary is the tenant guard, not a
per-type foreign key, so one model covers every artifact plane without a migration per new type.

``@mentions`` are resolved at write time against the author's organization members and stored as a
JSON list of user ids; the mention fan-out (audit event -> notification) is handled in
``comment_service``.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDMixin

# The governed artifact planes a comment thread may attach to. Kept in sync with
# ``src.schemas.comment.CommentEntityType``.
COMMENT_ENTITY_TYPES = ("risk", "qoe_adjustment", "memo", "ic_packet", "workspace")


class Comment(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "comments"
    __table_args__ = (
        CheckConstraint(
            "entity_type IN ('risk','qoe_adjustment','memo','ic_packet','workspace')",
            name="ck_comments_entity_type",
        ),
        Index("ix_comments_entity", "organization_id", "entity_type", "entity_id"),
        Index("ix_comments_parent", "parent_comment_id"),
    )

    organization_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    author_user_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    author_display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    parent_comment_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("comments.id", ondelete="SET NULL"), nullable=True
    )
    # User ids resolved from ``@mentions`` in ``body`` at write time (org members only).
    mentions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by_user_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
