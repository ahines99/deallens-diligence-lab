"""Shared text primitives for the SEC-side retrieval and cited-Q&A stack.

One tokenizer and one decimal-safe sentence splitter, so BM25 retrieval and the filings Q&A
that ranks on top of it can never disagree about what a token or a sentence is. The private-
corpus data-room Q&A (`deal_intelligence_service`) deliberately keeps its own broader tokenizer
tuned for uploaded documents; that divergence is intentional and documented there.
"""
from __future__ import annotations

import re

_TOKEN = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "the", "a", "an", "of", "to", "and", "or", "in", "on", "for", "is", "are", "was", "were",
    "what", "how", "does", "do", "with", "by", "as", "at", "from", "that", "this", "it",
}
# A decimal point is never a sentence boundary — `$185.0 million` must survive as one quote.
_SENTENCE_BOUNDARY = re.compile(r"[.!?]+(?=\s+|$)|\n+")


def tokens(text: str) -> list[str]:
    """Lowercase alphanumeric tokens, stopwords and 1–2 char noise removed."""
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS and len(t) > 2]


def sentences(text: str) -> list[str]:
    """Split into trimmed sentences without breaking on decimals or abbreviations mid-number."""
    spans: list[str] = []
    start_at = 0
    for boundary in _SENTENCE_BOUNDARY.finditer(text or ""):
        segment = text[start_at:boundary.end()].strip()
        if segment:
            spans.append(segment)
        start_at = boundary.end()
    tail = (text or "")[start_at:].strip()
    if tail:
        spans.append(tail)
    return spans or ([text.strip()] if text and text.strip() else [])
