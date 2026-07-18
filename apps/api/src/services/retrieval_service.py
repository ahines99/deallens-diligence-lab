"""Deterministic retrieval over document chunks.

Two rankers, both pure Python and fully reproducible offline:

* **BM25** (``retrieve``) — Okapi lexical ranking, unchanged and still the contract the rest of
  the app depends on.
* **Hybrid** (``retrieve_hybrid``) — fuses BM25 with vector similarity over the deterministic
  local embeddings (see ``embedding_service``) via Reciprocal Rank Fusion (RRF). This lifts
  chunks that both rankers like above chunks only one likes, and degrades cleanly to pure BM25
  when a workspace has no stored embeddings yet.

The vector store is ``DocumentChunk.embedding`` (JSON) — always written, always the source of
truth. On PostgreSQL a pgvector fast path (G83) additionally maintains a DB-side
``embedding_vector`` column (added by migration ``d5f2b8c3a1e9`` when the extension is
available) and ranks with the ``<=>`` cosine operator; the ranking is parity-tested against the
Python path, and any missing column/extension or runtime error falls back to the Python path —
logged, never crashed. SQLite always uses the Python path.

G82 adds an optional cross-encoder rerank of the hybrid top-k (``maybe_rerank``): default OFF
(``RERANKER_BACKEND=off``), applied only when the operator opts in AND the local ONNX model is
actually loadable, with explicit provenance either way.
"""
from __future__ import annotations

import logging
import math
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from src.config import settings
from src.models import DocumentChunk
from src.services import embedding_service, onnx_reranker, textkit

logger = logging.getLogger("deallens.retrieval")

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


# --------------------------------------------------------------------- pgvector fast path (G83)
# Per-process probe cache: engine URL -> whether document_chunks.embedding_vector exists. The
# column only exists on PostgreSQL databases where migration d5f2b8c3a1e9 found the pgvector
# extension (its type depends on the extension, so column presence implies extension presence).
_pgvector_probe: dict[str, bool] = {}

# Lazy sync of the DB-side vector column from the JSON source of truth, scoped to one workspace
# and the ACTIVE embedding method. Rows are (re)cast when the column is NULL OR stale — a row is
# stale after the backfill worker re-embeds it under a new method tag, which rewrites the JSON
# but not the vector column. Staleness is detected by comparing the stored vector's text form
# against a fresh cast of the JSON (cheap at workspace scale; the cast round-trips float32
# deterministically, so an in-sync row never rewrites). The JSON array's text form ("[0.1, ...]")
# is a valid pgvector input literal, so the cast happens entirely in SQL.
_PG_SYNC_SQL = text(
    """
    UPDATE document_chunks
       SET embedding_vector = CAST(CAST(embedding AS text) AS vector)
     WHERE workspace_id = :workspace_id
       AND embedding_id = :method
       AND embedding IS NOT NULL
       AND (embedding_vector IS NULL
            OR CAST(embedding_vector AS text)
               IS DISTINCT FROM CAST(CAST(CAST(embedding AS text) AS vector) AS text))
    """
)

# DB-side cosine ranking. Parity with the Python path is deliberate and tested:
# * similarity = 1 - cosine distance, kept > 0 (the Python path drops non-positive scores);
# * a zero stored vector yields distance NaN — and NaN compares GREATER than everything in
#   Postgres, so it must be excluded explicitly (NaN <> NaN is false, hence the predicate);
# * ORDER BY the similarity ROUNDED to 4 places then id descending in byte order ("C"),
#   mirroring the Python sort key (round(sim, 4), chunk.id) reverse=True.
_PG_RANK_SQL = text(
    """
    SELECT id,
           1 - (embedding_vector <=> CAST(:q AS vector)) AS sim
      FROM document_chunks
     WHERE workspace_id = :workspace_id
       AND embedding_id = :method
       AND embedding_vector IS NOT NULL
       AND (embedding_vector <=> CAST(:q AS vector)) <> CAST('NaN' AS float8)
       AND 1 - (embedding_vector <=> CAST(:q AS vector)) > 0
     ORDER BY round(CAST(1 - (embedding_vector <=> CAST(:q AS vector)) AS numeric), 4) DESC,
              id COLLATE "C" DESC
     LIMIT :k
    """
)


def _pgvector_ready(session: Session) -> bool:
    """True when the Postgres fast path can run: postgresql dialect + embedding_vector column.

    Probed once per process per engine. When the column is absent (migration skipped because
    the pgvector extension was unavailable, or an unmigrated create_all database), the Python
    path serves every query — logged once, never an error.
    """
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        return False
    key = str(bind.engine.url)
    if key not in _pgvector_probe:
        present = (
            session.execute(
                text(
                    """
                    SELECT 1 FROM information_schema.columns
                     WHERE table_schema = current_schema()
                       AND table_name = 'document_chunks'
                       AND column_name = 'embedding_vector'
                    """
                )
            ).first()
            is not None
        )
        _pgvector_probe[key] = present
        if present:
            logger.info("pgvector fast path active: DB-side cosine over embedding_vector")
        else:
            logger.info(
                "pgvector fast path unavailable: document_chunks.embedding_vector missing "
                "(migration d5f2b8c3a1e9 skipped or extension absent); using Python cosine"
            )
    return _pgvector_probe[key]


