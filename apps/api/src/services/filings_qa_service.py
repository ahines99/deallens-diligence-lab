"""Cited, abstaining Q&A over a workspace's ingested SEC filings.

Every answer is extractive: verbatim sentences from real 10-K sections, each carrying a
citation back to the filing and its sec.gov document. When the filings do not contain
lexical evidence for the question, the service abstains — it never composes an answer
from nothing. Retrieval is deterministic BM25 (see retrieval_service), so identical
inputs produce identical answers.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models import Filing
from src.services import retrieval_service, textkit
from src.services.common import get_workspace_or_404

ABSTENTION = (
    "The ingested filings do not contain enough evidence to answer this question. "
    "No answer was fabricated."
)

_MAX_SENTENCES = 3
_MAX_QUOTE_CHARS = 700

# Retrieval, ranking, and quote-splitting all go through textkit so this Q&A can never
# disagree with the BM25 layer about tokens or sentence boundaries (decimal-safe quotes).
_TOKENS = textkit.tokens
_MAX_QUESTION_CHARS = 2_000
# Below this fraction of the question's terms covered, the answer is labeled "partial" so a
# single-term lexical match never presents as a confident full answer.
_PARTIAL_COVERAGE_THRESHOLD = 0.5


def ask(session: Session, workspace_id: str, question: str, k: int = 6) -> dict:
    get_workspace_or_404(session, workspace_id)
    question = (question or "").strip()
    if not question:
        raise ValueError("Ask a question about the ingested filings.")
    if len(question) > _MAX_QUESTION_CHARS:
        raise ValueError(f"Question must be at most {_MAX_QUESTION_CHARS} characters.")

    retrieved = retrieval_service.retrieve(session, workspace_id, question, k=k)
    question_terms = set(_TOKENS(question))

    # Sentence-level candidates within the BM25-selected chunks.
    candidates: list[tuple[float, retrieval_service.RetrievedChunk, str, set[str]]] = []
    for item in retrieved:
        for sentence in textkit.sentences(item.chunk.chunk_text or ""):
            if len(sentence) < 40:
                continue
            overlap = question_terms & set(_TOKENS(sentence))
            if not overlap:
                continue
            coverage = len(overlap) / max(len(question_terms), 1)
            score = item.score + len(overlap) * 2 + coverage
            candidates.append((score, item, sentence[:_MAX_QUOTE_CHARS], overlap))

    generated_at = datetime.now(timezone.utc).isoformat()
    base = {
        "workspace_id": workspace_id,
        "question": question,
        "method": "extractive_bm25",
        "generated_at": generated_at,
    }
    if not candidates:
        return {
            **base,
            "status": "abstained",
            "answer": ABSTENTION,
            "citations": [],
            "retrieval": {
                "chunks_considered": len(retrieved),
                "matched_terms": [],
                "coverage": 0.0,
                "abstention_reason": "no filing sentence shares terms with the question",
            },
        }

    # Greedy coverage: each added sentence must cover a question term the others did not.
    remaining = set(question_terms)
    selected: list[tuple[float, retrieval_service.RetrievedChunk, str, set[str]]] = []
    pool = sorted(candidates, key=lambda c: c[0], reverse=True)
    while pool and len(selected) < _MAX_SENTENCES:
        best = max(pool, key=lambda c: (len(c[3] & remaining), c[0]))
        if not (best[3] & remaining):
            break
        selected.append(best)
        remaining -= best[3]
        pool.remove(best)

    filings = {
        f.id: f
        for f in session.scalars(
            select(Filing).where(Filing.id.in_({item[1].chunk.filing_id for item in selected}))
        )
    }
    citations = []
    for _, item, sentence, _ in selected:
        filing = filings.get(item.chunk.filing_id)
        citations.append(
            {
                "filing_id": item.chunk.filing_id,
                "form_type": filing.form_type if filing else None,
                "filing_date": filing.filing_date if filing else None,
                "section": item.chunk.section,
                "document_url": filing.document_url if filing else None,
                "quote": sentence,
                "retrieval_score": item.score,
            }
        )
    matched = sorted(set().union(*(item[3] for item in selected)))
    coverage = len(matched) / max(len(question_terms), 1)
    # "answered" only when the answer covers a majority of the question's terms; a thin
    # single-term match is surfaced as "partial" so the UI can flag low confidence rather
    # than present it as a complete answer (citations still resolve either way).
    status = "answered" if coverage >= _PARTIAL_COVERAGE_THRESHOLD else "partial"
    return {
        **base,
        "status": status,
        "answer": " ".join(item[2] for item in selected),
        "citations": citations,
        "retrieval": {
            "chunks_considered": len(retrieved),
            "matched_terms": matched,
            "coverage": round(coverage, 3),
            "abstention_reason": None,
        },
    }
