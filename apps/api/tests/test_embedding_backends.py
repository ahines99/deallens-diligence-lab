"""G55 — pluggable embedding backends with vector-space isolation.

Feature hashing stays the default (and the only backend CI exercises); an operator can opt into
a local ONNX model. What these tests pin:

* configuration without the optional extra / model degrades EXPLICITLY to feature hashing
  (a note says why) — never a crash, never a silent switch;
* the producer tag (``embedding_id``) always matches the backend that made the vector, and
  retrieval only ever compares same-tag vectors — vectors from different models are never mixed;
* the backfill worker refreshes stale-method vectors after a backend change.

The neural backend itself is faked at the module seam (``onnx_embedding``): CI must not download
models or install the extra.
"""
from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import select

from src.config import settings
from src.db.session import SessionLocal
from src.models import DocumentChunk, Filing
from src.services import embedding_service, onnx_embedding, retrieval_service
from src.workers.backfill_embeddings import backfill_embeddings

_FAKE_METHOD = "onnx-local-fake1234567"


def _activate_fake_onnx(monkeypatch, dim: int = 8) -> None:
    """Simulate a configured, loadable local model without any real dependency."""
    monkeypatch.setattr(settings, "embeddings_backend", "onnx_local")
    monkeypatch.setattr(onnx_embedding, "available", lambda: (True, None))
    monkeypatch.setattr(onnx_embedding, "method", lambda: _FAKE_METHOD)
    monkeypatch.setattr(
        onnx_embedding, "embed", lambda text: [1.0] + [0.0] * (dim - 1)
    )


def test_default_backend_is_feature_hashing_and_status_is_clean():
    assert embedding_service.active_method() == embedding_service.EMBED_METHOD
    assert embedding_service.embedding_status() == {
        "backend_configured": "feature_hashing",
        "backend_active": "feature_hashing",
        "method": embedding_service.EMBED_METHOD,
        "note": None,
    }
    vector = embedding_service.embed("revenue grew")
    assert len(vector) == embedding_service.EMBED_DIM


def test_configured_onnx_without_extra_or_model_degrades_explicitly(monkeypatch):
    """The opt-in backend missing its extra/model must fall back LOUDLY, not crash or mix tags."""
    monkeypatch.setattr(settings, "embeddings_backend", "onnx_local")
    monkeypatch.setattr(settings, "embeddings_model_path", "Z:/does-not-exist")
    status = embedding_service.embedding_status()
    assert status["backend_configured"] == "onnx_local"
    assert status["backend_active"] == "feature_hashing"
    assert status["note"]  # the reason is surfaced, never hidden
    assert embedding_service.active_method() == embedding_service.EMBED_METHOD
    # embed() still works (hashing), so ingest never breaks on a misconfigured backend.
    assert len(embedding_service.embed("text")) == embedding_service.EMBED_DIM


def test_active_onnx_backend_routes_embed_and_tags_chunks(monkeypatch):
    _activate_fake_onnx(monkeypatch)
    chunk = SimpleNamespace(section="Risk", chunk_text="text", embedding=None, embedding_id=None)
    embedding_service.embed_chunk(chunk)
    assert chunk.embedding[0] == 1.0
    assert chunk.embedding_id == _FAKE_METHOD
    assert embedding_service.embedding_status()["backend_active"] == "onnx_local"


def _seed_embedded_chunk(client) -> str:
    workspace_id = client.post(
        "/api/workspaces", json={"name": "Embedding isolation", "deal_type": "buyout"}
    ).json()["id"]
    with SessionLocal() as session:
        filing = Filing(
            workspace_id=workspace_id,
            company_name="Embed Corp",
            ticker="EMB",
            cik="0000000009",
            form_type="10-K",
            filing_date="2025-02-01",
            accession_number="0000000009-25-000001",
            document_url="https://www.sec.gov/Archives/embed-10k.htm",
        )
        session.add(filing)
        session.flush()
        chunk = DocumentChunk(
            filing_id=filing.id,
            workspace_id=workspace_id,
            section="Item 1A Risk Factors",
            chunk_index=0,
            chunk_text="Customer concentration remains a material revenue risk.",
            source_url=filing.document_url,
        )
        embedding_service.embed_chunk(chunk)  # tagged with the CURRENT (hashing) method
        session.add(chunk)
        session.commit()
    return workspace_id


def test_retrieval_never_compares_vectors_across_methods(client, monkeypatch):
    """Regression guard: stored hash-method vectors must not feed the hybrid path once a
    different backend is active — cosine between different spaces is meaningless."""
    workspace_id = _seed_embedded_chunk(client)
    with SessionLocal() as session:
        assert retrieval_service.workspace_has_embeddings(session, workspace_id) is True
        _activate_fake_onnx(monkeypatch)
        assert retrieval_service.workspace_has_embeddings(session, workspace_id) is False
        assert (
            retrieval_service._vector_candidates(
                session, workspace_id, "customer concentration", k=5
            )
            == []
        )


def test_backfill_refreshes_stale_method_vectors_and_is_idempotent(client, monkeypatch):
    workspace_id = _seed_embedded_chunk(client)
    _activate_fake_onnx(monkeypatch)
    with SessionLocal() as session:
        first = backfill_embeddings(session, workspace_id=workspace_id)
        assert first == {"embedded": 1, "method": _FAKE_METHOD}
        row = session.scalars(
            select(DocumentChunk).where(DocumentChunk.workspace_id == workspace_id)
        ).one()
        assert row.embedding_id == _FAKE_METHOD
        assert row.embedding[0] == 1.0
        # The refreshed vector is now the active method, so retrieval sees it again...
        assert retrieval_service.workspace_has_embeddings(session, workspace_id) is True
        # ...and a second run has nothing to do.
        second = backfill_embeddings(session, workspace_id=workspace_id)
        assert second["embedded"] == 0
