"""Deterministic retrieval over document chunks.

Two rankers, both pure Python and fully reproducible offline:

* **BM25** (``retrieve``) — Okapi lexical ranking, unchanged and still the contract the rest of
  the app depends on.
* **Hybrid** (``retrieve_hybrid``) — fuses BM25 with vector similarity over the deterministic
  local embeddings (see ``embedding_service``) via Reciprocal Rank Fusion (RRF). This lifts
  chunks that both rankers like above chunks only one likes, and degrades cleanly to pure BM25
  when a workspace has no stored embeddings yet.

The vector store here is ``DocumentChunk.embedding`` (JSON) with cosine computed in Python; a
pgvector-backed column is the production seam and exposes the identical ``retrieve -> ranked
chunks`` interface.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models import DocumentChunk
from src.services import embedding_service, textkit

# Standard Okapi BM25 constants: k1 tempers term-frequency saturation, b scales length
# normalization by the corpus-average chunk length.
_BM25_K1 = 1.5
_BM25_B = 0.75

# Reciprocal Rank Fusion damping constant. 60 is the value from the original Cormack et al.
# paper and the de-facto default: large enough that top ranks don't dominate outright, small
# enough that rank order still matters.
_RRF_K = 60
# Each ranker contributes this many candidates to the fusion pool before the top-k cut, so a
# chunk ranked highly by one signal can still be rescued into the final list by the other.
_FUSION_POOL = 50

# Retrieval and the Q&A that ranks on top of it share one tokenizer (see textkit).
_tokens = textkit.tokens


@dataclass
class RetrievedChunk:
    chunk: DocumentChunk
    score: float


def retrieve(session: Session, workspace_id: str, query: str, k: int = 5) -> list[RetrievedChunk]:
    q_terms = set(_tokens(query))
    if not q_terms:
        return []
    chunks = list(
        session.scalars(select(DocumentChunk).where(DocumentChunk.workspace_id == workspace_id))
    )
    if not chunks:
        return []

    term_counts: list[Counter[str]] = []
    lengths: list[int] = []
    document_frequency: Counter[str] = Counter()
    for chunk in chunks:
        counts = Counter(_tokens(f"{chunk.section} {chunk.chunk_text}"))
        term_counts.append(counts)
        lengths.append(sum(counts.values()))
        document_frequency.update(term for term in q_terms if term in counts)

    corpus_size = len(chunks)
    average_length = (sum(lengths) / corpus_size) or 1.0
    idf = {
        term: math.log((corpus_size - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5) + 1.0)
        for term in q_terms
        if document_frequency[term] > 0
    }
    if not idf:
        return []

    scored: list[RetrievedChunk] = []
    for chunk, counts, length in zip(chunks, term_counts, lengths):
        score = 0.0
        for term, term_idf in idf.items():
            tf = counts.get(term, 0)
            if tf == 0:
                continue
            score += term_idf * (tf * (_BM25_K1 + 1)) / (
                tf + _BM25_K1 * (1 - _BM25_B + _BM25_B * length / average_length)
            )
        if score > 0:
            scored.append(RetrievedChunk(chunk=chunk, score=round(score, 4)))
    scored.sort(key=lambda r: (r.score, r.chunk.id), reverse=True)
    return scored[:k]


def workspace_has_embeddings(session: Session, workspace_id: str) -> bool:
    """True when at least one chunk has a stored vector from the ACTIVE embedding method.

    Vectors from a different producer (a previous backend or model — G55) are not comparable
    with the active method's query vector, so they must not enable the hybrid path; the backfill
    worker refreshes them.
    """
    hit = session.scalar(
        select(DocumentChunk.id)
        .where(
            DocumentChunk.workspace_id == workspace_id,
            DocumentChunk.embedding.is_not(None),
            DocumentChunk.embedding_id == embedding_service.active_method(),
        )
        .limit(1)
    )
    return hit is not None


def _vector_candidates(
    session: Session, workspace_id: str, query: str, k: int
) -> list[RetrievedChunk]:
    """Cosine similarity of the query embedding against same-method stored chunk embeddings."""
    query_vector = embedding_service.embed(query)
    if not any(query_vector):
        return []
    chunks = list(
        session.scalars(
            select(DocumentChunk).where(
                DocumentChunk.workspace_id == workspace_id,
                DocumentChunk.embedding.is_not(None),
                # Same-space guard: never compare vectors from different producers/models.
                DocumentChunk.embedding_id == embedding_service.active_method(),
            )
        )
    )
    scored: list[RetrievedChunk] = []
    for chunk in chunks:
        if not chunk.embedding:
            continue
        similarity = embedding_service.cosine(query_vector, chunk.embedding)
        if similarity > 0:
            scored.append(RetrievedChunk(chunk=chunk, score=round(similarity, 4)))
    scored.sort(key=lambda r: (r.score, r.chunk.id), reverse=True)
    return scored[:k]


def _reciprocal_rank_fusion(
    ranked_lists: list[list[RetrievedChunk]], k: int
) -> list[RetrievedChunk]:
    """Fuse ranked lists by RRF: score(d) = Σ 1 / (RRF_K + rank_in_list(d)), rank starting at 1.

    A chunk both rankers place highly accumulates two reciprocal-rank contributions and so
    outranks a chunk only one ranker surfaced — the property the fusion test pins down.
    """
    fused_scores: dict[str, float] = {}
    chunk_by_id: dict[str, DocumentChunk] = {}
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked, start=1):
            chunk_id = item.chunk.id
            fused_scores[chunk_id] = fused_scores.get(chunk_id, 0.0) + 1.0 / (_RRF_K + rank)
            chunk_by_id[chunk_id] = item.chunk
    fused = [
        RetrievedChunk(chunk=chunk_by_id[chunk_id], score=round(score, 6))
        for chunk_id, score in fused_scores.items()
    ]
    fused.sort(key=lambda r: (r.score, r.chunk.id), reverse=True)
    return fused[:k]


def retrieve_hybrid(
    session: Session, workspace_id: str, query: str, k: int = 5
) -> list[RetrievedChunk]:
    """BM25 fused with vector similarity via RRF; degrades to BM25 when no embeddings exist.

    Returns ``RetrievedChunk`` items whose ``score`` is the fused RRF score (small positive
    magnitudes), ranked best-first — the same shape ``retrieve`` returns, so callers are
    interchangeable.
    """
    pool = max(k, _FUSION_POOL)
    bm25 = retrieve(session, workspace_id, query, k=pool)
    vector = _vector_candidates(session, workspace_id, query, k=pool)
    ranked_lists = [ranked for ranked in (bm25, vector) if ranked]
    if not ranked_lists:
        return []
    return _reciprocal_rank_fusion(ranked_lists, k=k)
