from __future__ import annotations

from datetime import datetime

from src.schemas.common import MemoType, ORMModel


class MemoOut(ORMModel):
    id: str
    workspace_id: str
    memo_type: MemoType
    title: str
    markdown_content: str
    created_at: datetime
    updated_at: datetime
