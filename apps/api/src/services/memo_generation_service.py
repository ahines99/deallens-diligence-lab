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


def faithfulness_report(session: Session, workspace_id: str) -> dict:
    """Runtime faithfulness diagnostics: do the memos' citations resolve, and are
    numeric claims citation-bound? Read-only — the enforcement itself lives in the
    citation auditor and the analysis pipeline; this makes it inspectable."""
    from datetime import datetime, timezone
    import re

    from src.agents.citation_auditor import CitationAuditor
    from src.models import Evidence, RedTeamReport
    from src.services.common import get_workspace_or_404

    get_workspace_or_404(session, workspace_id)
    known_refs = set(
        session.scalars(select(Evidence.ref).where(Evidence.workspace_id == workspace_id))
    )

    documents: list[tuple[str, str]] = [
        (memo.memo_type, memo.markdown_content)
        for memo in session.scalars(select(Memo).where(Memo.workspace_id == workspace_id))
    ]
    red_team = session.scalar(
        select(RedTeamReport).where(RedTeamReport.workspace_id == workspace_id)
    )
    if red_team is not None and red_team.bear_case_markdown:
        documents.append(("red_team_bear_case", red_team.bear_case_markdown))

    reports = []
    for document_type, markdown in documents:
        sequence = CitationAuditor.extract_ref_sequence(markdown)
        cited = set(sequence)
        unresolved = sorted(CitationAuditor.find_uncited(cited, known_refs))
        numeric_tokens = CitationAuditor.extract_numeric_tokens(markdown)
        # Numeric prose sentences that carry no citation. Table/heading rows are the
        # memo's own formatting, not claims, so they are excluded.
        uncited_numeric_sentences: list[str] = []
        for segment in re.split(r"(?<=[.!?])\s+|\n+", markdown or ""):
            stripped = segment.strip()
            if not stripped or stripped.startswith(("#", "|", "-", "*", ">")):
                continue
            if CitationAuditor.extract_ref_sequence(stripped):
                continue
            if CitationAuditor.extract_numeric_tokens(stripped):
                uncited_numeric_sentences.append(stripped[:300])
        reports.append(
            {
                "document_type": document_type,
                "citation_count": len(sequence),
                "distinct_refs": len(cited),
                "unresolved_refs": unresolved,
                "numeric_token_count": sum(numeric_tokens.values()),
                "uncited_numeric_sentences": uncited_numeric_sentences[:10],
                "uncited_numeric_sentence_count": len(uncited_numeric_sentences),
                "fully_resolved": not unresolved,
            }
        )
    return {
        "workspace_id": workspace_id,
        "evidence_ref_count": len(known_refs),
        "documents": reports,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
