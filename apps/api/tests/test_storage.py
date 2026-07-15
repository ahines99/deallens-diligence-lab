"""G40 — blob-storage abstraction: backend-parity contract tests.

The central deliverable is a *parity* test: the same put/get/exists/delete contract behaves
identically on the local-disk default and on the S3-compatible backend (exercised with an
in-memory fake S3 client injected into ``S3Store``). Also covers key-sanitization traversal
safety, consistent missing-key semantics across backends, the ``get_store`` factory, and a
round-trip through the rewired EDGAR cache (which now flows through the abstraction).
"""
from __future__ import annotations

import pytest

from src.config import settings
from src.services import edgar_client
from src.services.storage_service import (
    BlobNotFound,
    BlobStoreConfigurationError,
    LocalDiskStore,
    S3Store,
    get_store,
    sanitize_key,
)


class FakeS3Client:
    """In-memory stand-in for a real S3-compatible client (S3ClientProtocol).

    Proves ``S3Store`` is backend-agnostic without a live bucket. ``get_object`` returns ``None``
    for a missing key — the same "not found" signal a real adapter derives from an HTTP 404.
    """

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(self, *, bucket: str, key: str, body: bytes) -> None:
        self.objects[(bucket, key)] = bytes(body)

    def get_object(self, *, bucket: str, key: str) -> bytes | None:
        return self.objects.get((bucket, key))

    def head_object(self, *, bucket: str, key: str) -> bool:
        return (bucket, key) in self.objects

    def delete_object(self, *, bucket: str, key: str) -> None:
        self.objects.pop((bucket, key), None)


@pytest.fixture(params=["local", "s3"])
def store(request, tmp_path):
    """A ready-to-use BlobStore for each backend, parametrized for parity testing."""
    if request.param == "local":
        return LocalDiskStore(tmp_path)
    return S3Store(bucket="test-bucket", client=FakeS3Client(), prefix="blobs")


# --- (a) backend parity: the core contract test ----------------------------
BLOBS = [
    b"hello world",
    b"",  # empty payload must round-trip too
    b"\x00\x01\x02\xff\xfe binary safe",
    "unicode: café — €1,000".encode("utf-8"),
    b"x" * 100_000,  # a larger blob
]


@pytest.mark.parametrize("data", BLOBS, ids=["ascii", "empty", "binary", "unicode", "large"])
def test_put_get_roundtrip_is_byte_identical_on_every_backend(store, data):
    ref = store.put("docs/report.bin", data)
    assert isinstance(ref, str) and ref  # a usable reference/URI is returned
    assert store.get("docs/report.bin") == data


def test_exists_and_delete_are_consistent_across_backends(store):
    assert store.exists("a/b/c.txt") is False
    store.put("a/b/c.txt", b"payload")
    assert store.exists("a/b/c.txt") is True
    store.delete("a/b/c.txt")
    assert store.exists("a/b/c.txt") is False


def test_delete_missing_key_is_idempotent_noop_across_backends(store):
    # Deleting something that was never written must not raise on any backend.
    store.delete("never/written.bin")
    store.put("k", b"v")
    store.delete("k")
    store.delete("k")
    assert store.exists("k") is False


def test_overwrite_replaces_bytes_across_backends(store):
    store.put("k", b"first")
    store.put("k", b"second")
    assert store.get("k") == b"second"


# --- (c) missing-key semantics are consistent across backends --------------
def test_get_missing_key_raises_blob_not_found_across_backends(store):
    with pytest.raises(BlobNotFound):
        store.get("does/not/exist.bin")


# --- (b) key sanitization: traversal cannot escape the storage root --------
@pytest.mark.parametrize(
    "bad_key",
    [
        "../escape",
        "../../etc/passwd",
        "a/../../b",
        "/absolute/path",
        "C:/windows/system32",
        "..\\..\\escape",
        "",
        ".",
        "..",
        "foo/../../bar",
        "with\x00nul",
    ],
)
def test_sanitize_key_rejects_traversal_and_absolute_paths(bad_key):
    with pytest.raises(ValueError):
        sanitize_key(bad_key)


