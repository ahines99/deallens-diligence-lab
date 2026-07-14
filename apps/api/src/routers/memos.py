from __future__ import annotations

from fastapi import APIRouter

from src.routers.deps import SessionDep
from src.schemas.memo import MemoFaithfulnessReport, MemoOut
from src.services import memo_generation_service

router = APIRouter(prefix="/api/workspaces", tags=["memo"])


@router.post("/{workspace_id}/memo/generate", response_model=MemoOut)
def generate_memo(workspace_id: str, session: SessionDep) -> MemoOut:
    return MemoOut.model_validate(memo_generation_service.generate_ic_memo(session, workspace_id))


@router.get("/{workspace_id}/memo", response_model=MemoOut)
def get_memo(workspace_id: str, session: SessionDep) -> MemoOut:
    return MemoOut.model_validate(memo_generation_service.get_ic_memo(session, workspace_id))


@router.get("/{workspace_id}/memo/faithfulness", response_model=MemoFaithfulnessReport)
def get_memo_faithfulness(workspace_id: str, session: SessionDep) -> MemoFaithfulnessReport:
    return MemoFaithfulnessReport.model_validate(
        memo_generation_service.faithfulness_report(session, workspace_id)
    )
