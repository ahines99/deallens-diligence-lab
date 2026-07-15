"""G34 — full-text search across all artifacts in a workspace (one interface, two engines)."""
from __future__ import annotations

from fastapi import APIRouter, Query

from src.routers.deps import SessionDep
from src.schemas.search import SearchHitOut, WorkspaceSearchOut
from src.services import search_service
from src.services.common import get_workspace_or_404

router = APIRouter(prefix="/api/workspaces", tags=["search"])


@router.get("/{workspace_id}/search", response_model=WorkspaceSearchOut)
def search_workspace(
    workspace_id: str,
    session: SessionDep,
    q: str = Query(default="", description="Full-text query over workspace artifacts"),
    limit: int = Query(default=20, ge=1, le=100),
) -> WorkspaceSearchOut:
    get_workspace_or_404(session, workspace_id)
    result = search_service.search_workspace(session, workspace_id, q, limit)
    return WorkspaceSearchOut(
        query=result.query,
        hits=[SearchHitOut(**vars(hit)) for hit in result.hits],
        engine=result.engine,
        total=result.total,
    )


__all__ = ["router"]
