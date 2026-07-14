from __future__ import annotations

from fastapi import APIRouter, HTTPException

from src.routers.deps import SessionDep
from src.schemas.filing import FilingOut, FilingsQAOut, FilingsQARequest
from src.services import filings_qa_service, sec_ingestion_service
from src.services.common import get_workspace_or_404

router = APIRouter(prefix="/api/workspaces", tags=["filings"])


@router.get("/{workspace_id}/filings", response_model=list[FilingOut])
def list_filings(workspace_id: str, session: SessionDep) -> list[FilingOut]:
    get_workspace_or_404(session, workspace_id)
    return [FilingOut.model_validate(f) for f in sec_ingestion_service.list_filings(session, workspace_id)]


@router.post("/{workspace_id}/qa", response_model=FilingsQAOut)
def ask_filings(
    workspace_id: str, payload: FilingsQARequest, session: SessionDep
) -> FilingsQAOut:
    try:
        result = filings_qa_service.ask(session, workspace_id, payload.question)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return FilingsQAOut.model_validate(result)
