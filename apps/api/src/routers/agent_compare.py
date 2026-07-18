"""G63 — the comparative-agent endpoint.

One POST runs the same objective across the primary workspace plus 1..3 comp workspaces (each
as its own governed, harness-scoped G57 run), merges the individually grounded answers
deterministically with per-workspace provenance, and returns the sealed comparative record.
Consent is unanimous and fail-closed: any non-consenting workspace makes the whole run
``not_run`` with the blocking workspace named. The route belongs in the G58 LLM-capable quota
set (``_LLM_CAPABLE_PATHS``) since one request may trigger up to four live agent runs.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request

from src.routers.deps import SessionDep
from src.schemas.agent_compare import AgentCompareOut, AgentCompareRequest
from src.services import agent_compare_service

router = APIRouter(prefix="/api", tags=["agent"])


def _actor_id(request: Request, header_actor_id: str | None) -> str | None:
    # Mirrors routers/agent.py: an authenticated principal outranks the dev-mode header.
    principal = getattr(request.state, "principal", None)
    return principal.user_id if principal is not None else header_actor_id


@router.post("/workspaces/{workspace_id}/agent/compare", response_model=AgentCompareOut)
def run_agent_compare(
    workspace_id: str,
    payload: AgentCompareRequest,
    session: SessionDep,
    request: Request,
    header_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
) -> AgentCompareOut:
    try:
        record = agent_compare_service.run_comparative_agent(
            session,
            workspace_id,
            payload.comp_workspace_ids,
            payload.objective,
            actor_id=_actor_id(request, header_actor_id),
            max_steps_per_workspace=payload.max_steps_per_workspace,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return AgentCompareOut.model_validate(record)
