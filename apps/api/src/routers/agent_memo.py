"""G59 — agent-drafted IC memo endpoints.

POST ``/workspaces/{id}/agent-memo/draft`` runs the per-section G57 loop (LLM-capable: the
orchestrator must add ``agent-memo/draft`` to the G58 ``_LLM_CAPABLE_PATHS`` quota set in
``main.py`` alongside ``agent/run``, and register this module in ``_ROUTER_MODULES``). GET
serves the newest draft; the decide endpoint records a HUMAN accept/reject and therefore
requires an authenticated actor — the agent proposes, only a person disposes.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request

from src.routers.deps import SessionDep
from src.schemas.agent_memo import (
    AgentMemoDecideRequest,
    AgentMemoDraftOut,
    AgentMemoDraftRequest,
)
from src.services import agent_memo_service

router = APIRouter(prefix="/api", tags=["agent-memo"])


def _actor_id(request: Request, header_actor_id: str | None) -> str | None:
    principal = getattr(request.state, "principal", None)
    return principal.user_id if principal is not None else header_actor_id


@router.post("/workspaces/{workspace_id}/agent-memo/draft", response_model=AgentMemoDraftOut)
def draft_agent_memo(
    workspace_id: str,
    session: SessionDep,
    request: Request,
    payload: AgentMemoDraftRequest | None = None,
    header_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
) -> AgentMemoDraftOut:
    body = payload or AgentMemoDraftRequest()
    try:
        record = agent_memo_service.draft_sections(
            session,
            workspace_id,
            actor_id=_actor_id(request, header_actor_id),
            max_steps_per_section=body.max_steps_per_section,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return AgentMemoDraftOut.model_validate(record)


@router.get("/workspaces/{workspace_id}/agent-memo", response_model=AgentMemoDraftOut)
def get_agent_memo_draft(workspace_id: str, session: SessionDep) -> AgentMemoDraftOut:
    record = agent_memo_service.get_latest_draft(session, workspace_id)
    if record is None:
        raise HTTPException(
            status_code=404, detail="No agent memo draft exists for this workspace yet."
        )
    return AgentMemoDraftOut.model_validate(record)


@router.post(
    "/workspaces/{workspace_id}/agent-memo/{draft_id}/sections/decide",
    response_model=AgentMemoDraftOut,
)
def decide_agent_memo_section(
    workspace_id: str,
    draft_id: str,
    payload: AgentMemoDecideRequest,
    session: SessionDep,
    request: Request,
    header_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
) -> AgentMemoDraftOut:
    actor_id = _actor_id(request, header_actor_id)
    if not actor_id:
        raise HTTPException(
            status_code=401,
            detail="A section decision requires an authenticated actor "
            "(session principal or X-Actor-ID header).",
        )
    try:
        record = agent_memo_service.decide_section(
            session,
            workspace_id,
            draft_id,
            payload.section,
            payload.decision,
            actor_id=actor_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return AgentMemoDraftOut.model_validate(record)
