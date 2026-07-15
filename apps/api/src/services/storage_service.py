"""Blob-storage abstraction (G40).

A tiny content-addressable-ish key/value store for opaque bytes — data-room document
bytes and the EDGAR on-disk cache are the two consumers. The point of the abstraction is
*backend parity*: the same ``put``/``get``/``exists``/``delete`` contract behaves identically
whether bytes land on local disk (the zero-setup default) or in an S3-compatible bucket.

Two backends:

* :class:`LocalDiskStore` — the default. Stores each key as a file under ``settings.storage_root``,
  key-namespaced and path-traversal-safe. Fully working with no configuration.
* :class:`S3Store` — the S3-compatible option, gated by ``settings.storage_backend == "s3"``.
  It is a *structurally complete* adapter written against a small, pluggable client protocol
  (:class:`S3ClientProtocol`) rather than a hard dependency on ``boto3``. The interface parity is
  unit-tested by injecting an in-memory fake client, proving the abstraction is backend-agnostic.
  Wiring a **real** bucket needs credentials plus a concrete client implementing four methods
  (``put_object``/``get_object``/``head_object``/``delete_object``); ``get_store()`` raises a clear
  error explaining exactly that until such a client is supplied. No new dependency is introduced.

Keys are logical identifiers such as ``"edgar-cache/json-<sha>"``. Callers never pass filesystem
paths; :func:`sanitize_key` rejects absolute paths and ``..`` traversal so no key can escape the
configured root (or bucket prefix).
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Protocol, runtime_checkable

from src.config import settings


class BlobStoreError(Exception):
    """Base class for storage-layer failures."""


class BlobNotFound(BlobStoreError, KeyError):
    """Raised by ``get`` when a key does not exist. Consistent across every backend.

    Subclasses ``KeyError`` so ``except KeyError`` callers keep working, while remaining a
    ``BlobStoreError`` for storage-aware handlers.
    """


class BlobStoreConfigurationError(BlobStoreError):
    """Raised when a backend is selected but cannot be constructed as configured."""


# A key segment may contain word characters, dot, and hyphen. Segments are joined by "/".
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def sanitize_key(key: str) -> str:
    """Return a normalized, traversal-safe relative key or raise :class:`ValueError`.

    Accepts forward-or-back-slash separated keys, collapses them to ``/``-joined segments, and
    rejects anything that could escape the storage root: absolute paths, drive letters, empty
    keys, ``.``/``..`` segments, NUL bytes, or characters outside ``[A-Za-z0-9._-]``.
    """
    if not key or not isinstance(key, str):
        raise ValueError("Storage key must be a non-empty string")
    if "\x00" in key:
        raise ValueError("Storage key must not contain NUL bytes")
    normalized = key.replace("\\", "/")
    # Reject absolute POSIX paths and Windows drive-qualified paths (e.g. "C:/..").
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        raise ValueError(f"Storage key must be relative, got {key!r}")
    segments = [segment for segment in normalized.split("/") if segment != ""]
    if not segments:
        raise ValueError(f"Storage key resolves to an empty path: {key!r}")
    for segment in segments:
        if segment in {".", ".."}:
            raise ValueError(f"Storage key must not contain path traversal: {key!r}")
        if not _SEGMENT_RE.match(segment):
            raise ValueError(f"Storage key segment {segment!r} contains illegal characters")
    return "/".join(segments)


class BlobStore(ABC):
    """The backend-agnostic blob contract shared by every storage backend."""

    @abstractmethod
    def put(self, key: str, data: bytes) -> str:
        """Store ``data`` under ``key`` and return a stable reference/URI to it."""

    @abstractmethod
    def get(self, key: str) -> bytes:
        """Return the bytes stored under ``key`` or raise :class:`BlobNotFound`."""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Return whether ``key`` currently holds a blob."""

    @abstractmethod
    def delete(self, key: str) -> None:
        """Remove ``key``. Deleting a missing key is a no-op (idempotent)."""