def _vector_candidates_pg(
    session: Session, workspace_id: str, query_vector: list[float], k: int
) -> list[RetrievedChunk] | None:
    """Postgres pgvector ranking; returns ``None`` to signal fallback to the Python path.

    Runs inside a SAVEPOINT so any runtime failure (e.g. a dimension mismatch, a concurrently
    dropped extension) rolls back cleanly without poisoning the caller's transaction; the
    fallback is silent for callers but always logged.
    """
    method = embedding_service.active_method()
    q_literal = "[" + ",".join(str(v) for v in query_vector) + "]"
    try:
        with session.begin_nested():
            session.execute(_PG_SYNC_SQL, {"workspace_id": workspace_id, "method": method})
            rows = session.execute(
                _PG_RANK_SQL,
                {"workspace_id": workspace_id, "method": method, "q": q_literal, "k": k},
            ).all()
    except DBAPIError as exc:
        logger.warning("pgvector fast path failed (%s); falling back to Python cosine", exc)
        return None
    if not rows:
        return []
    chunk_by_id = {
        chunk.id: chunk
        for chunk in session.scalars(
            select(DocumentChunk).where(DocumentChunk.id.in_([row[0] for row in rows]))
        )
    }
    return [
        RetrievedChunk(chunk=chunk_by_id[chunk_id], score=round(float(sim), 4))
        for chunk_id, sim in rows
        if chunk_id in chunk_by_id
    ]


def _vector_candidates_python(
    session: Session, workspace_id: str, query_vector: list[float], k: int
) -> list[RetrievedChunk]:
    """Pure-Python cosine ranking over the JSON vectors (SQLite path and Postgres fallback)."""
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


def _vector_candidates(
    session: Session, workspace_id: str, query: str, k: int
) -> list[RetrievedChunk]:
    """Cosine similarity of the query embedding against same-method stored chunk embeddings."""
    query_vector = embedding_service.embed(query)
    if not any(query_vector):
        return []
    if _pgvector_ready(session):
        fast = _vector_candidates_pg(session, workspace_id, query_vector, k)
        if fast is not None:
            return fast
    return _vector_candidates_python(session, workspace_id, query_vector, k)


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


def maybe_rerank(
    query: str, retrieved: list[RetrievedChunk], k: int
) -> tuple[list[RetrievedChunk], dict]:
    """Optionally reorder an already-retrieved top-k with the local cross-encoder (G82).

    Applies ONLY when the operator set ``RERANKER_BACKEND=onnx_local`` AND the model actually
    loads; otherwise the input ordering is returned untouched. The provenance dict is always
    honest about what happened::

        {"applied": bool, "reason": "off" | "unavailable: <note>" | "applied", "method": tag|None}

    When applied, each item's ``score`` becomes the cross-encoder relevance score (rounded to 4
    places) and the list is re-sorted by (score, chunk.id) descending — the same stable
    tie-break every ranker in this module uses.
    """
    backend = (settings.reranker_backend or "off").lower()
    if backend != "onnx_local":
        return list(retrieved[:k]), {"applied": False, "reason": "off", "method": None}
    ok, note = onnx_reranker.available()
    if not ok:
        logger.info("reranker configured but unavailable (%s); hybrid order unchanged", note)
        return (
            list(retrieved[:k]),
            {"applied": False, "reason": f"unavailable: {note}", "method": None},
        )
    provenance = {"applied": True, "reason": "applied", "method": onnx_reranker.method()}
    if not retrieved:
        return [], provenance
    # Score the same combined text BM25 and the embedding index — one definition of a chunk.
    texts = [f"{item.chunk.section or ''} {item.chunk.chunk_text or ''}" for item in retrieved]
    scores = onnx_reranker.score(query, texts)
    reranked = [
        RetrievedChunk(chunk=item.chunk, score=round(float(score), 4))
        for item, score in zip(retrieved, scores)
    ]
    reranked.sort(key=lambda r: (r.score, r.chunk.id), reverse=True)
    return reranked[:k], provenance


def _hybrid_fused(
    session: Session, workspace_id: str, query: str, k: int
) -> list[RetrievedChunk]:
    """The pure RRF fusion, never reranked — what the eval's "hybrid" ranker measures even when
    a reranker is active, so the G82 promotion gate compares rerank against true RRF."""
    pool = max(k, _FUSION_POOL)
    bm25 = retrieve(session, workspace_id, query, k=pool)
    vector = _vector_candidates(session, workspace_id, query, k=pool)
    ranked_lists = [ranked for ranked in (bm25, vector) if ranked]
    if not ranked_lists:
        return []
    return _reciprocal_rank_fusion(ranked_lists, k=k)


def retrieve_hybrid_with_provenance(
    session: Session, workspace_id: str, query: str, k: int = 5
) -> tuple[list[RetrievedChunk], dict]:
    """``retrieve_hybrid`` plus the rerank provenance dict (used by the eval harness)."""
    fused = _hybrid_fused(session, workspace_id, query, k)
    return maybe_rerank(query, fused, k)


def retrieve_hybrid(
    session: Session, workspace_id: str, query: str, k: int = 5
) -> list[RetrievedChunk]:
    """BM25 fused with vector similarity via RRF; degrades to BM25 when no embeddings exist.

    Returns ``RetrievedChunk`` items whose ``score`` is the fused RRF score (small positive
    magnitudes), ranked best-first — the same shape ``retrieve`` returns, so callers are
    interchangeable. With the default ``RERANKER_BACKEND=off`` the result is exactly the RRF
    fusion; an opted-in, loadable reranker reorders the top-k (see ``maybe_rerank``).
    """
    ranked, _provenance = retrieve_hybrid_with_provenance(session, workspace_id, query, k=k)
    return ranked
