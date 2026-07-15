from __future__ import annotations

from fastapi import APIRouter

from src.db.base import now_utc
from src.routers.deps import SessionDep
from src.schemas.signals import FilingWatch, InsiderPatterns, NewsSignals, SignalsOverview
from src.schemas.workspace import WorkspaceOverview
from src.services import (
    news_service,
    sec_feeds_service,
    signals_overview_service,
    watch_service,
    workspace_service,
)
from src.services.common import NotFound, get_workspace_or_404

router = APIRouter(prefix="/api/workspaces", tags=["signals"])


@router.get("/{workspace_id}/news", response_model=NewsSignals)
def get_news(workspace_id: str, session: SessionDep) -> NewsSignals:
    get_workspace_or_404(session, workspace_id)
    target = workspace_service.get_target(session, workspace_id)
    if target is None:
        raise NotFound("No target set; ingest a company first.")
    data = news_service.fetch_news(target.name)
    return NewsSignals.model_validate(
        {
            "workspace_id": workspace_id,
            "query": data["query"],
            "articles": data["articles"],
            "source_status": data["source_status"],
            "source_error": data["source_error"],
            "generated_at": now_utc(),
        }
    )


@router.get("/{workspace_id}/filing-watch", response_model=FilingWatch)
def get_filing_watch(workspace_id: str, session: SessionDep) -> FilingWatch:
    get_workspace_or_404(session, workspace_id)
    return FilingWatch.model_validate(watch_service.filing_watch(session, workspace_id))


@router.get("/{workspace_id}/insider-patterns", response_model=InsiderPatterns)
def get_insider_patterns(workspace_id: str, session: SessionDep) -> InsiderPatterns:
    get_workspace_or_404(session, workspace_id)
    return InsiderPatterns.model_validate(sec_feeds_service.insider_patterns(session, workspace_id))


@router.get("/{workspace_id}/signals-overview", response_model=SignalsOverview)
def get_signals_overview(workspace_id: str, session: SessionDep) -> SignalsOverview:
    get_workspace_or_404(session, workspace_id)
    return SignalsOverview.model_validate(signals_overview_service.overview(session, workspace_id))


@router.post("/{workspace_id}/refresh", response_model=WorkspaceOverview)
def refresh_workspace(workspace_id: str, session: SessionDep) -> WorkspaceOverview:
    get_workspace_or_404(session, workspace_id)
    return WorkspaceOverview.model_validate(watch_service.refresh(session, workspace_id))
