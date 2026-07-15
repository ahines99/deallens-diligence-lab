"""Hybrid retrieval (G01) + embedding ingestion pipeline (G02).

All offline and deterministic: the local feature-hashing embedding (embedding_service) needs no
model download or network, so identical text always yields the identical vector.
"""
from __future__ import annotations

import inspect

import pytest

from src.db.session import SessionLocal
from src.models import DocumentChunk, Filing
from src.services import edgar_client, embedding_service, retrieval_service, sec_ingestion_service
from src.workers.backfill_embeddings import backfill_embeddings


# --------------------------------------------------------------------------- fixtures
def _make_workspace(client, name: str) -> str:
    return client.post(
        "/api/workspaces", json={"name": name, "deal_type": "public_equity"}
    ).json()["id"]


def _add_filing(session, ws_id: str) -> Filing:
    filing = Filing(
        workspace_id=ws_id,
        company_name="Fixture Corp",
        ticker="FIX",
        cik="0000000001",
        form_type="10-K",
        filing_date="2025-02-01",
        accession_number="0000000001-25-000001",
        document_url="https://www.sec.gov/Archives/fixture-10k.htm",
        is_synthetic=False,
    )
    session.add(filing)
    session.flush()
    return filing


# --------------------------------------------------------------------------- (a) embedding
def test_embed_is_deterministic_and_l2_normalized():
    text = "Customer concentration remains a material risk to consolidated revenue."
    first = embedding_service.embed(text)
    second = embedding_service.embed(text)
    assert first == second, "same text must produce the byte-identical vector"
    assert len(first) == embedding_service.EMBED_DIM
    norm_sq = sum(v * v for v in first)
    assert norm_sq == pytest.approx(1.0, abs=1e-9)


def test_cosine_high_for_identical_low_for_unrelated():
    risk = "Customer concentration remains a material risk to consolidated revenue."
    similar = "Concentration among our largest customers is a material revenue risk."
    unrelated = "Antarctic lithium mining logistics and polar shipping route planning."
    assert embedding_service.cosine(embedding_service.embed(risk), embedding_service.embed(risk)) == pytest.approx(1.0, abs=1e-9)
    assert embedding_service.cosine(embedding_service.embed(risk), embedding_service.embed(similar)) > 0.2
    assert embedding_service.cosine(embedding_service.embed(risk), embedding_service.embed(unrelated)) < 0.1


def test_empty_text_is_zero_vector_and_cosine_zero():
    empty = embedding_service.embed("   ")
    assert empty == [0.0] * embedding_service.EMBED_DIM
    assert embedding_service.cosine(empty, embedding_service.embed("revenue")) == 0.0


# --------------------------------------------------------------------------- (b) ingest + backfill
def test_ingest_persists_embeddings(client, monkeypatch):
    """A real ingest (EDGAR calls stubbed) stores a vector + method tag on every new chunk."""
    ws_id = _make_workspace(client, "Ingest embeds")

    section_body = (
        "Customer concentration remains a material risk. Our largest customer represented "
        "approximately 14 percent of consolidated revenue during the fiscal year, and the loss "
        "of this customer would materially harm our operating results and future prospects. "
        "We continue to depend on a small number of large enterprise relationships for a "
        "significant portion of our recurring subscription revenue base each period."
    )
    meta = edgar_client.FilingMeta(
        form="10-K",
        filing_date="2025-02-01",
        accession="0000000010-25-000001",
        primary_document="fix-10k.htm",
        primary_doc_url="https://www.sec.gov/Archives/fix-10k.htm",
        report_date="2024-12-31",
    )
    monkeypatch.setattr(
        sec_ingestion_service.edgar_client, "resolve_ticker",
        lambda t: {"cik": "0000000010", "name": "Embed Test Co", "ticker": "EMB"},
    )
    monkeypatch.setattr(
        sec_ingestion_service.edgar_client, "get_submissions",
        lambda cik: {"sicDescription": "Prepackaged Software"},
    )
    monkeypatch.setattr(
        sec_ingestion_service.edgar_client, "recent_filings",
        lambda cik, forms, limit: [meta] if "10-K" in forms else [],
    )
    monkeypatch.setattr(
        sec_ingestion_service.edgar_client, "get_company_facts",
        lambda cik: (_ for _ in ()).throw(edgar_client.EdgarError("no facts")),
    )
    monkeypatch.setattr(
        sec_ingestion_service.edgar_client, "fetch_document_text", lambda url: "raw 10-K text"
    )
    monkeypatch.setattr(
        sec_ingestion_service, "extract_sections",
        lambda text: {"Risk Factors (Item 1A)": section_body},
    )

    with SessionLocal() as session:
        sec_ingestion_service.ingest_company(session, ws_id, "EMB")
        session.commit()

    with SessionLocal() as session:
        chunks = list(
            session.scalars(select_chunks(ws_id))
        )
    assert chunks, "ingest should have produced at least one chunk"
    for chunk in chunks:
        assert chunk.embedding is not None
        assert len(chunk.embedding) == embedding_service.EMBED_DIM
        assert chunk.embedding_id == embedding_service.EMBED_METHOD


