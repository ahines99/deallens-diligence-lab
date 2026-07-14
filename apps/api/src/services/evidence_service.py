"""Evidence & audit layer. Allocates stable refs and exposes citation lookups.

Every material claim in a generated artifact is backed by an Evidence row created here, so the
`EV-###` refs that risks/questions/memos cite always resolve. Refs are allocated monotonically per
workspace and are protected by a database uniqueness constraint plus savepoint retry. Historical
evidence is append-only from this service so frozen artifacts never lose a citation target.
"""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.models import Evidence


def next_ref(session: Session, workspace_id: str) -> str:
    """Return the next monotonic candidate; the unique constraint is the concurrency authority."""
    maximum = 0
    for ref in session.scalars(
        select(Evidence.ref).where(Evidence.workspace_id == workspace_id)
    ):
        match = re.fullmatch(r"EV-(\d+)", ref or "")
        if match:
            maximum = max(maximum, int(match.group(1)))
    return f"EV-{maximum + 1:03d}"


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
    for attempt in range(5):
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
        try:
            # A savepoint contains a losing concurrent allocation without rolling back the
            # analysis transaction that may already contain other evidence and artifacts.
            with session.begin_nested():
                session.add(ev)
                session.flush()
            return ev
        except IntegrityError:
            if attempt == 4:
                raise
            session.expire_all()
    raise RuntimeError("Evidence reference allocation exhausted")  # pragma: no cover


def clear(session: Session, workspace_id: str) -> None:
    del session, workspace_id
    raise ValueError("Evidence is append-only; create a new analysis/artifact version instead")


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
