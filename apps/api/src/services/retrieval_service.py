"""Deterministic retrieval over document chunks.

A keyword/term-frequency scorer stands in for a vector store so the demo is fully reproducible
with no embedding calls. The interface (retrieve -> ranked chunks) is what a pgvector-backed
implementation would also expose; swapping in real embeddings is a drop-in change.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models import DocumentChunk

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = {
    "the", "a", "an", "of", "to", "and", "or", "in", "on", "for", "is", "are", "was", "were",
    "what", "how", "does", "do", "with", "by", "as", "at", "from", "that", "this", "it",
}


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP and len(t) > 2]


@dataclass
class RetrievedChunk:
    chunk: DocumentChunk
    score: float


def retrieve(session: Session, workspace_id: str, query: str, k: int = 5) -> list[RetrievedChunk]:
    q_terms = _tokens(query)
    if not q_terms:
        return []
    q_set = set(q_terms)
    chunks = list(
        session.scalars(select(DocumentChunk).where(DocumentChunk.workspace_id == workspace_id))
    )
    scored: list[RetrievedChunk] = []
    for chunk in chunks:
        doc_terms = _tokens(f"{chunk.section} {chunk.chunk_text}")
        if not doc_terms:
            continue
        overlap = sum(1 for t in doc_terms if t in q_set)
        if overlap == 0:
            continue
        # TF-normalized overlap so long chunks don't dominate.
        score = overlap / (len(doc_terms) ** 0.5)
        scored.append(RetrievedChunk(chunk=chunk, score=round(score, 4)))
    scored.sort(key=lambda r: r.score, reverse=True)
    return scored[:k]
