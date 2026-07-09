"""IC memo — read persisted memo; (re)build via the full analysis pass."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models import Memo
from src.services import analysis_service
from src.services.common import NotFound


def generate_ic_memo(session: Session, workspace_id: str) -> Memo:
    analysis_service.run_full_analysis(session, workspace_id)
    return get_ic_memo(session, workspace_id)


def get_ic_memo(session: Session, workspace_id: str) -> Memo:
    memo = session.scalar(
        select(Memo).where(Memo.workspace_id == workspace_id, Memo.memo_type == "ic_memo")
    )
    if memo is None:
        raise NotFound("IC memo not generated yet.")
    return memo
