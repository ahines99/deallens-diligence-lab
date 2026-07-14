"""Routes for private target data, immutable sources, and QoE review."""
from __future__ import annotations

import hashlib
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile, status

from src.routers.deps import SessionDep
from src.schemas.target import TargetOut
from src.schemas.underwriting_data import (
    AccountMappingCreate,
    AccountMappingOut,
    AnalysisRunCreate,
    AnalysisRunOut,
    ArtifactVersionCreate,
    ArtifactVersionOut,
    CanonicalFinancialFactOut,
    FinancialImportCreate,
    FinancialImportExceptionOut,
    FinancialImportExceptionResolution,
    FinancialImportPreview,
    FinancialImportResult,
    FinancialReconciliationOut,
    PrivateTargetCreate,
    QoEAdjustmentCreate,
    QoEAdjustmentDecision,
    QoEAdjustmentOut,
    QoEBridgeOut,
    SourceSnapshotCreate,
    SourceSnapshotOut,
)
from src.services import underwriting_data_service as service


router = APIRouter(prefix="/api/workspaces", tags=["underwriting-data"])
MAX_CSV_BYTES = 10 * 1024 * 1024
XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ALLOWED_XLSX_CONTENT_TYPES = {XLSX_CONTENT_TYPE, "application/octet-stream", "application/zip"}


def _verified_actor(
    request: Request,
    header_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
) -> str:
    principal = getattr(request.state, "principal", None)
    return principal.user_id if principal is not None else (header_actor_id or "system")


ActorDep = Annotated[str, Depends(_verified_actor)]


def _translate_error(exc: service.UnderwritingDataError) -> HTTPException:
    code = status.HTTP_409_CONFLICT if isinstance(exc, service.UnderwritingDataConflict) else 422
    return HTTPException(status_code=code, detail=str(exc))


def _safe_upload_filename(filename: str | None, suffix: str) -> str:
    name = (filename or "").strip()
    if (
        not name
        or len(name) > 260
        or any(character in name for character in ("/", "\\", "\x00", ":"))
        or any(ord(character) < 32 for character in name)
    ):
        raise HTTPException(status_code=415, detail="Upload filename is unsafe")
    if not name.casefold().endswith(suffix.casefold()):
        raise HTTPException(status_code=415, detail=f"Upload filename must end with {suffix}")
    return name


@router.post(
    "/{workspace_id}/underwriting/private-target",
    response_model=TargetOut,
    status_code=status.HTTP_201_CREATED,
)
def create_private_target(
    workspace_id: str, payload: PrivateTargetCreate, session: SessionDep
) -> TargetOut:
    try:
        target = service.create_private_target(session, workspace_id, payload)
    except service.UnderwritingDataError as exc:
        raise _translate_error(exc) from exc
    return TargetOut.model_validate(target)


@router.post(
    "/{workspace_id}/underwriting/sources",
    response_model=SourceSnapshotOut,
    status_code=status.HTTP_201_CREATED,
)
def register_source(
    workspace_id: str, payload: SourceSnapshotCreate, session: SessionDep, actor_id: ActorDep
) -> SourceSnapshotOut:
    try:
        snapshot = service.register_source_snapshot(
            session, workspace_id, payload, actor_id=actor_id
        )
    except service.UnderwritingDataError as exc:
        raise _translate_error(exc) from exc
    return SourceSnapshotOut.model_validate(snapshot)


@router.get(
    "/{workspace_id}/underwriting/sources", response_model=list[SourceSnapshotOut]
)
def list_sources(workspace_id: str, session: SessionDep) -> list[SourceSnapshotOut]:
    return [
        SourceSnapshotOut.model_validate(snapshot)
        for snapshot in service.list_source_snapshots(session, workspace_id)
    ]