def test_backfill_fills_only_nulls_and_is_idempotent(client):
    ws_id = _make_workspace(client, "Backfill idempotency")
    with SessionLocal() as session:
        filing = _add_filing(session, ws_id)
        # Two chunks with no embedding (legacy) + one already embedded.
        session.add_all(
            [
                DocumentChunk(
                    filing_id=filing.id, workspace_id=ws_id, section="Item 1A",
                    chunk_index=0, chunk_text="Customer concentration is a material risk.",
                ),
                DocumentChunk(
                    filing_id=filing.id, workspace_id=ws_id, section="Item 7",
                    chunk_index=1, chunk_text="Revenue increased twelve percent year over year.",
                ),
            ]
        )
        pre_embedded = DocumentChunk(
            filing_id=filing.id, workspace_id=ws_id, section="Item 8",
            chunk_index=2, chunk_text="Total operating expenses grew more slowly than revenue.",
        )
        embedding_service.embed_chunk(pre_embedded)
        frozen_vector = list(pre_embedded.embedding)
        session.add(pre_embedded)
        session.commit()

    with SessionLocal() as session:
        first = backfill_embeddings(session, workspace_id=ws_id)
    assert first["embedded"] == 2, "only the two null-embedding chunks are touched"

    with SessionLocal() as session:
        chunks = list(session.scalars(select_chunks(ws_id)))
        assert len(chunks) == 3, "backfill must not create or drop rows"
        assert all(c.embedding is not None for c in chunks)
        assert all(c.embedding_id == embedding_service.EMBED_METHOD for c in chunks)
        # The already-embedded chunk was left byte-for-byte alone.
        untouched = next(c for c in chunks if c.chunk_index == 2)
        assert untouched.embedding == frozen_vector

    # Re-running is a no-op: nothing left null, no duplicates.
    with SessionLocal() as session:
        second = backfill_embeddings(session, workspace_id=ws_id)
    assert second["embedded"] == 0
    with SessionLocal() as session:
        assert len(list(session.scalars(select_chunks(ws_id)))) == 3


# --------------------------------------------------------------------------- (c) RRF fusion
class _FakeChunk:
    def __init__(self, chunk_id: str) -> None:
        self.id = chunk_id


def test_rrf_ranks_a_chunk_both_rankers_like_above_a_single_ranker_favorite():
    both = retrieval_service.RetrievedChunk(chunk=_FakeChunk("both"), score=9.0)
    bm25_only = retrieval_service.RetrievedChunk(chunk=_FakeChunk("bm25only"), score=8.0)
    vec_only = retrieval_service.RetrievedChunk(chunk=_FakeChunk("veconly"), score=0.9)

    # 'both' is rank #1 in BM25 and rank #1 in the vector list; each rival is a rank-#1 in only
    # one list. RRF should still lift the doubly-endorsed chunk to the top.
    fused = retrieval_service._reciprocal_rank_fusion(
        [[both, bm25_only], [both, vec_only]], k=3
    )
    assert [r.chunk.id for r in fused][0] == "both"
    # Two reciprocal-rank contributions beat one.
    top = next(r for r in fused if r.chunk.id == "both")
    assert top.score == pytest.approx(2.0 / (retrieval_service._RRF_K + 1), abs=1e-6)


