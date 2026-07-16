"""Per-membership capability grants and revokes (G49).

A row overrides one capability for one membership: ``granted=True`` lifts a membership above its
role default, ``granted=False`` drops it below. Absent a row, the role default applies. Resolution
lives in :mod:`src.services.permission_service`.
"""
from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDMixin


class MembershipPermission(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "membership_permissions"
    __table_args__ = (
        UniqueConstraint("membership_id", "capability", name="uq_membership_permission"),
    )

    membership_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("organization_memberships.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    capability: Mapped[str] = mapped_column(String(60), nullable=False)
    # True = explicit grant (add), False = explicit revoke (remove) relative to the role default.
    granted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
