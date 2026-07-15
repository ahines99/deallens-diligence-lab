"""HTTP surface for the G47 memo-redline diff of two analysis runs."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from src.routers.deps import SessionDep
from src.schemas.memo_redline import MemoRedlineOut
from src.services import memo_redline_service

router = APIRouter(prefix="/api", tags=["memo redline"])


@router.get("/workspaces/{workspace_id}/memo-redline", response_model=MemoRedlineOut)
def get_memo_redline(
    workspace_id: str,
    session: SessionDep,
    run_a: str = Query(..., max_length=32),
    run_b: str = Query(..., max_length=32),
) -> MemoRedlineOut:
    """Side-by-side diff of two analysis runs' sealed memo content with changed-claim highlighting."""
    try:
        result = memo_redline_service.diff_runs(session, workspace_id, run_a, run_b)
    except memo_redline_service.MemoRedlineError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return MemoRedlineOut.model_validate(result)


__all__ = ["router"]
