"""G34 — workspace full-text search contracts."""
from __future__ import annotations

from pydantic import BaseModel


class SearchHitOut(BaseModel):
    artifact_type: str
    artifact_id: str
    title: str
    snippet: str
    rank: float


class WorkspaceSearchOut(BaseModel):
    query: str
    hits: list[SearchHitOut]
    engine: str
    total: int
