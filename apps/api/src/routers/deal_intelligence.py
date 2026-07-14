"""HTTP API for versioned deal-room documents and evidence intelligence."""
from __future__ import annotations

import json
from typing import Annotated, Any, Callable, TypeVar

from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile

from src.routers.deal_workflow import ActorDep
from src.routers.deps import SessionDep
from src.schemas.deal_intelligence import (
    CitedQARequest,
    CitedQARunOut,
    ClaimCollectionOut,
    ClaimHistoryOut,
    ClaimReviewOut,
    ClaimReviewRequest,
    ClaimReviewResult,
    ComparisonRequest,
    DataRoomChunkOut,
    DataRoomDocumentOut,
    DocumentComparisonOut,
    DocumentTextCreate,
    EvaluationRequest,
    ExtractionRequest,
    IntelligenceEvaluationOut,
    SecFilingComparisonOut,
    SecFilingComparisonRequest,
    StructuredClaimOut,
)
from src.services import deal_intelligence_service as service

router = APIRouter(prefix="/api", tags=["deal intelligence"])
T = TypeVar("T")


def _call(function: Callable[..., T], *args, **kwargs) -> T:
    try:
        return function(*args, **kwargs)
    except service.IntelligenceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post(
    "/deals/{deal_id}/intelligence/documents",
    response_model=DataRoomDocumentOut,
    status_code=201,
)
def ingest_text_document(
    deal_id: str,
    payload: DocumentTextCreate,
    session: SessionDep,
    actor: ActorDep,
) -> DataRoomDocumentOut:
    document = _call(service.ingest_text_document, session, deal_id, payload, actor)
    return DataRoomDocumentOut.model_validate(document)


@router.post(
    "/deals/{deal_id}/intelligence/documents/upload",
    response_model=DataRoomDocumentOut,
    status_code=201,
)
async def upload_document(
    deal_id: str,
    session: SessionDep,
    actor: ActorDep,
    file: Annotated[UploadFile, File()],
    title: Annotated[str | None, Form(max_length=240)] = None,
    logical_document_id: Annotated[str | None, Form(max_length=32)] = None,
    metadata_json: Annotated[str | None, Form()] = None,
) -> DataRoomDocumentOut:
    try:
        content = await file.read(service.MAX_DOCUMENT_BYTES + 1)
    finally:
        await file.close()
    metadata: dict[str, Any] = {}
    if metadata_json:
        try:
            decoded = json.loads(metadata_json)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=422, detail="metadata_json must be valid JSON") from exc
        if not isinstance(decoded, dict):
            raise HTTPException(status_code=422, detail="metadata_json must be a JSON object")
        metadata = decoded
    document = _call(
        service.ingest_document,
        session,
        deal_id,
        filename=file.filename or "upload",
        content=content,
        content_type=file.content_type,
        title=title,
        logical_document_id=logical_document_id,
        document_metadata=metadata,
        actor=actor,
    )
    return DataRoomDocumentOut.model_validate(document)


@router.get(
    "/deals/{deal_id}/intelligence/documents", response_model=list[DataRoomDocumentOut]
)
def list_documents(
    deal_id: str,
    session: SessionDep,
    actor: ActorDep,
    latest_only: bool = True,
    logical_document_id: str | None = None,
) -> list[DataRoomDocumentOut]:
    return [
        DataRoomDocumentOut.model_validate(item)
        for item in _call(
            service.list_documents,
            session,
            deal_id,
            actor,
            latest_only=latest_only,
            logical_document_id=logical_document_id,
        )
    ]


@router.get(
    "/deals/{deal_id}/intelligence/documents/{logical_document_id}/versions",
    response_model=list[DataRoomDocumentOut],
)
def list_document_versions(
    deal_id: str,
    logical_document_id: str,
    session: SessionDep,
    actor: ActorDep,
) -> list[DataRoomDocumentOut]:
    return [
        DataRoomDocumentOut.model_validate(item)
        for item in _call(
            service.list_document_versions,
            session,
            deal_id,
            logical_document_id,
            actor,
        )
    ]


@router.get(
    "/intelligence/documents/{document_id}", response_model=DataRoomDocumentOut
)
def get_document(
    document_id: str, session: SessionDep, actor: ActorDep
) -> DataRoomDocumentOut:
    return DataRoomDocumentOut.model_validate(
        _call(service.get_document, session, document_id, actor)
    )


@router.get(
    "/intelligence/documents/{document_id}/chunks", response_model=list[DataRoomChunkOut]
)
def list_chunks(
    document_id: str, session: SessionDep, actor: ActorDep
) -> list[DataRoomChunkOut]:
    return [
        DataRoomChunkOut.model_validate(item)
        for item in _call(service.list_chunks, session, document_id, actor)
    ]


@router.get("/intelligence/documents/{document_id}/download")
def download_document(document_id: str, session: SessionDep, actor: ActorDep) -> Response:
    document = _call(service.get_document, session, document_id, actor)
    return Response(
        content=document.raw_bytes,
        media_type=document.content_type,
        headers={"Content-Disposition": f'attachment; filename="{document.filename}"'},
    )


@router.post(
    "/deals/{deal_id}/intelligence/qa", response_model=CitedQARunOut, status_code=201
)
def answer_question(
    deal_id: str,
    payload: CitedQARequest,
    session: SessionDep,
    actor: ActorDep,
) -> CitedQARunOut:
    return CitedQARunOut.model_validate(
        _call(service.answer_question, session, deal_id, payload, actor)
    )


