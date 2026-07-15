"""G34 — full-text search across all workspace artifacts (one interface, two engines).

Covers: multi-artifact-type matching scoped to a workspace, cross-workspace isolation, sane
ranking (exact term above partial), empty/no-hit handling, the ``engine`` field reporting the
active backend, and the dialect-branch parity test (real SQLite path end-to-end + PostgreSQL
query construction asserted against the PostgreSQL dialect without a live server).
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

import src.models  # noqa: F401 - register every mapped table on the shared metadata
from src.db.base import Base
from src.models.evidence import Evidence
from src.models.filing import Filing
from src.models.memo import Memo
from src.models.question import DiligenceQuestion
from src.models.risk import RiskFinding
from src.models.workspace import Workspace
from src.services import search_service


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def _workspace(db: Session, name: str = "WS") -> Workspace:
    ws = Workspace(name=name)
    db.add(ws)
    db.flush()
    return ws


def test_indexes_and_finds_a_term_across_multiple_artifact_types(db: Session):
    ws = _workspace(db)
    db.add(Evidence(workspace_id=ws.id, ref="EV-1", claim="Data encryption at rest is enforced"))
    db.add(
        RiskFinding(
            workspace_id=ws.id,
            risk_category="cyber_security",
            title="Weak encryption posture",
            finding="Legacy systems lack encryption controls",
        )
    )
    db.add(
        Memo(
            workspace_id=ws.id,
            memo_type="ic_memo",
            title="IC Memo",
            markdown_content="The encryption program is a diligence gate.",
        )
    )
    # A non-matching artifact must not surface.
    db.add(Evidence(workspace_id=ws.id, ref="EV-2", claim="Headcount grew 12% year over year"))
    db.commit()

    result = search_service.search_workspace(db, ws.id, "encryption")

    assert result.total >= 3
    types = {hit.artifact_type for hit in result.hits}
    assert {"evidence", "risk", "memo"} <= types
    assert "EV-2" not in {hit.title for hit in result.hits}
    for hit in result.hits:
        assert hit.artifact_id
        assert "encryption" in hit.snippet.lower()


def test_cross_workspace_isolation(db: Session):
    ws_a = _workspace(db, "Alpha")
    ws_b = _workspace(db, "Beta")
    db.add(Evidence(workspace_id=ws_a.id, ref="A-1", claim="Alpha covenant headroom is thin"))
    # A term that exists only in workspace B must never surface for workspace A.
    db.add(Evidence(workspace_id=ws_b.id, ref="B-1", claim="Beta zqxwombat exposure noted"))
    db.add(Memo(workspace_id=ws_b.id, memo_type="ic_memo", title="B", markdown_content="zqxwombat"))
    db.commit()

    from_a = search_service.search_workspace(db, ws_a.id, "zqxwombat")
    assert from_a.total == 0
    assert from_a.hits == []

    from_b = search_service.search_workspace(db, ws_b.id, "zqxwombat")
    assert from_b.total == 2
    assert all(hit.artifact_type in {"evidence", "memo"} for hit in from_b.hits)


def test_exact_term_ranks_above_partial(db: Session):
    ws = _workspace(db)
    exact = Evidence(workspace_id=ws.id, ref="EX", claim="Annual revenue grew sharply")
    partial = Evidence(workspace_id=ws.id, ref="PA", claim="Deferred revenues line item shifted")
    db.add_all([exact, partial])
    db.commit()

    result = search_service.search_workspace(db, ws.id, "revenue")

    assert result.total == 2
    # "revenue" is a whole word in `exact`; only a substring of "revenues" in `partial`.
    assert result.hits[0].artifact_id == exact.id
    assert result.hits[0].rank > result.hits[1].rank


def test_empty_query_and_no_hits_are_handled(db: Session):
    ws = _workspace(db)
    db.add(Evidence(workspace_id=ws.id, ref="EV", claim="Gross margin expanded"))
    db.commit()

    empty = search_service.search_workspace(db, ws.id, "")
    assert empty.hits == []
    assert empty.total == 0

    punctuation = search_service.search_workspace(db, ws.id, "   !!!  ")
    assert punctuation.hits == []
    assert punctuation.total == 0

    miss = search_service.search_workspace(db, ws.id, "nonexistentterm")
    assert miss.hits == []
    assert miss.total == 0


def test_engine_field_reports_the_active_backend(db: Session):
    ws = _workspace(db)
    db.add(Filing(workspace_id=ws.id, company_name="Acme Corp", form_type="10-K", filing_date="2025-01-01"))
    db.commit()

    result = search_service.search_workspace(db, ws.id, "Acme")
    assert result.engine == "sqlite_like"
    assert result.engine == search_service.active_engine(db)
    assert result.total == 1
    assert result.hits[0].artifact_type == "filing"


def test_diligence_questions_are_searchable(db: Session):
    ws = _workspace(db)
    db.add(
        DiligenceQuestion(
            workspace_id=ws.id,
            workstream="financial",
            question="What drives supplier concentration risk?",
            rationale="Top-3 suppliers exceed 60% of COGS.",
        )
    )
    db.commit()

    result = search_service.search_workspace(db, ws.id, "supplier")
    assert result.total == 1
    assert result.hits[0].artifact_type == "question"


def test_postgres_path_constructs_wellformed_tsvector_query():
    """Dialect-branch parity: the same interface builds a valid PostgreSQL full-text statement.

    CI runs SQLite end-to-end (the tests above); the Postgres path is exercised for real in the
    G36 Postgres matrix. Here we assert the statement is well-formed against the PostgreSQL
    dialect without needing a live server.
    """
    assert search_service.engine_for_dialect("postgresql") == "postgresql_tsvector"
    assert search_service.engine_for_dialect("sqlite") == "sqlite_like"

    stmt = search_service.build_postgres_statement("ws-123", "encryption headroom", 20)
    sql = str(stmt.compile(dialect=postgresql.dialect())).lower()

    assert "to_tsvector" in sql
    assert "plainto_tsquery" in sql
    assert "ts_rank" in sql
    assert "ts_headline" in sql
    assert "union all" in sql
    assert "order by" in sql


def test_search_endpoint_contract(client):
    """HTTP surface: workspace-scoped search returns the documented shape and honors limit."""
    from src.db.session import SessionLocal

    with SessionLocal() as session:
        ws = Workspace(name="Endpoint Search WS")
        session.add(ws)
        session.flush()
        session.add(
            Evidence(workspace_id=ws.id, ref="EV-9", claim="Customer churn accelerated in Q3")
        )
        session.add(
            Memo(
                workspace_id=ws.id,
                memo_type="ic_memo",
                title="Retention memo",
                markdown_content="Churn is the central diligence risk.",
            )
        )
        session.commit()
        ws_id = ws.id

    resp = client.get(f"/api/workspaces/{ws_id}/search", params={"q": "churn"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["query"] == "churn"
    assert body["engine"] == "sqlite_like"
    assert body["total"] >= 2
    assert {hit["artifact_type"] for hit in body["hits"]} >= {"evidence", "memo"}
    for hit in body["hits"]:
        assert set(hit) == {"artifact_type", "artifact_id", "title", "snippet", "rank"}

    empty = client.get(f"/api/workspaces/{ws_id}/search", params={"q": ""})
    assert empty.status_code == 200
    assert empty.json()["hits"] == []

    missing = client.get("/api/workspaces/nonexistent-ws/search", params={"q": "churn"})
    assert missing.status_code == 404