@router.post(
    "/{workspace_id}/underwriting/account-mappings",
    response_model=AccountMappingOut,
    status_code=status.HTTP_201_CREATED,
)
def create_account_mapping(
    workspace_id: str,
    payload: AccountMappingCreate,
    session: SessionDep,
    actor_id: ActorDep,
) -> AccountMappingOut:
    payload = payload.model_copy(
        update={
            "created_by": actor_id,
            "approved_by": actor_id if payload.status == "approved" else None,
        }
    )
    try:
        mapping = service.create_account_mapping(session, workspace_id, payload)
    except service.UnderwritingDataError as exc:
        raise _translate_error(exc) from exc
    return AccountMappingOut.model_validate(mapping)


@router.get(
    "/{workspace_id}/underwriting/account-mappings",
    response_model=list[AccountMappingOut],
)
def list_account_mappings(workspace_id: str, session: SessionDep) -> list[AccountMappingOut]:
    return [
        AccountMappingOut.model_validate(mapping)
        for mapping in service.list_account_mappings(session, workspace_id)
    ]


@router.post(
    "/{workspace_id}/underwriting/financial-imports",
    response_model=FinancialImportResult,
    status_code=status.HTTP_201_CREATED,
)
def import_financial_rows(
    workspace_id: str,
    payload: FinancialImportCreate,
    session: SessionDep,
    actor_id: ActorDep,
) -> FinancialImportResult:
    payload = payload.model_copy(update={"created_by": actor_id})
    try:
        result = service.import_financial_rows(
            session, workspace_id, payload, actor_id=actor_id
        )
    except service.UnderwritingDataError as exc:
        raise _translate_error(exc) from exc
    return FinancialImportResult.model_validate(result)


@router.post(
    "/{workspace_id}/underwriting/financial-imports/preview",
    response_model=FinancialImportPreview,
)
def preview_financial_rows(
    workspace_id: str,
    payload: FinancialImportCreate,
    session: SessionDep,
    actor_id: ActorDep,
) -> FinancialImportPreview:
    payload = payload.model_copy(update={"created_by": actor_id})
    try:
        result = service.preview_financial_rows(session, workspace_id, payload)
    except service.UnderwritingDataError as exc:
        raise _translate_error(exc) from exc
    return FinancialImportPreview.model_validate(result)


@router.post(
    "/{workspace_id}/underwriting/financial-imports/csv",
    response_model=FinancialImportResult,
    status_code=status.HTTP_201_CREATED,
)
async def import_financial_csv(
    workspace_id: str,
    session: SessionDep,
    actor_id: ActorDep,
    file: UploadFile = File(...),
    source_name: str | None = Form(default=None),
    source_type: str = Form(default="management_financials"),
    created_by: str = Form(default="system"),
    reconciliation_tolerance_bps: str = Form(default="50"),
) -> FinancialImportResult:
    content = await file.read(MAX_CSV_BYTES + 1)
    if len(content) > MAX_CSV_BYTES:
        raise HTTPException(status_code=413, detail="CSV exceeds the 10 MiB upload limit")
    filename = file.filename or "financials.csv"
    if not filename.casefold().endswith(".csv"):
        raise HTTPException(status_code=415, detail="Only CSV uploads are supported by this endpoint")
    try:
        rows = service.parse_financial_csv(content, filename)
        payload = FinancialImportCreate(
            source_name=source_name or filename,
            source_type=source_type,
            filename=filename,
            content_type=file.content_type or "text/csv",
            rows=rows,
            reconciliation_tolerance_bps=reconciliation_tolerance_bps,
            created_by=actor_id,
        )
        result = service.import_financial_rows(
            session,
            workspace_id,
            payload,
            raw_input_hash=hashlib.sha256(content).hexdigest(),
            byte_size=len(content),
            actor_id=actor_id,
        )
    except service.UnderwritingDataError as exc:
        raise _translate_error(exc) from exc
    return FinancialImportResult.model_validate(result)


