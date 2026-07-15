"""Response contract for the G47 memo-redline diff."""
from __future__ import annotations

from pydantic import BaseModel


class RunRef(BaseModel):
    id: str
    version: int
    run_type: str
    granularity: str


class ChangedClaim(BaseModel):
    before: str
    after: str
    numeric_change: bool
    numbers_added: list[str]
    numbers_removed: list[str]


class MemoRedlineOut(BaseModel):
    workspace_id: str
    run_a: RunRef
    run_b: RunRef
    granularity: str
    changed: list[ChangedClaim]
    added: list[str]
    removed: list[str]
    numeric_changes: list[ChangedClaim]
    counts: dict[str, int]
    is_empty: bool
