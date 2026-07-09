from __future__ import annotations

from fastapi import APIRouter

from src.routers.deps import SessionDep
from src.schemas.question import QuestionOut
from src.services import diligence_question_service
from src.services.common import get_workspace_or_404

router = APIRouter(prefix="/api/workspaces", tags=["questions"])


@router.post("/{workspace_id}/questions/generate", response_model=list[QuestionOut])
def generate_questions(workspace_id: str, session: SessionDep) -> list[QuestionOut]:
    return [
        QuestionOut.model_validate(q)
        for q in diligence_question_service.generate_questions(session, workspace_id)
    ]


@router.get("/{workspace_id}/questions", response_model=list[QuestionOut])
def list_questions(workspace_id: str, session: SessionDep) -> list[QuestionOut]:
    get_workspace_or_404(session, workspace_id)
    return [
        QuestionOut.model_validate(q)
        for q in diligence_question_service.list_questions(session, workspace_id)
    ]
