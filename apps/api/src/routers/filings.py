from __future__ import annotations

from fastapi import APIRouter, HTTPException

from src.routers.deps import SessionDep
from src.schemas.filing import (
    CrossCorpusQAOut,
    CrossCorpusQARequest,
    FilingOut,
    FilingsQAOut,
    FilingsQARequest,
    RiskDiffOut,
)
from src.services import (
    cross_corpus_qa_service,
    filing_diff_service,
    filings_qa_service,
    sec_ingestion_service,
)
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


@router.get("/{workspace_id}/filings/risk-diff", response_model=RiskDiffOut)
def risk_factor_diff(workspace_id: str, session: SessionDep) -> RiskDiffOut:
    """Cross-year 10-K risk-factor drift (added / removed / materially changed) with citations."""
    result = filing_diff_service.diff_risk_factors(session, workspace_id)
    return RiskDiffOut.model_validate(result)


@router.post("/{workspace_id}/cross-corpus-qa", response_model=CrossCorpusQAOut)
def cross_corpus_qa(
    workspace_id: str, payload: CrossCorpusQARequest, session: SessionDep
) -> CrossCorpusQAOut:
    """Answer one question over public filings + confidential data room, labeling each citation."""
    try:
        result = cross_corpus_qa_service.answer(session, workspace_id, payload.question)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if payload.grounded:
        # G54: consent is resolved server-side, never from the request. A restricted
        # classification or missing workspace consent keeps every quote — confidential ones
        # above all — inside the box: the provider is never constructed.
        ws = get_workspace_or_404(session, workspace_id)
        external_allowed = ws.external_llm_allowed and ws.data_classification != "restricted"
        result = cross_corpus_qa_service.maybe_synthesize_cross_corpus(
            result, external_allowed=external_allowed
        )
    return CrossCorpusQAOut.model_validate(result)