def test_hybrid_returns_fused_results_when_embeddings_exist(client):
    ws_id = _make_workspace(client, "Hybrid fusion")
    with SessionLocal() as session:
        filing = _add_filing(session, ws_id)
        for idx, text in enumerate(
            [
                "Customer concentration remains a material risk to consolidated revenue.",
                "Revenue increased twelve percent driven by subscription growth.",
                "Our supply chain depends on a limited number of component vendors.",
            ]
        ):
            chunk = DocumentChunk(
                filing_id=filing.id, workspace_id=ws_id, section=f"Item {idx}",
                chunk_index=idx, chunk_text=text,
            )
            embedding_service.embed_chunk(chunk)
            session.add(chunk)
        session.commit()

    with SessionLocal() as session:
        assert retrieval_service.workspace_has_embeddings(session, ws_id) is True
        ranked = retrieval_service.retrieve_hybrid(
            session, ws_id, "customer concentration revenue risk", k=3
        )
    assert ranked, "hybrid retrieval should return fused matches"
    assert "concentration" in ranked[0].chunk.chunk_text.lower()
    # Fused scores are RRF magnitudes, best-first.
    assert ranked == sorted(ranked, key=lambda r: r.score, reverse=True)


# --------------------------------------------------------------------------- (d) BM25 contract
def test_retrieve_bm25_signature_and_return_shape_unchanged(client):
    sig = inspect.signature(retrieval_service.retrieve)
    assert list(sig.parameters) == ["session", "workspace_id", "query", "k"]
    assert sig.parameters["k"].default == 5

    ws_id = _make_workspace(client, "BM25 contract")
    with SessionLocal() as session:
        filing = _add_filing(session, ws_id)
        session.add(
            DocumentChunk(
                filing_id=filing.id, workspace_id=ws_id, section="Item 1A Risk Factors",
                chunk_index=0,
                chunk_text="Customer concentration remains a material risk to revenue.",
            )
        )
        session.commit()
    with SessionLocal() as session:
        ranked = retrieval_service.retrieve(session, ws_id, "customer concentration risk", k=2)
    assert ranked and isinstance(ranked, list)
    assert isinstance(ranked[0], retrieval_service.RetrievedChunk)
    assert hasattr(ranked[0], "chunk") and isinstance(ranked[0].score, float)


# --------------------------------------------------------------------------- (e) fallback
def test_hybrid_falls_back_to_bm25_when_no_embeddings(client):
    ws_id = _make_workspace(client, "Hybrid fallback")
    with SessionLocal() as session:
        filing = _add_filing(session, ws_id)
        session.add_all(
            [
                DocumentChunk(
                    filing_id=filing.id, workspace_id=ws_id, section="Item 1A",
                    chunk_index=0,
                    chunk_text="Customer concentration remains a material risk to revenue.",
                ),
                DocumentChunk(
                    filing_id=filing.id, workspace_id=ws_id, section="Item 7",
                    chunk_index=1,
                    chunk_text="Revenue increased twelve percent year over year.",
                ),
            ]
        )
        session.commit()

    with SessionLocal() as session:
        assert retrieval_service.workspace_has_embeddings(session, ws_id) is False
        bm25 = retrieval_service.retrieve(session, ws_id, "customer concentration risk", k=5)
        hybrid = retrieval_service.retrieve_hybrid(session, ws_id, "customer concentration risk", k=5)
    assert bm25, "BM25 should find the risk chunk"
    assert [r.chunk.id for r in hybrid] == [r.chunk.id for r in bm25], (
        "with no embeddings, hybrid must reproduce BM25 ordering"
    )


# --------------------------------------------------------------------------- helpers
def select_chunks(ws_id: str):
    from sqlalchemy import select

    return select(DocumentChunk).where(DocumentChunk.workspace_id == ws_id).order_by(
        DocumentChunk.chunk_index
    )
