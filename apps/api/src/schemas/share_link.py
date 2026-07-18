"""API contracts for read-only tokenized workspace share links (G44) + analytics (G76)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.schemas.common import ORMModel

ShareScope = Literal["read_only"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ShareLinkCreate(StrictModel):
    label: str = Field(default="", max_length=200)
    scope: ShareScope = "read_only"
    expires_at: datetime | None = None


class ShareLinkOut(ORMModel):
    id: str
    organization_id: str
    workspace_id: str
    created_by_user_id: str | None = None
    scope: str
    label: str
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    last_accessed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ShareLinkCreatedOut(StrictModel):
    """Returned once at creation; ``token`` (the ``dsh_`` plaintext) is never shown again."""

    share_link: ShareLinkOut
    token: str


class ShareLinkViewOut(StrictModel):
    """One recorded public read of the link — coarse context only (G76)."""

    viewed_at: datetime
    user_agent: str | None = None


class ShareLinkAnalyticsOut(StrictModel):
    """Owner-facing view analytics for one share link (G76).

    ``share_link`` carries the existing revocation/expiry state (``revoked_at``,
    ``expires_at``) so the UI can present views and one-click revoke together. Served only
    through the org-scoped management surface — the public token route never exposes this.
    """

    share_link: ShareLinkOut
    view_count: int
    first_viewed_at: datetime | None = None
    last_viewed_at: datetime | None = None
    recent: list[ShareLinkViewOut]


class SharedWorkspaceSnapshot(BaseModel):
    """The public, read-only, non-confidential snapshot a share token unlocks."""

    scope: str
    workspace: dict[str, Any]
    target: dict[str, Any] | None
    risks: list[dict[str, Any]]
    counts: dict[str, int]
    disclaimer: str
    # Server-composed provenance line rendered as a visible overlay by the shared view (G76).
    watermark: str
