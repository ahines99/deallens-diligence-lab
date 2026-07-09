from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from src.routers.deps import SessionDep
from src.schemas.filing import FilingOut, SecIngestRequest, SecSearchResult
from src.services import analysis_service, sec_ingestion_service
from src.services.common import get_workspace_or_404
from src.services.edgar_client import EdgarError

router = APIRouter(prefix="/api/sec", tags=["sec"])


@router.get("/search", response_model=list[SecSearchResult])
def sec_search(q: str = Query("", description="Ticker or company name substring")) -> list[SecSearchResult]:
    return [SecSearchResult(**r) for r in sec_ingestion_service.search(q)]


@router.post("/ingest", response_model=list[FilingOut])
def sec_ingest(payload: SecIngestRequest, session: SessionDep) -> list[FilingOut]:
    get_workspace_or_404(session, payload.workspace_id)
    if not payload.ticker:
        raise HTTPException(status_code=422, detail="A ticker is required for SEC ingestion.")
    try:
        sec_ingestion_service.ingest_company(
            session, payload.workspace_id, payload.ticker, filing_limit=payload.limit or 8
        )
        session.commit()
        analysis_service.run_full_analysis(session, payload.workspace_id)
    except EdgarError as exc:
        status = 404 if "not found" in str(exc).lower() else 502
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return [FilingOut.model_validate(f) for f in sec_ingestion_service.list_filings(session, payload.workspace_id)]
