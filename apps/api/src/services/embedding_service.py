"""Deterministic, dependency-free local text embedding.

This is a **feature-hashing** embedding: each document is decomposed into lexical features
(word unigrams, word bigrams, and character 3-grams), and every feature is hashed — with a
stable cryptographic hash, not Python's salted ``hash()`` — into a fixed-dimension float
vector using the signed hashing trick. The vector is L2-normalized, so a cosine similarity is
just a dot product. It is pure Python and needs no model download, no API key, and no network,
which keeps the project's determinism invariant intact: identical text always yields the byte-
identical vector, offline and across processes.

Why not a real transformer? The hash-pinned ``requirements.lock`` and CI forbid heavyweight ML
deps (torch/transformers/onnxruntime/sentence-transformers). A hashing embedding is a legitimate
keyless embedding that gives semantic-ish vector similarity (subword character n-grams let it
reward near-matches, not just exact tokens) while staying reproducible.

Pluggable seam: the persisted vector lives on ``DocumentChunk.embedding`` and its producer tag
on ``DocumentChunk.embedding_id``. Swapping in a real model later is a drop-in change — compute
a different vector here, bump ``EMBED_METHOD``, and re-run the backfill worker. In production a
pgvector-backed column would store the same vector and compute cosine in the database; the Python
``cosine`` here is the SQLite-friendly equivalent and the interface is identical.
"""
from __future__ import annotations

import hashlib
import math
import re

# Fixed embedding dimensionality. 256 buckets keep hash collisions low for filing-sized chunks
# while staying cheap to store as JSON and to dot-product in Python.
EMBED_DIM = 256
# Producer tag stored on DocumentChunk.embedding_id. Bump this when the feature extraction or
# dimensionality changes so a backfill can detect and refresh stale vectors.
EMBED_METHOD = "local-hash-charngram-256-v1"

_WORD = re.compile(r"[a-z0-9]+")


def _features(text: str) -> list[str]:
    """Lexical features for hashing: word unigrams + bigrams + intra-word character 3-grams.

    Character n-grams give the embedding subword sensitivity (``revenue``/``revenues`` share
    most of their grams), which is what makes hashed similarity behave semantic-ish rather than
    purely exact-match.
    """
    words = _WORD.findall((text or "").lower())
    feats: list[str] = list(words)
    for first, second in zip(words, words[1:]):
        feats.append(f"{first}_{second}")
    for word in words:
        padded = f"#{word}#"
        for i in range(len(padded) - 2):
            feats.append(f"c:{padded[i:i + 3]}")
    return feats


def _bucket_and_sign(feature: str) -> tuple[int, float]:
    """Map a feature to a (bucket, ±1) pair via a stable hash (distinct bytes for each)."""
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    bucket = int.from_bytes(digest[:4], "big") % EMBED_DIM
    sign = 1.0 if digest[4] & 1 else -1.0
    return bucket, sign


def embed(text: str) -> list[float]:
    """Return the deterministic, L2-normalized embedding vector for ``text``.

    Empty / feature-less text yields the zero vector (norm 0); callers treat that as "no
    embedding signal" and fall back to lexical retrieval.
    """
    vector = [0.0] * EMBED_DIM
    for feature in _features(text):
        bucket, sign = _bucket_and_sign(feature)
        vector[bucket] += sign
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return vector
    return [value / norm for value in vector]


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two vectors; 0.0 when either is empty or zero-length."""
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def embed_chunk(chunk) -> None:
    """Populate a DocumentChunk-like object's ``embedding`` and ``embedding_id`` in place.

    Duck-typed on ``section`` / ``chunk_text`` so the ingest and backfill paths share one
    definition of what text an embedding covers (the same combined text BM25 indexes).
    """
    combined = f"{chunk.section or ''} {chunk.chunk_text or ''}"
    chunk.embedding = embed(combined)
    chunk.embedding_id = EMBED_METHOD
