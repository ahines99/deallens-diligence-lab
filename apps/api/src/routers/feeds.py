from __future__ import annotations

import logging

from fastapi import APIRouter

from src.routers.deps import SessionDep
from src.schemas.comp import CompOut
from src.schemas.feeds import EventTimeline, InsiderActivity, ThemeScan
from src.services import sec_feeds_service as feeds
from src.services.common import get_workspace_or_404

logger = logging.getLogger("deallens.feeds")

router = APIRouter(prefix="/api/workspaces", tags=["feeds"])


@router.get("/{workspace_id}/events", response_model=EventTimeline)
def get_events(workspace_id: str, session: SessionDep) -> EventTimeline:
    get_workspace_or_404(session, workspace_id)
    return EventTimeline.model_validate(feeds.events(session, workspace_id))


@router.get("/{workspace_id}/insiders", response_model=InsiderActivity)
def get_insiders(workspace_id: str, session: SessionDep) -> InsiderActivity:
    get_workspace_or_404(session, workspace_id)
    return InsiderActivity.model_validate(feeds.insiders(session, workspace_id))


@router.get("/{workspace_id}/themes", response_model=ThemeScan)
def get_themes(workspace_id: str, session: SessionDep) -> ThemeScan:
    get_workspace_or_404(session, workspace_id)
    return ThemeScan.model_validate(feeds.themes(session, workspace_id))


@router.post("/{workspace_id}/comps/auto", response_model=list[CompOut])
def auto_comps(workspace_id: str, session: SessionDep) -> list[CompOut]:
    get_workspace_or_404(session, workspace_id)
    result = feeds.auto_comps(session, workspace_id)
    session.commit()
    logger.info("auto-comps for %s: %s", workspace_id, result.get("note"))
    return [CompOut.model_validate(c) for c in result["comps"]]
