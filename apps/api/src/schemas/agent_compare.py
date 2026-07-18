"""G63 — comparative-agent API contracts."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.schemas.agent import AgentGroundingOut


class AgentCompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    # The service appends per-workspace framing that must stay inside the G57 objective cap,
    # hence the tighter bound than AgentRunRequest.
    objective: str = Field(min_length=1, max_length=1_800)
    comp_workspace_ids: list[str] = Field(min_length=1, max_length=3)
    max_steps_per_workspace: int = Field(default=6, ge=1, le=16)


class AgentCompareWorkspaceOut(BaseModel):
    workspace_id: str
    workspace_name: str
    role: Literal["primary", "comp"]
    status: Literal[
        "completed", "rejected_ungrounded", "budget_exhausted", "error", "not_run"
    ]
    reason: str
    answer: str | None
    artifact_version_id: str | None
    tools_used: list[str]
    steps_used: int
    grounding: AgentGroundingOut | None


class AgentCompareOut(BaseModel):
    primary_workspace_id: str
    comp_workspace_ids: list[str]
    objective: str
    status: Literal["completed", "rejected_ungrounded", "not_run"]
    reason: str
    blocking_workspace_id: str | None
    per_workspace: list[AgentCompareWorkspaceOut]
    merged_markdown: str | None
    grounding: AgentGroundingOut | None
    artifact_version_id: str | None
    generated_at: str
