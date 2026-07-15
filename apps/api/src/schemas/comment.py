"""API contracts for comment threads with @mentions (G41)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.schemas.common import ORMModel

CommentEntityType = Literal["risk", "qoe_adjustment", "memo", "ic_packet", "workspace"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CommentCreate(StrictModel):
    entity_type: CommentEntityType
    entity_id: str = Field(min_length=1, max_length=64)
    body: str = Field(min_length=1, max_length=10_000)
    parent_comment_id: str | None = Field(default=None, max_length=32)


class CommentOut(ORMModel):
    id: str
    organization_id: str
    author_user_id: str | None = None
    author_display_name: str | None = None
    entity_type: str
    entity_id: str
    body: str
    parent_comment_id: str | None = None
    mentions: list[str] = []
    resolved_at: datetime | None = None
    resolved_by_user_id: str | None = None
    created_at: datetime
    updated_at: datetime


class CommentThreadOut(CommentOut):
    """A top-level comment with its direct replies (one level of nesting, like IC comments)."""

    replies: list[CommentOut] = []
