"""Optional local cross-encoder reranker backend (G82).

Operator opt-in: set ``RERANKER_BACKEND=onnx_local`` and ``RERANKER_MODEL_PATH`` to a directory
containing ``model.onnx`` and ``tokenizer.json`` (e.g. an exported
cross-encoder/ms-marco-MiniLM-L-6-v2). The dependencies are the SAME optional extra as the G55
embedding backend (``pip install .[embeddings]``: ``onnxruntime`` + ``tokenizers``) and are
deliberately NOT part of the base install — the default stack, CI, and the keyless pitch stay
dependency-stable, and inference remains fully local (no network, no API key).

Every failure mode — extra not installed, model path unset/missing, load error — reports an
explicit note through ``available()`` so ``retrieval_service.maybe_rerank`` can skip reranking
loudly (provenance carries the note), never crash, and never pretend a reranker ran. The method
tag embeds a fingerprint of the exact model file, so provenance always records WHICH model
produced an applied reranking.

Unlike the bi-encoder in ``onnx_embedding`` (one text in, one pooled vector out), a
cross-encoder scores a (query, passage) PAIR in a single forward pass and is only ever used to
reorder an already-retrieved candidate list — see ``score``.
"""
from __future__ import annotations

import hashlib
import threading
from pathlib import Path

from src.config import settings

# Cross-encoders read the query and the candidate passage as ONE packed sequence, so the budget
# is double the embedding backend's single-text cap; 512 matches the trained length of the
# ms-marco MiniLM cross-encoder family this seam targets.
_MAX_TOKENS = 512

_lock = threading.Lock()
# path -> (ok, note, method, session, tokenizer). One entry per configured model path; a changed
# RERANKER_MODEL_PATH simply loads (and caches) the new model next call.
_cache: dict[str, tuple] = {}


def _fingerprint(model_file: Path) -> str:
    digest = hashlib.sha256()
    with model_file.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()[:12]


def _load(path: str) -> tuple:
    """Load (once per path) the ONNX session + tokenizer; failures return (False, note, ...)."""
    try:
        import onnxruntime  # noqa: PLC0415 - optional extra, imported lazily by design
        from tokenizers import Tokenizer  # noqa: PLC0415
    except ImportError:
        return (
            False,
            "onnx_local reranker requires the optional extra: pip install .[embeddings]",
            None,
            None,
            None,
        )
    model_dir = Path(path)
    model_file = model_dir / "model.onnx"
    tokenizer_file = model_dir / "tokenizer.json"
    if not model_file.is_file() or not tokenizer_file.is_file():
        return (
            False,
            f"RERANKER_MODEL_PATH must contain model.onnx and tokenizer.json (got {path!r})",
            None,
            None,
            None,
        )
    try:
        session = onnxruntime.InferenceSession(
            str(model_file), providers=["CPUExecutionProvider"]
        )
        tokenizer = Tokenizer.from_file(str(tokenizer_file))
        # Pair truncation (default longest_first) keeps query + passage within the trained cap.
        tokenizer.enable_truncation(max_length=_MAX_TOKENS)
        method = f"onnx-rerank-{_fingerprint(model_file)}"
    except Exception as exc:  # noqa: BLE001 - any load failure must degrade, not crash
        return (False, f"ONNX reranker model failed to load: {exc}", None, None, None)
    return (True, None, method, session, tokenizer)


def _entry() -> tuple:
    path = settings.reranker_model_path or ""
    if not path:
        return (False, "RERANKER_MODEL_PATH is not set", None, None, None)
    with _lock:
        if path not in _cache:
            _cache[path] = _load(path)
        return _cache[path]


def available() -> tuple[bool, str | None]:
    """(usable, note). The note explains WHY the reranker is unusable — surfaced, never hidden."""
    ok, note, *_ = _entry()
    return ok, note


def method() -> str | None:
    """Producer tag carrying the model-file fingerprint (rerank provenance identification)."""
    ok, _note, tag, *_ = _entry()
    return tag if ok else None


def score(query: str, texts: list[str]) -> list[float]:
    """Cross-encoder relevance scores, one per candidate text; higher = more relevant.

    Scoring convention (documented so exported models are interchangeable):

    * each (query, text) pair is tokenized as ONE packed sequence — ``encode(query, text)`` —
      with the segment/type ids marking which tokens belong to the passage (BERT-family
      cross-encoders require real pair segmentation, unlike the all-zeros segment the
      bi-encoder uses);
    * the model runs once per pair and the FIRST output holds the classification logits.
      No pooling is involved: cross-encoders emit sequence-level logits directly from the
      [CLS] position, so there is nothing to mean-pool;
    * a single-logit head (the ms-marco cross-encoder convention) is read as-is — sigmoid is
      monotone, so ranking by the raw logit equals ranking by relevance probability;
    * a two-class head is read as the LAST logit (the positive/relevant class by convention).
    """
    ok, _note, _tag, session, tokenizer = _entry()
    if not ok:  # pragma: no cover - callers gate on available() first
        raise RuntimeError("onnx_local reranker backend is not available")
    import numpy  # noqa: PLC0415 - ships with onnxruntime, lazy like its parent

    scores: list[float] = []
    for text in texts:
        encoding = tokenizer.encode(query or "", text or "")
        ids = numpy.array([encoding.ids], dtype=numpy.int64)
        mask = numpy.array([encoding.attention_mask], dtype=numpy.int64)
        type_ids = numpy.array([encoding.type_ids], dtype=numpy.int64)
        feeds = {}
        for model_input in session.get_inputs():
            if model_input.name == "input_ids":
                feeds[model_input.name] = ids
            elif model_input.name == "attention_mask":
                feeds[model_input.name] = mask
            elif model_input.name == "token_type_ids":
                feeds[model_input.name] = type_ids
            else:
                feeds[model_input.name] = numpy.zeros_like(ids)
        logits = numpy.asarray(session.run(None, feeds)[0]).reshape(-1)  # [1] or [num_classes]
        scores.append(float(logits[0] if logits.shape[0] == 1 else logits[-1]))
    return scores