@router.get(
    "/deals/{deal_id}/intelligence/qa-runs", response_model=list[CitedQARunOut]
)
def list_qa_runs(
    deal_id: str,
    session: SessionDep,
    actor: ActorDep,
    limit: Annotated[int, Query(ge=1, le=1_000)] = 100,
) -> list[CitedQARunOut]:
    return [
        CitedQARunOut.model_validate(item)
        for item in _call(service.list_qa_runs, session, deal_id, actor, limit=limit)
    ]


@router.post(
    "/deals/{deal_id}/intelligence/extractions",
    response_model=list[StructuredClaimOut],
    status_code=201,
)
def extract_structured_claims(
    deal_id: str,
    payload: ExtractionRequest,
    session: SessionDep,
    actor: ActorDep,
) -> list[StructuredClaimOut]:
    return [
        StructuredClaimOut.model_validate(item)
        for item in _call(service.extract_structured_claims, session, deal_id, payload, actor)
    ]


@router.get(
    "/deals/{deal_id}/intelligence/claims", response_model=ClaimCollectionOut
)
def list_current_claims(
    deal_id: str, session: SessionDep, actor: ActorDep
) -> ClaimCollectionOut:
    grouped = _call(service.list_current_claims, session, deal_id, actor)
    approved = [StructuredClaimOut.model_validate(item) for item in grouped["approved"]]
    pending = [StructuredClaimOut.model_validate(item) for item in grouped["pending"]]
    rejected = [StructuredClaimOut.model_validate(item) for item in grouped["rejected"]]
    return ClaimCollectionOut(
        approved=approved,
        pending=pending,
        rejected=rejected,
        counts={
            "approved": len(approved),
            "pending": len(pending),
            "rejected": len(rejected),
        },
    )


@router.post(
    "/intelligence/claims/{claim_id}/review", response_model=ClaimReviewResult
)
def review_claim(
    claim_id: str,
    payload: ClaimReviewRequest,
    session: SessionDep,
    actor: ActorDep,
) -> ClaimReviewResult:
    claim, review = _call(service.review_claim, session, claim_id, payload, actor)
    return ClaimReviewResult(
        claim=StructuredClaimOut.model_validate(claim),
        review=ClaimReviewOut.model_validate(review),
    )


@router.get(
    "/intelligence/claims/{logical_claim_id}/history", response_model=ClaimHistoryOut
)
def claim_history(
    logical_claim_id: str, session: SessionDep, actor: ActorDep
) -> ClaimHistoryOut:
    revisions, reviews = _call(service.claim_history, session, logical_claim_id, actor)
    return ClaimHistoryOut(
        logical_claim_id=logical_claim_id,
        revisions=[StructuredClaimOut.model_validate(item) for item in revisions],
        reviews=[ClaimReviewOut.model_validate(item) for item in reviews],
    )


@router.post(
    "/deals/{deal_id}/intelligence/comparisons",
    response_model=DocumentComparisonOut,
    status_code=201,
)
def compare_documents(
    deal_id: str,
    payload: ComparisonRequest,
    session: SessionDep,
    actor: ActorDep,
) -> DocumentComparisonOut:
    return DocumentComparisonOut.model_validate(
        _call(service.compare_documents, session, deal_id, payload, actor)
    )


@router.get(
    "/deals/{deal_id}/intelligence/comparisons",
    response_model=list[DocumentComparisonOut],
)
def list_comparisons(
    deal_id: str,
    session: SessionDep,
    actor: ActorDep,
    limit: Annotated[int, Query(ge=1, le=1_000)] = 100,
) -> list[DocumentComparisonOut]:
    return [
        DocumentComparisonOut.model_validate(item)
        for item in _call(service.list_comparisons, session, deal_id, actor, limit=limit)
    ]


@router.post(
    "/workspaces/{workspace_id}/intelligence/sec-comparisons",
    response_model=SecFilingComparisonOut,
    status_code=201,
)
def compare_sec_filings(
    workspace_id: str,
    payload: SecFilingComparisonRequest,
    session: SessionDep,
    actor: ActorDep,
) -> SecFilingComparisonOut:
    return SecFilingComparisonOut.model_validate(
        _call(service.compare_sec_filings, session, workspace_id, payload, actor)
    )


@router.get(
    "/workspaces/{workspace_id}/intelligence/sec-comparisons",
    response_model=list[SecFilingComparisonOut],
)
def list_sec_filing_comparisons(
    workspace_id: str,
    session: SessionDep,
    actor: ActorDep,
    limit: Annotated[int, Query(ge=1, le=1_000)] = 100,
) -> list[SecFilingComparisonOut]:
    return [
        SecFilingComparisonOut.model_validate(item)
        for item in _call(
            service.list_sec_filing_comparisons,
            session,
            workspace_id,
            actor,
            limit=limit,
        )
    ]


@router.post(
    "/deals/{deal_id}/intelligence/evaluations",
    response_model=IntelligenceEvaluationOut,
    status_code=201,
)
def run_evaluation(
    deal_id: str,
    payload: EvaluationRequest,
    session: SessionDep,
    actor: ActorDep,
) -> IntelligenceEvaluationOut:
    return IntelligenceEvaluationOut.model_validate(
        _call(service.run_evaluation, session, deal_id, payload, actor)
    )


@router.get(
    "/deals/{deal_id}/intelligence/evaluations",
    response_model=list[IntelligenceEvaluationOut],
)
def list_evaluations(
    deal_id: str,
    session: SessionDep,
    actor: ActorDep,
    limit: Annotated[int, Query(ge=1, le=1_000)] = 100,
) -> list[IntelligenceEvaluationOut]:
    return [
        IntelligenceEvaluationOut.model_validate(item)
        for item in _call(service.list_evaluations, session, deal_id, actor, limit=limit)
    ]