class LocalDiskStore(BlobStore):
    """Default backend: one file per key beneath ``root``, traversal-safe."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).expanduser().resolve()

    @property
    def root(self) -> Path:
        return self._root

    def _path(self, key: str) -> Path:
        safe = sanitize_key(key)
        path = (self._root / safe).resolve()
        # Defense in depth: even after sanitization, confirm the resolved path stays under root.
        if path != self._root and self._root not in path.parents:
            raise ValueError(f"Storage key escapes the storage root: {key!r}")
        return path

    def put(self, key: str, data: bytes) -> str:
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("LocalDiskStore.put requires bytes")
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write atomically so a concurrent reader never sees a half-written blob.
        tmp = path.with_name(f"{path.name}.tmp-{id(data)}")
        tmp.write_bytes(bytes(data))
        tmp.replace(path)
        return path.as_uri()

    def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise BlobNotFound(sanitize_key(key)) from exc

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        path = self._path(key)
        try:
            path.unlink()
        except FileNotFoundError:
            pass


@runtime_checkable
class S3ClientProtocol(Protocol):
    """Minimal S3-compatible client contract that :class:`S3Store` is written against.

    A production adapter can implement this with ``boto3``, ``aioboto3``, or a hand-rolled
    ``httpx`` + SigV4 client — none of which is a dependency here. ``get_object`` returns ``None``
    (not an exception) for a missing key so the "not found" signal is unambiguous across
    implementations; the real adapter translates an HTTP 404 / ``NoSuchKey`` into ``None``.
    """

    def put_object(self, *, bucket: str, key: str, body: bytes) -> None: ...

    def get_object(self, *, bucket: str, key: str) -> bytes | None: ...

    def head_object(self, *, bucket: str, key: str) -> bool: ...

    def delete_object(self, *, bucket: str, key: str) -> None: ...


class S3Store(BlobStore):
    """S3-compatible backend delegating to an injected :class:`S3ClientProtocol`.

    The store owns key sanitization and prefix namespacing; the injected client owns the actual
    transport. This keeps the S3 wire concerns (auth, endpoint, retries) out of the abstraction and
    lets the parity contract be tested with a pure in-memory fake.
    """

    def __init__(self, bucket: str, client: S3ClientProtocol, *, prefix: str = "") -> None:
        if not bucket:
            raise BlobStoreConfigurationError("S3Store requires a bucket name")
        self._bucket = bucket
        self._client = client
        # A prefix, if given, is itself sanitized so it cannot inject traversal.
        self._prefix = sanitize_key(prefix) if prefix else ""

    def _object_key(self, key: str) -> str:
        safe = sanitize_key(key)
        return f"{self._prefix}/{safe}" if self._prefix else safe

    def put(self, key: str, data: bytes) -> str:
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("S3Store.put requires bytes")
        object_key = self._object_key(key)
        self._client.put_object(bucket=self._bucket, key=object_key, body=bytes(data))
        return f"s3://{self._bucket}/{object_key}"

    def get(self, key: str) -> bytes:
        object_key = self._object_key(key)
        body = self._client.get_object(bucket=self._bucket, key=object_key)
        if body is None:
            raise BlobNotFound(object_key)
        return body

    def exists(self, key: str) -> bool:
        return bool(self._client.head_object(bucket=self._bucket, key=self._object_key(key)))

    def delete(self, key: str) -> None:
        self._client.delete_object(bucket=self._bucket, key=self._object_key(key))


def get_store() -> BlobStore:
    """Return the configured blob store.

    ``local`` (default) is fully working with zero setup. ``s3`` is structurally complete but has
    no default client to inject (no credentials, no S3 SDK dependency), so it raises a clear
    :class:`BlobStoreConfigurationError` instructing the operator to construct
    :class:`S3Store` with a concrete :class:`S3ClientProtocol` client. The interface itself is
    proven backend-agnostic by the parity contract tests.
    """
    backend = (settings.storage_backend or "local").strip().lower()
    if backend in {"local", "disk", "localdisk"}:
        return LocalDiskStore(settings.storage_root)
    if backend == "s3":
        raise BlobStoreConfigurationError(
            "STORAGE_BACKEND=s3 selects the S3-compatible backend, but no S3 client is wired. "
            "Construct S3Store(bucket=settings.s3_bucket, client=<your S3ClientProtocol>, "
            "prefix=settings.s3_prefix) with credentials — e.g. a boto3 or httpx+SigV4 adapter. "
            "The disk default requires no such wiring."
        )
    raise BlobStoreConfigurationError(f"Unknown STORAGE_BACKEND {backend!r}; expected 'local' or 's3'")
