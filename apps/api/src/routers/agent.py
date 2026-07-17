"""G57 — the diligence agent endpoint.

One POST runs a budget-capped, consent-gated tool loop over the workspace's governed read-only
tools and returns the full sealed run record (see ``agent_service`` for the boundaries). The
route is in the LLM-capable quota set (G58), so a live deployment's spend stays bounded.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request

from src.routers.deps import SessionDep
from src.schemas.agent import AgentRunOut, AgentRunRequest
from src.services import agent_service

router = APIRouter(prefix="/api", tags=["agent"])


def _actor_id(request: Request, header_actor_id: str | None) -> str | None:
    principal = getattr(request.state, "principal", None)
    return principal.user_id if principal is not None else header_actor_id


@router.post("/workspaces/{workspace_id}/agent/run", response_model=AgentRunOut)
def run_agent(
    workspace_id: str,
    payload: AgentRunRequest,
    session: SessionDep,
    request: Request,
    header_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
) -> AgentRunOut:
    try:
        record = agent_service.run_diligence_agent(
            session,
            workspace_id,
            payload.objective,
            actor_id=_actor_id(request, header_actor_id),
            max_steps=payload.max_steps,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return AgentRunOut.model_validate(record)