@router.post(
    "/{workspace_id}/underwriting/financial-imports/xlsx",
    response_model=FinancialImportResult,
    status_code=status.HTTP_201_CREATED,
)
async def import_financial_xlsx(
    workspace_id: str,
    session: SessionDep,
    actor_id: ActorDep,
    file: UploadFile = File(...),
    source_name: str | None = Form(default=None),
    source_type: str = Form(default="management_financials"),
    created_by: str = Form(default="system"),
    reconciliation_tolerance_bps: str = Form(default="50"),
) -> FinancialImportResult:
    filename = _safe_upload_filename(file.filename, ".xlsx")
    content_type = (file.content_type or "").casefold()
    if content_type and content_type not in ALLOWED_XLSX_CONTENT_TYPES:
        raise HTTPException(status_code=415, detail="Upload content type is not XLSX")
    content = await file.read(service.MAX_XLSX_BYTES + 1)
    if len(content) > service.MAX_XLSX_BYTES:
        raise HTTPException(status_code=413, detail="XLSX exceeds the 20 MiB upload limit")
    try:
        rows = service.parse_financial_xlsx(content, filename)
        payload = FinancialImportCreate(
            source_name=source_name or filename,
            source_type=source_type,
            filename=filename,
            content_type=XLSX_CONTENT_TYPE,
            rows=rows,
            source_metadata={
                "adapter": "normalized_xlsx",
                "template_version": "normalized-financials-v1",
                "worksheet": rows[0].source_sheet,
            },
            reconciliation_tolerance_bps=reconciliation_tolerance_bps,
            created_by=actor_id,
        )
        result = service.import_financial_rows(
            session,
            workspace_id,
            payload,
            raw_input_hash=hashlib.sha256(content).hexdigest(),
            byte_size=len(content),
            actor_id=actor_id,
        )
    except service.UnderwritingDataError as exc:
        raise _translate_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return FinancialImportResult.model_validate(result)


@router.get(
    "/{workspace_id}/underwriting/financial-facts",
    response_model=list[CanonicalFinancialFactOut],
)
def list_financial_facts(
    workspace_id: str,
    session: SessionDep,
    source_snapshot_id: str | None = None,
    canonical_account: str | None = None,
    period_end: date | None = None,
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
) -> list[CanonicalFinancialFactOut]:
    return [
        CanonicalFinancialFactOut.model_validate(fact)
        for fact in service.list_financial_facts(
            session,
            workspace_id,
            source_snapshot_id=source_snapshot_id,
            canonical_account=canonical_account,
            period_end=period_end,
            limit=limit,
            offset=offset,
        )
    ]


@router.get(
    "/{workspace_id}/underwriting/import-exceptions",
    response_model=list[FinancialImportExceptionOut],
)
def list_import_exceptions(
    workspace_id: str,
    session: SessionDep,
    source_snapshot_id: str | None = None,
) -> list[FinancialImportExceptionOut]:
    return [
        FinancialImportExceptionOut.model_validate(item)
        for item in service.list_import_exceptions(session, workspace_id, source_snapshot_id)
    ]


@router.post(
    "/{workspace_id}/underwriting/import-exceptions/{exception_id}/resolve",
    response_model=FinancialImportExceptionOut,
)
def resolve_import_exception(
    workspace_id: str,
    exception_id: str,
    payload: FinancialImportExceptionResolution,
    session: SessionDep,
    actor_id: ActorDep,
) -> FinancialImportExceptionOut:
    try:
        item = service.resolve_import_exception(
            session,
            workspace_id,
            exception_id,
            resolved_by=actor_id,
        )
    except service.UnderwritingDataError as exc:
        raise _translate_error(exc) from exc
    return FinancialImportExceptionOut.model_validate(item)


@router.get(
    "/{workspace_id}/underwriting/reconciliations",
    response_model=list[FinancialReconciliationOut],
)
def list_reconciliations(
    workspace_id: str,
    session: SessionDep,
    source_snapshot_id: str | None = None,
) -> list[FinancialReconciliationOut]:
    return [
        FinancialReconciliationOut.model_validate(item)
        for item in service.list_reconciliations(session, workspace_id, source_snapshot_id)
    ]


@router.post(
    "/{workspace_id}/underwriting/qoe-adjustments",
    response_model=QoEAdjustmentOut,
    status_code=status.HTTP_201_CREATED,
)
def create_qoe_adjustment(
    workspace_id: str,
    payload: QoEAdjustmentCreate,
    session: SessionDep,
    actor_id: ActorDep,
) -> QoEAdjustmentOut:
    payload = payload.model_copy(update={"created_by": actor_id})
    try:
        adjustment = service.create_qoe_adjustment(session, workspace_id, payload)
    except service.UnderwritingDataError as exc:
        raise _translate_error(exc) from exc
    return QoEAdjustmentOut.model_validate(adjustment)


