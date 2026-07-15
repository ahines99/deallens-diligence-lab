"""Unified cross-corpus Q&A (G08).

Answers one question over **both** a workspace's public SEC filing chunks (``DocumentChunk``,
ranked by ``retrieval_service``) and, when the workspace is linked to a deal, the **confidential**
data-room chunks (``DataRoomChunk``). Candidate sentences from both corpora are scored on one
shared lexical basis (question-term overlap via ``textkit``) so a public and a confidential
sentence compete fairly, then the same extractive greedy-coverage discipline used by the filings
and data-room Q&A selects up to three verbatim sentences and abstains when neither corpus offers
lexical evidence.

Every citation is labeled with its provenance: ``corpus`` is ``"public_filing"`` or
``"confidential_dataroom"`` and ``confidential`` is the matching boolean, so downstream consumers
can never mistake a confidential data-room quote for a public disclosure. This service stays in the
extractive-cite-or-abstain lane — it never composes a fluent free-text answer (that is G04's job).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models import DataRoomChunk, DataRoomDocument, Deal, Filing
from src.services import retrieval_service, textkit
from src.services.common import get_workspace_or_404

ABSTENTION = (
    "Neither the public filings nor the confidential data room contain enough evidence to "
    "answer this question. No answer was fabricated."
)
METHOD = "cross-corpus-extractive-v1"

_MAX_SENTENCES = 3
_MAX_QUOTE_CHARS = 700
_MIN_SENTENCE_CHARS = 40
_MAX_QUESTION_CHARS = 2_000
_PARTIAL_COVERAGE_THRESHOLD = 0.5

_TOKENS = textkit.tokens

_PUBLIC_LABEL = "Public SEC filing"
_CONFIDENTIAL_LABEL = "Confidential data room"


def _public_candidates(
    session: Session, workspace_id: str, question: str, question_terms: set[str], k: int
) -> tuple[list[tuple], int]:
    """Sentence candidates from the workspace's public filing chunks."""
    if retrieval_service.workspace_has_embeddings(session, workspace_id):
        retrieved = retrieval_service.retrieve_hybrid(session, workspace_id, question, k=k)
    else:
        retrieved = retrieval_service.retrieve(session, workspace_id, question, k=k)
    filings = {
        f.id: f
        for f in session.scalars(
            select(Filing).where(Filing.id.in_({item.chunk.filing_id for item in retrieved}))
        )
    }
    candidates: list[tuple] = []
    for item in retrieved:
        chunk = item.chunk
        filing = filings.get(chunk.filing_id)
        for sentence in textkit.sentences(chunk.chunk_text or ""):
            if len(sentence) < _MIN_SENTENCE_CHARS:
                continue
            overlap = question_terms & set(_TOKENS(sentence))
            if not overlap:
                continue
            coverage = len(overlap) / max(len(question_terms), 1)
            score = len(overlap) * 2 + coverage
            citation = {
                "corpus": "public_filing",
                "confidential": False,
                "label": _PUBLIC_LABEL,
                "quote": sentence[:_MAX_QUOTE_CHARS],
                "source_name": (
                    f"{filing.form_type} filed {filing.filing_date}" if filing else "SEC filing"
                ),
                "provenance": {
                    "filing_id": chunk.filing_id,
                    "form_type": filing.form_type if filing else None,
                    "filing_date": filing.filing_date if filing else None,
                    "section": chunk.section,
                    "chunk_index": chunk.chunk_index,
                    "document_url": chunk.source_url
                    or (filing.document_url if filing else None),
                },
            }
            candidates.append((score, overlap, citation, sentence[:_MAX_QUOTE_CHARS]))
    return candidates, len(retrieved)


def _latest_documents(session: Session, deal_id: str) -> list[DataRoomDocument]:
    """Latest version of every logical data-room document for the deal."""
    documents = session.scalars(
        select(DataRoomDocument)
        .where(DataRoomDocument.deal_id == deal_id)
        .order_by(DataRoomDocument.logical_document_id, DataRoomDocument.version.desc())
    )
    latest: dict[str, DataRoomDocument] = {}
    for document in documents:
        latest.setdefault(document.logical_document_id, document)
    return list(latest.values())


