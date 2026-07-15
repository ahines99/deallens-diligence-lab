"""G45 — Workspace export bundle endpoints (IC memo PDF + evidence appendix + hash manifest)."""
from __future__ import annotations

import io

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import StreamingResponse

from src.routers.deps import SessionDep
from src.schemas.workspace_bundle import BundleVerificationResult
from src.services import workspace_bundle_service

router = APIRouter(prefix="/api/workspaces", tags=["workspace bundle"])


@router.get("/{workspace_id}/export-bundle", response_class=StreamingResponse)
def export_bundle(workspace_id: str, session: SessionDep) -> StreamingResponse:
    bundle = workspace_bundle_service.build_bundle(session, workspace_id)
    return StreamingResponse(
        io.BytesIO(bundle.content),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{bundle.filename}"',
            "X-Bundle-SHA256": bundle.bundle_sha256,
        },
    )


@router.post("/{workspace_id}/export-bundle/verify", response_model=BundleVerificationResult)
def verify_export_bundle(
    workspace_id: str,
    session: SessionDep,
    file: UploadFile | None = File(default=None),
) -> BundleVerificationResult:
    """Verify an uploaded bundle, or (when none is supplied) regenerate one and verify it."""
    if file is not None:
        zip_bytes = file.file.read()
    else:
        zip_bytes = workspace_bundle_service.build_bundle(session, workspace_id).content
    return BundleVerificationResult.model_validate(
        workspace_bundle_service.verify_bundle(zip_bytes)
    )
