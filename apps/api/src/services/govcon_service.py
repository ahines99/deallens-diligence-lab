"""GovCon profile persistence (fetch from USAspending, store, read)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models import GovConProfile
from src.services import usaspending_service
from src.services.common import NotFound, get_workspace_or_404
from src.services.workspace_service import get_target


def fetch(session: Session, workspace_id: str, recipient_name: str | None = None) -> GovConProfile:
    get_workspace_or_404(session, workspace_id)
    name = (recipient_name or "").strip()
    if not name:
        target = get_target(session, workspace_id)
        if target is None:
            raise NotFound("No target set; provide a recipient_name or ingest a company first.")
        name = target.name

    data = usaspending_service.award_profile(name)  # may raise UsaSpendingError

    profile = session.scalar(select(GovConProfile).where(GovConProfile.workspace_id == workspace_id))
    if profile is None:
        profile = GovConProfile(workspace_id=workspace_id)
        session.add(profile)
    profile.recipient_name = data["recipient_name"]
    profile.total_obligations = data["total_obligations"]
    profile.award_count = data["award_count"]
    profile.top_agency = data["top_agency"]
    profile.top_agency_pct = data["top_agency_pct"]
    profile.agency_concentration = data["agency_concentration"]
    profile.top_awards = data["top_awards"]
    profile.recompete = data["recompete_within_24mo"]
    session.flush()
    return profile


def get(session: Session, workspace_id: str) -> GovConProfile:
    profile = session.scalar(select(GovConProfile).where(GovConProfile.workspace_id == workspace_id))
    if profile is None:
        raise NotFound("No GovCon profile generated yet.")
    return profile


def get_optional(session: Session, workspace_id: str) -> GovConProfile | None:
    return session.scalar(select(GovConProfile).where(GovConProfile.workspace_id == workspace_id))