@router.get(
    "/{workspace_id}/underwriting/qoe-adjustments",
    response_model=list[QoEAdjustmentOut],
)
def list_qoe_adjustments(
    workspace_id: str, session: SessionDep, period_end: date | None = None
) -> list[QoEAdjustmentOut]:
    return [
        QoEAdjustmentOut.model_validate(adjustment)
        for adjustment in service.list_qoe_adjustments(session, workspace_id, period_end)
    ]


@router.post(
    "/{workspace_id}/underwriting/qoe-adjustments/{adjustment_id}/decision",
    response_model=QoEAdjustmentOut,
)
def decide_qoe_adjustment(
    workspace_id: str,
    adjustment_id: str,
    payload: QoEAdjustmentDecision,
    session: SessionDep,
    actor_id: ActorDep,
) -> QoEAdjustmentOut:
    payload = payload.model_copy(update={"decided_by": actor_id})
    try:
        adjustment = service.decide_qoe_adjustment(
            session, workspace_id, adjustment_id, payload
        )
    except service.UnderwritingDataError as exc:
        raise _translate_error(exc) from exc
    return QoEAdjustmentOut.model_validate(adjustment)


@router.get("/{workspace_id}/underwriting/qoe-bridge", response_model=QoEBridgeOut)
def get_qoe_bridge(
    workspace_id: str,
    session: SessionDep,
    period_end: date | None = None,
    source_snapshot_id: str | None = None,
) -> QoEBridgeOut:
    try:
        result = service.get_qoe_bridge(
            session,
            workspace_id,
            period_end=period_end,
            source_snapshot_id=source_snapshot_id,
        )
    except service.UnderwritingDataError as exc:
        raise _translate_error(exc) from exc
    return QoEBridgeOut.model_validate(result)


@router.post(
    "/{workspace_id}/underwriting/analysis-runs",
    response_model=AnalysisRunOut,
    status_code=status.HTTP_201_CREATED,
)
def create_analysis_run(
    workspace_id: str,
    payload: AnalysisRunCreate,
    session: SessionDep,
    actor_id: ActorDep,
) -> AnalysisRunOut:
    payload = payload.model_copy(update={"created_by": actor_id})
    try:
        run = service.create_analysis_run(session, workspace_id, payload)
    except service.UnderwritingDataError as exc:
        raise _translate_error(exc) from exc
    return AnalysisRunOut.model_validate(run)


@router.get(
    "/{workspace_id}/underwriting/analysis-runs", response_model=list[AnalysisRunOut]
)
def list_analysis_runs(workspace_id: str, session: SessionDep) -> list[AnalysisRunOut]:
    return [
        AnalysisRunOut.model_validate(run)
        for run in service.list_analysis_runs(session, workspace_id)
    ]


@router.post(
    "/{workspace_id}/underwriting/artifact-versions",
    response_model=ArtifactVersionOut,
    status_code=status.HTTP_201_CREATED,
)
def create_artifact_version(
    workspace_id: str,
    payload: ArtifactVersionCreate,
    session: SessionDep,
    actor_id: ActorDep,
) -> ArtifactVersionOut:
    payload = payload.model_copy(update={"created_by": actor_id})
    try:
        artifact = service.create_artifact_version(session, workspace_id, payload)
    except service.UnderwritingDataError as exc:
        raise _translate_error(exc) from exc
    return ArtifactVersionOut.model_validate(artifact)


@router.get(
    "/{workspace_id}/underwriting/artifact-versions",
    response_model=list[ArtifactVersionOut],
)
def list_artifact_versions(
    workspace_id: str, session: SessionDep
) -> list[ArtifactVersionOut]:
    return [
        ArtifactVersionOut.model_validate(artifact)
        for artifact in service.list_artifact_versions(session, workspace_id)
    ]


__all__ = ["router"]
