"""API contracts for read-only tokenized workspace share links (G44)."""
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


class SharedWorkspaceSnapshot(BaseModel):
    """The public, read-only, non-confidential snapshot a share token unlocks."""

    scope: str
    workspace: dict[str, Any]
    target: dict[str, Any] | None
    risks: list[dict[str, Any]]
    counts: dict[str, int]
    disclaimer: str
