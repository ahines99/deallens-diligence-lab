"""G83 — pgvector parity: the Postgres fast path must rank exactly like the Python path.

The JSON ``embedding`` column stays the source of truth on every backend. On PostgreSQL with
the pgvector extension (migration ``d5f2b8c3a1e9``) retrieval lazily mirrors those vectors into
``document_chunks.embedding_vector`` and ranks with the DB-side cosine operator. What these
tests pin:

* on SQLite (the default test DB) the fast path is NEVER taken and dispatch equals the pure
  Python ranking;
* on real Postgres (the CI matrix points ``DEALLENS_TEST_DATABASE_URL`` at a pgvector-enabled
  service container — see conftest) the fast path's ranking and scores match the Python path,
  the vector column is lazily backfilled, and a runtime failure falls back to the Python path
  without corrupting the transaction;
* method-tag discipline survives the fast path: a stored vector from a foreign embedding
  method never surfaces and is never even cast into the vector column.

The Postgres-only tests skip cleanly on SQLite (and on a Postgres database where the migration
skipped the column because the extension was unavailable — the documented degraded mode).
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from src.db.session import SessionLocal, engine
from src.models import DocumentChunk, Filing
from src.services import embedding_service, retrieval_service

_IS_POSTGRES = engine.dialect.name == "postgresql"

_postgres_only = pytest.mark.skipif(
    not _IS_POSTGRES,
    reason="pgvector parity needs real Postgres (CI matrix sets DEALLENS_TEST_DATABASE_URL)",
)

_QUERY = "customer concentration revenue risk"
_TEXTS = [
    "Customer concentration remains a material risk to consolidated revenue.",
    "Revenue increased twelve percent driven by subscription growth.",
    "Our supply chain depends on a limited number of component vendors.",
    "Operating expenses grew more slowly than revenue during the period.",
    "The credit facility contains customary covenants and restrictions.",
]


def _seed_workspace(client, name: str, texts: list[str]) -> str:
    ws_id = client.post(
        "/api/workspaces", json={"name": name, "deal_type": "public_equity"}
    ).json()["id"]
    with SessionLocal() as session:
        filing = Filing(
            workspace_id=ws_id,
            company_name="Parity Corp",
            ticker="PAR",
            cik="0000000083",
            form_type="10-K",
            filing_date="2025-02-01",
            accession_number="0000000083-25-000001",
            document_url="https://www.sec.gov/Archives/parity-10k.htm",
            is_synthetic=False,
        )
        session.add(filing)
        session.flush()
        for idx, chunk_text in enumerate(texts):
            chunk = DocumentChunk(
                filing_id=filing.id, workspace_id=ws_id, section=f"Item {idx}",
                chunk_index=idx, chunk_text=chunk_text,
            )
            embedding_service.embed_chunk(chunk)
            session.add(chunk)
        session.commit()
    return ws_id


def _require_fast_path(session) -> None:
    if not retrieval_service._pgvector_ready(session):
        pytest.skip(
            "embedding_vector column absent (pgvector extension unavailable or database not "
            "migrated) — the documented degraded mode; nothing to parity-test"
        )


def _ids(ranked) -> list[str]:
    return [item.chunk.id for item in ranked]


# --------------------------------------------------------------------------- SQLite behavior
def test_sqlite_never_takes_the_fast_path_and_matches_python(client, monkeypatch):
    if _IS_POSTGRES:
        pytest.skip("SQLite-only assertion; the Postgres matrix runs the parity tests below")
    ws_id = _seed_workspace(client, "pgvector sqlite", _TEXTS)

    def _forbidden(*args, **kwargs):
        raise AssertionError("pgvector fast path must never run on SQLite")

    with SessionLocal() as session:
        assert retrieval_service._pgvector_ready(session) is False
        monkeypatch.setattr(retrieval_service, "_vector_candidates_pg", _forbidden)
        dispatched = retrieval_service._vector_candidates(session, ws_id, _QUERY, k=5)
        python_ranked = retrieval_service._vector_candidates_python(
            session, ws_id, embedding_service.embed(_QUERY), 5
        )
    assert dispatched, "fixture should produce vector candidates"
    assert _ids(dispatched) == _ids(python_ranked)
    assert [r.score for r in dispatched] == [r.score for r in python_ranked]


# --------------------------------------------------------------------------- Postgres parity
@_postgres_only
def test_fast_path_ranking_and_scores_match_python_path(client):
    ws_id = _seed_workspace(client, "pgvector parity", _TEXTS)
    with SessionLocal() as session:
        _require_fast_path(session)
        query_vector = embedding_service.embed(_QUERY)
        fast = retrieval_service._vector_candidates_pg(session, ws_id, query_vector, 10)
        python_ranked = retrieval_service._vector_candidates_python(
            session, ws_id, query_vector, 10
        )
        assert fast is not None, "fast path must not fall back on a pgvector-enabled database"
        assert len(fast) >= 3, "fixture should produce several positive-similarity candidates"
        # THE parity contract: identical ranking, and scores equal to Python's fp64 cosine
        # within float32 storage tolerance.
        assert _ids(fast) == _ids(python_ranked)
        for fast_item, py_item in zip(fast, python_ranked):
            assert fast_item.score == pytest.approx(py_item.score, abs=1e-3)
        # The public dispatch uses the fast path and agrees with it.
        dispatched = retrieval_service._vector_candidates(session, ws_id, _QUERY, 10)
        assert _ids(dispatched) == _ids(fast)
        session.rollback()


@_postgres_only
def test_fast_path_lazily_backfills_the_vector_column(client):
    ws_id = _seed_workspace(client, "pgvector backfill", _TEXTS)
    count_sql = text(
        "SELECT count(*) FROM document_chunks "
        "WHERE workspace_id = :ws AND embedding_vector IS NOT NULL"
    )
    with SessionLocal() as session:
        _require_fast_path(session)
        assert session.execute(count_sql, {"ws": ws_id}).scalar_one() == 0, (
            "ingest writes only the JSON source of truth; the vector column starts NULL"
        )
        first = retrieval_service._vector_candidates(session, ws_id, _QUERY, 5)
        assert session.execute(count_sql, {"ws": ws_id}).scalar_one() == len(_TEXTS)
        # Idempotent: a second retrieval re-syncs nothing and ranks identically.
        second = retrieval_service._vector_candidates(session, ws_id, _QUERY, 5)
        assert _ids(first) == _ids(second)
        session.rollback()


@_postgres_only
def test_foreign_method_vector_never_surfaces_from_fast_path(client):
    """Method-tag discipline: a vector produced by a different embedding method must neither
    rank (its space is incomparable) nor even be cast into the vector column."""
    ws_id = _seed_workspace(client, "pgvector method discipline", _TEXTS[:2])
    with SessionLocal() as session:
        filing_id = session.execute(
            text("SELECT filing_id FROM document_chunks WHERE workspace_id = :ws LIMIT 1"),
            {"ws": ws_id},
        ).scalar_one()
        # A decoy whose text IS the query — it would rank first if the tag guard ever leaked.
        decoy = DocumentChunk(
            filing_id=filing_id, workspace_id=ws_id, section="Decoy",
            chunk_index=99, chunk_text=_QUERY,
        )
        embedding_service.embed_chunk(decoy)
        decoy.embedding_id = "foreign-method-v0"
        session.add(decoy)
        session.commit()
        decoy_id = decoy.id

    with SessionLocal() as session:
        _require_fast_path(session)
        fast = retrieval_service._vector_candidates_pg(
            session, ws_id, embedding_service.embed(_QUERY), 10
        )
        assert fast is not None
        assert fast, "same-method chunks should still rank"
        assert decoy_id not in _ids(fast)
        still_null = session.execute(
            text("SELECT embedding_vector IS NULL FROM document_chunks WHERE id = :id"),
            {"id": decoy_id},
        ).scalar_one()
        assert still_null is True, "lazy sync must not cast foreign-method vectors"
        session.rollback()


@_postgres_only
def test_runtime_failure_falls_back_to_python_path(client, monkeypatch):
    ws_id = _seed_workspace(client, "pgvector fallback", _TEXTS[:3])
    with SessionLocal() as session:
        _require_fast_path(session)
        python_ranked = retrieval_service._vector_candidates_python(
            session, ws_id, embedding_service.embed(_QUERY), 5
        )
        # Break the fast path at runtime: the SAVEPOINT must absorb the error and dispatch
        # must silently-but-loggedly serve the Python ranking from the same transaction.
        monkeypatch.setattr(
            retrieval_service, "_PG_SYNC_SQL", text("SELECT no_such_column FROM no_such_table")
        )
        dispatched = retrieval_service._vector_candidates(session, ws_id, _QUERY, 5)
        assert _ids(dispatched) == _ids(python_ranked)
        assert [r.score for r in dispatched] == [r.score for r in python_ranked]
        session.rollback()
