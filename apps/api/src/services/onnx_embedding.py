"""Optional local neural embedding backend (G55).

Operator opt-in: set ``EMBEDDINGS_BACKEND=onnx_local`` and ``EMBEDDINGS_MODEL_PATH`` to a
directory containing ``model.onnx`` and ``tokenizer.json`` (e.g. an exported
sentence-transformers/all-MiniLM-L6-v2). The dependencies (``onnxruntime``, ``tokenizers``)
install via ``pip install .[embeddings]`` and are deliberately NOT part of the base install —
the default stack, CI, and the keyless pitch stay dependency-stable, and inference remains
fully local (no network, no API key), preserving the project's keyless-data invariant.

Every failure mode — extra not installed, model path unset/missing, load error — reports an
explicit note through ``available()`` so ``embedding_service`` can degrade to feature hashing
loudly, never crash, and never mix vector spaces: the method tag embeds a fingerprint of the
exact model file, so vectors from different models (or from the hashing backend) are never
compared against each other.
"""
from __future__ import annotations

import hashlib
import threading
from pathlib import Path

from src.config import settings

# Sentence-transformer models degrade past their trained length; 256 tokens comfortably covers
# filing-sized chunks after the ingest-side character caps.
_MAX_TOKENS = 256

_lock = threading.Lock()
# path -> (ok, note, method, session, tokenizer). One entry per configured model path; a changed
# EMBEDDINGS_MODEL_PATH simply loads (and caches) the new model next call.
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
            "onnx_local backend requires the optional extra: pip install .[embeddings]",
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
            f"EMBEDDINGS_MODEL_PATH must contain model.onnx and tokenizer.json (got {path!r})",
            None,
            None,
            None,
        )
    try:
        session = onnxruntime.InferenceSession(
            str(model_file), providers=["CPUExecutionProvider"]
        )
        tokenizer = Tokenizer.from_file(str(tokenizer_file))
        tokenizer.enable_truncation(max_length=_MAX_TOKENS)
        method = f"onnx-local-{_fingerprint(model_file)}"
    except Exception as exc:  # noqa: BLE001 - any load failure must degrade, not crash
        return (False, f"ONNX model failed to load: {exc}", None, None, None)
    return (True, None, method, session, tokenizer)


def _entry() -> tuple:
    path = settings.embeddings_model_path or ""
    if not path:
        return (False, "EMBEDDINGS_MODEL_PATH is not set", None, None, None)
    with _lock:
        if path not in _cache:
            _cache[path] = _load(path)
        return _cache[path]


def available() -> tuple[bool, str | None]:
    """(usable, note). The note explains WHY the backend is unusable — surfaced, never hidden."""
    ok, note, *_ = _entry()
    return ok, note


def method() -> str | None:
    """Producer tag carrying the model-file fingerprint (vector-space isolation)."""
    ok, _note, tag, *_ = _entry()
    return tag if ok else None


def embed(text: str) -> list[float]:
    """Mean-pooled, L2-normalized sentence embedding from the configured local model."""
    ok, _note, _tag, session, tokenizer = _entry()
    if not ok:  # pragma: no cover - callers gate on available() first
        raise RuntimeError("onnx_local embedding backend is not available")
    import numpy  # noqa: PLC0415 - ships with onnxruntime, lazy like its parent

    encoding = tokenizer.encode(text or "")
    ids = numpy.array([encoding.ids], dtype=numpy.int64)
    mask = numpy.array([encoding.attention_mask], dtype=numpy.int64)
    feeds = {}
    for model_input in session.get_inputs():
        if model_input.name == "input_ids":
            feeds[model_input.name] = ids
        elif model_input.name == "attention_mask":
            feeds[model_input.name] = mask
        else:
            # e.g. token_type_ids for BERT-family exports: a single segment of zeros.
            feeds[model_input.name] = numpy.zeros_like(ids)
    hidden = session.run(None, feeds)[0]  # [1, seq, dim]
    weights = mask[..., None].astype(hidden.dtype)
    summed = (hidden * weights).sum(axis=1)
    counts = numpy.clip(weights.sum(axis=1), 1e-9, None)
    pooled = summed / counts
    norm = float(numpy.linalg.norm(pooled))
    if norm == 0.0:
        return [0.0] * pooled.shape[-1]
    return (pooled[0] / norm).tolist()