def test_sanitize_key_normalizes_separators_and_accepts_safe_keys():
    assert sanitize_key("edgar-cache/json-abc.def") == "edgar-cache/json-abc.def"
    assert sanitize_key("a\\b\\c") == "a/b/c"
    assert sanitize_key("a//b///c") == "a/b/c"


def test_local_disk_store_cannot_escape_root_even_via_get(tmp_path):
    store = LocalDiskStore(tmp_path)
    store.put("safe.bin", b"ok")
    # A traversal key never resolves to a file outside root; it raises before any I/O.
    for bad in ["../outside.bin", "../../secret", "/etc/passwd"]:
        with pytest.raises(ValueError):
            store.get(bad)
        with pytest.raises(ValueError):
            store.put(bad, b"nope")
    # Nothing leaked above the root directory.
    assert list(tmp_path.parent.glob("outside.bin")) == []


def test_local_disk_store_namespaces_keys_as_files_under_root(tmp_path):
    store = LocalDiskStore(tmp_path)
    store.put("ns/sub/item.bin", b"data")
    assert (tmp_path / "ns" / "sub" / "item.bin").read_bytes() == b"data"


# --- get_store() factory ----------------------------------------------------
def test_get_store_defaults_to_local_disk(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "storage_root", str(tmp_path))
    resolved = get_store()
    assert isinstance(resolved, LocalDiskStore)
    resolved.put("k", b"v")
    assert (tmp_path / "k").read_bytes() == b"v"


def test_get_store_s3_without_client_raises_clear_configuration_error(monkeypatch):
    monkeypatch.setattr(settings, "storage_backend", "s3")
    with pytest.raises(BlobStoreConfigurationError) as excinfo:
        get_store()
    assert "S3Store" in str(excinfo.value)


def test_get_store_rejects_unknown_backend(monkeypatch):
    monkeypatch.setattr(settings, "storage_backend", "gcs")
    with pytest.raises(BlobStoreConfigurationError):
        get_store()


# --- (d) EDGAR cache round-trips through the abstraction --------------------
def test_edgar_cache_roundtrips_through_blob_store(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "edgar_cache_ttl_seconds", 3600)
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "storage_root", str(tmp_path))
    url = "https://data.sec.gov/submissions/CIK0000320193.json"
    assert edgar_client._cache_read(url, "json") is None  # cold
    edgar_client._cache_write(url, "json", '{"name": "Apple Inc."}')
    assert edgar_client._cache_read(url, "json") == '{"name": "Apple Inc."}'


def test_edgar_cache_disabled_when_ttl_is_zero(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "edgar_cache_ttl_seconds", 0)
    monkeypatch.setattr(settings, "storage_root", str(tmp_path))
    url = "https://example.com/x"
    edgar_client._cache_write(url, "doc", "should not persist")
    assert edgar_client._cache_read(url, "doc") is None
    assert list(tmp_path.rglob("*")) == []  # nothing written at all


def test_edgar_cache_entry_past_ttl_is_ignored(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "edgar_cache_ttl_seconds", 3600)
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "storage_root", str(tmp_path))
    url = "https://example.com/stale"
    edgar_client._cache_write(url, "html", "<html>old</html>")
    # Age the entry beyond the TTL by shrinking the window; the envelope timestamp is authoritative.
    monkeypatch.setattr(settings, "edgar_cache_ttl_seconds", 0.0001)
    import time

    time.sleep(0.01)
    assert edgar_client._cache_read(url, "html") is None


def test_edgar_cache_roundtrips_through_injected_s3_backend(monkeypatch):
    """The rewired cache is genuinely backend-agnostic: swap in S3Store and it still round-trips."""
    s3_store = S3Store(bucket="edgar", client=FakeS3Client(), prefix="cache")
    monkeypatch.setattr(settings, "edgar_cache_ttl_seconds", 3600)
    monkeypatch.setattr(edgar_client, "get_store", lambda: s3_store)
    url = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"
    assert edgar_client._cache_read(url, "json") is None
    edgar_client._cache_write(url, "json", '{"facts": {}}')
    assert edgar_client._cache_read(url, "json") == '{"facts": {}}'
