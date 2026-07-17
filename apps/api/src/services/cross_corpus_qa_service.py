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
can never mistake a confidential data-room quote for a public disclosure. The default answer stays
in the extractive-cite-or-abstain lane; :func:`maybe_synthesize_cross_corpus` (G54) optionally
re-voices it through the same fail-closed fluency gate as G04, preserving those labels.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.agents.citation_auditor import CitationAuditor
from src.agents.llm_provider import LiveProvider
from src.config import settings
from src.models import DataRoomChunk, DataRoomDocument, Deal, Filing
from src.services import prompt_registry, retrieval_service, textkit
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
    # confidential, then quote text — all deterministic. Selection uses max()/descending order,
    # so the preferred (public) corpus must carry the HIGHER component value.
    def _preference(candidate: tuple) -> tuple:
        score, _, citation, quote = candidate
        return (score, 1 if citation["corpus"] == "public_filing" else 0, quote)

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


# --- G54: grounded synthesis over the cross-corpus answer, failing closed on drift ----------

# Statuses whose answers carry real extracted evidence and are eligible for a fluency pass.
_SYNTHESIS_ELIGIBLE = {"answered", "partial"}


def _synthesis_user_prompt(result: dict) -> str:
    """Compose the LLM instruction from the labeled extracts only (no outside context).

    Each quote keeps its provenance label so the registered ``cross_corpus_synthesis`` system
    prompt can hold the rewrite to its no-cross-attribution rule.
    """
    quotes = [
        f"- [{'CONFIDENTIAL' if c.get('confidential') else 'PUBLIC'}] {c['quote']}"
        for c in result.get("citations", [])
        if c.get("quote")
    ]
    if not quotes:
        quotes = [f"- [PUBLIC] {result.get('answer', '')}"]
    labeled = "\n".join(quotes)
    return (
        "Rewrite the following labeled verbatim extracts into one fluent answer to the question "
        f"{result.get('question', '')!r}. Preserve every number exactly and never attribute "
        "[CONFIDENTIAL] content to a public source or vice versa.\n\n"
        f"Extracts:\n{labeled}"
    )


def maybe_synthesize_cross_corpus(
    result: dict,
    *,
    external_allowed: bool,
    provider_factory: Callable[[], LiveProvider] = LiveProvider,
) -> dict:
    """Optionally re-voice ``result``'s extractive answer for fluency, failing closed on drift.

    The G04 discipline applied to the cross-corpus lane: eligible only when the answer carries
    real evidence (abstentions are never sent to an LLM), gated on consent/mock/no-key, audited
    by :class:`CitationAuditor` against the extractive answer, and falling back on any drift or
    provider failure. The ``citations`` list — including every public/confidential label and
    provenance block — is passed through untouched in every path.
    """
    if result.get("status") not in _SYNTHESIS_ELIGIBLE:
        return {**result, "grounded": {"applied": False, "reason": "not_eligible"}}
    if not external_allowed:
        return {**result, "grounded": {"applied": False, "reason": "no_consent"}}
    if settings.is_mock:
        return {**result, "grounded": {"applied": False, "reason": "mock"}}
    if not settings.llm_api_key:
        return {**result, "grounded": {"applied": False, "reason": "no_api_key"}}

    extractive_answer = result.get("answer", "")
    try:
        provider = provider_factory()
        candidate = provider.complete(
            prompt_registry.get("cross_corpus_synthesis").template,
            _synthesis_user_prompt(result),
        )
        audit = CitationAuditor.audit_rewrite(extractive_answer, candidate)
        man = prompt_registry.manifest("cross_corpus_synthesis", model=provider.model)
        if audit.faithful:
            return {
                **result,
                "answer": candidate,
                "method": f"{result.get('method', METHOD)}+grounded_llm",
                "grounded": {"applied": True, "reason": "applied", "manifest": man},
            }
        return {
            **result,
            "grounded": {"applied": False, "reason": "audit_rejected", "manifest": man},
        }
    except Exception:
        # Any provider/parse failure falls back to the deterministic extractive answer.
        return {**result, "grounded": {"applied": False, "reason": "error"}}
