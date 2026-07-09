"""Evidence & audit layer. Allocates stable refs and exposes citation lookups.

Every material claim in a generated artifact is backed by an Evidence row created here, so the
`EV-###` refs that risks/questions/memos cite always resolve. Refs are allocated sequentially per
workspace; a full re-analysis clears and rebuilds them deterministically.
"""
from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from src.models import Evidence


def next_ref(session: Session, workspace_id: str) -> str:
    n = session.scalar(
        select(func.count()).select_from(Evidence).where(Evidence.workspace_id == workspace_id)
    ) or 0
    return f"EV-{n + 1:03d}"


def create(
    session: Session,
    workspace_id: str,
    *,
    claim: str,
    claim_type: str,
    source_name: str,
    source_type: str,
    evidence_text: str,
    confidence: float,
    agent_name: str,
    source_url: str | None = None,
    source_date: str | None = None,
    source_section: str | None = None,
) -> Evidence:
    ev = Evidence(
        workspace_id=workspace_id,
        ref=next_ref(session, workspace_id),
        claim=claim,
        claim_type=claim_type,
        source_name=source_name,
        source_type=source_type,
        source_url=source_url,
        source_date=source_date,
        source_section=source_section,
        evidence_text=evidence_text,
        confidence=round(float(confidence), 3),
        agent_name=agent_name,
    )
    session.add(ev)
    session.flush()
    return ev


def clear(session: Session, workspace_id: str) -> None:
    session.execute(delete(Evidence).where(Evidence.workspace_id == workspace_id))


def list_evidence(session: Session, workspace_id: str) -> list[Evidence]:
    return list(
        session.scalars(
            select(Evidence).where(Evidence.workspace_id == workspace_id).order_by(Evidence.ref)
        )
    )


def known_refs(session: Session, workspace_id: str) -> set[str]:
    return {
        e.ref
        for e in session.scalars(select(Evidence).where(Evidence.workspace_id == workspace_id))
    }