def _confidential_candidates(
    session: Session, deal_id: str, question_terms: set[str]
) -> tuple[list[tuple], int]:
    """Sentence candidates from the deal's confidential data-room chunks."""
    candidates: list[tuple] = []
    chunk_count = 0
    for document in _latest_documents(session, deal_id):
        chunks = session.scalars(
            select(DataRoomChunk)
            .where(DataRoomChunk.document_id == document.id)
            .order_by(DataRoomChunk.ordinal)
        )
        for chunk in chunks:
            chunk_count += 1
            for sentence in textkit.sentences(chunk.text or ""):
                if len(sentence) < _MIN_SENTENCE_CHARS:
                    continue
                overlap = question_terms & set(_TOKENS(sentence))
                if not overlap:
                    continue
                coverage = len(overlap) / max(len(question_terms), 1)
                score = len(overlap) * 2 + coverage
                citation = {
                    "corpus": "confidential_dataroom",
                    "confidential": True,
                    "label": _CONFIDENTIAL_LABEL,
                    "quote": sentence[:_MAX_QUOTE_CHARS],
                    "source_name": f"{document.filename} (v{document.version})",
                    "provenance": {
                        "document_id": document.id,
                        "logical_document_id": document.logical_document_id,
                        "document_version": document.version,
                        "filename": document.filename,
                        "sha256": document.sha256,
                        "chunk_id": chunk.id,
                        "content_hash": chunk.content_hash,
                        "locator": dict(chunk.locator),
                    },
                }
                candidates.append((score, overlap, citation, sentence[:_MAX_QUOTE_CHARS]))
    return candidates, chunk_count


def answer(session: Session, workspace_id: str, question: str, k: int = 6) -> dict:
    """Answer one question over the public filing corpus and (if linked) the confidential data room."""
    get_workspace_or_404(session, workspace_id)
    question = (question or "").strip()
    if not question:
        raise ValueError("Ask a question about the filings and data room.")
    if len(question) > _MAX_QUESTION_CHARS:
        raise ValueError(f"Question must be at most {_MAX_QUESTION_CHARS} characters.")

    question_terms = set(_TOKENS(question))
    deal = session.scalar(select(Deal).where(Deal.workspace_id == workspace_id))

    public, public_chunks = _public_candidates(
        session, workspace_id, question, question_terms, k
    )
    confidential: list[tuple] = []
    confidential_chunks = 0
    if deal is not None:
        confidential, confidential_chunks = _confidential_candidates(
            session, deal.id, question_terms
        )

    corpora = {
        "public_filing": {"available": True, "chunks_considered": public_chunks},
        "confidential_dataroom": {
            "available": deal is not None,
            "chunk_count": confidential_chunks,
        },
    }
    generated_at = datetime.now(timezone.utc).isoformat()
    base = {
        "workspace_id": workspace_id,
        "deal_id": deal.id if deal is not None else None,
        "question": question,
        "method": METHOD,
        "generated_at": generated_at,
        "corpora": corpora,
    }

    pool = public + confidential
    if not pool:
        return {
            **base,
            "status": "abstained",
            "answer": ABSTENTION,
            "citations": [],
            "retrieval": {
                "matched_terms": [],
                "coverage": 0.0,
                "abstention_reason": (
                    "no filing or data-room sentence shares terms with the question"
                ),
            },
        }

    # Greedy coverage across the merged pool: each added sentence must cover a question term the
    # already-selected sentences did not. Ties break toward higher score, then public over
    # confidential, then quote text — all deterministic.
    def _preference(candidate: tuple) -> tuple:
        score, _, citation, quote = candidate
        return (score, 0 if citation["corpus"] == "public_filing" else 1, quote)

    remaining = set(question_terms)
    selected: list[tuple] = []
    available = sorted(pool, key=_preference, reverse=True)
    while available and len(selected) < _MAX_SENTENCES:
        best = max(available, key=lambda c: (len(c[1] & remaining), _preference(c)))
        if not (best[1] & remaining):
            break
        selected.append(best)
        remaining -= best[1]
        available.remove(best)

    matched = sorted(set().union(*(candidate[1] for candidate in selected)))
    coverage = len(matched) / max(len(question_terms), 1)
    status = "answered" if coverage >= _PARTIAL_COVERAGE_THRESHOLD else "partial"
    return {
        **base,
        "status": status,
        "answer": " ".join(candidate[3] for candidate in selected),
        "citations": [candidate[2] for candidate in selected],
        "retrieval": {
            "matched_terms": matched,
            "coverage": round(coverage, 3),
            "abstention_reason": None,
        },
    }
