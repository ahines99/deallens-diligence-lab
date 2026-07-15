"""Deterministic retrieval over document chunks.

Okapi BM25 (pure Python, no model downloads, no embedding calls) ranks filing chunks so the
demo stays fully reproducible offline. The interface (retrieve -> ranked chunks) is what a
pgvector-backed implementation would also expose; swapping in real embeddings is a drop-in
change — `DocumentChunk.embedding_id` is already reserved for that store.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models import DocumentChunk
from src.services import textkit

# Standard Okapi BM25 constants: k1 tempers term-frequency saturation, b scales length
# normalization by the corpus-average chunk length.
_BM25_K1 = 1.5
_BM25_B = 0.75

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
