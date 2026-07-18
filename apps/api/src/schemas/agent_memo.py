"""G59 — agent-drafted IC memo section API contracts."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.schemas.agent import AgentGroundingOut


class AgentMemoDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_steps_per_section: int = Field(default=6, ge=1, le=16)


class AgentMemoSectionOut(BaseModel):
    section: str
    status: Literal["drafted", "withheld", "error"]
    answer: str | None
    grounding: AgentGroundingOut | None
    artifact_version_id: str | None
    decision: Literal["pending", "accept", "reject"]
    decided_by: str | None = None
    decided_at: str | None = None


class AgentMemoDraftOut(BaseModel):
    workspace_id: str
    status: Literal["in_review", "decided", "not_run"]
    reason: str | None
    sections: list[AgentMemoSectionOut]
    generated_at: str | None
    draft_artifact_id: str | None
    version: int | None
    assembled_markdown: str | None


class AgentMemoDecideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    section: str = Field(min_length=1, max_length=200)
    decision: Literal["accept", "reject"]
