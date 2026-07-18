"""G57 — diligence-agent API contracts."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    objective: str = Field(min_length=1, max_length=2_000)
    max_steps: int = Field(default=8, ge=1, le=16)
    # Client-generated idempotency key (G61 recovery): one user action carries ONE id across the
    # streaming POST and any fallback POST, so a retry after an ambiguous network failure can
    # never execute (or bill, or seal) the same run twice. Optional — a bare API call without
    # one keeps the plain run-once semantics.
    client_request_id: str | None = Field(
        default=None, min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$"
    )


class AgentStepOut(BaseModel):
    tool: str
    arguments: dict[str, Any]
    ok: bool
    result: dict[str, Any] | None = None
    error: str | None = None


class AgentGroundingOut(BaseModel):
    grounded: bool
    numeric_violations: list[str]
    unknown_refs: list[str]


class AgentRunOut(BaseModel):
    workspace_id: str
    objective: str
    status: Literal[
        "completed", "rejected_ungrounded", "budget_exhausted", "error", "not_run"
    ]
    reason: str
    answer: str | None
    steps: list[AgentStepOut]
    tools_used: list[str]
    steps_used: int
    artifact_version_id: str | None
    manifest: dict[str, str] | None
    grounding: AgentGroundingOut | None
    generated_at: str
    # Defaulted: sealed transcripts from before the idempotency seam carry no request id.
    client_request_id: str | None = None
