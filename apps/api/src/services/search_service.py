"""G34 — full-text search across all workspace artifacts, behind one interface.

``search_workspace(session, workspace_id, query, limit)`` ranks matches over every searchable
artifact (evidence, risk findings, diligence questions, memos, filings, document chunks), scoped
to a single workspace, and returns hits shaped ``{artifact_type, artifact_id, title, snippet,
rank}`` plus the active ``engine``.

Design: **query-time search over the live tables** (no separate index table, no sync triggers,
no background worker). A workspace-scoped scan/UNION is evaluated per request, so the index can
never go stale and the behavior is identical on migrated and ``create_all`` databases. Two
backends sit behind the one interface, selected by dialect:

* SQLite (and any non-Postgres engine): a deterministic tokenized LIKE scan scored in Python —
  whole-word matches dominate partial substring matches so an exact term ranks above a partial
  one.
* PostgreSQL: ``to_tsvector`` / ``plainto_tsquery`` with ``ts_rank`` and ``ts_headline`` snippets,
  built as a single ``UNION ALL`` statement (see :func:`build_postgres_statement`).

Parity is by construction: both paths implement the same signature and return the same result
shape; exact ranking numbers differ per engine but the relevance ordering is sane on both. CI runs
on SQLite (end-to-end), and the Postgres query construction is asserted directly against the
PostgreSQL dialect without needing a live server (exercised for real in the G36 Postgres matrix).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import String, cast, func, literal, or_, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from src.models.document import DocumentChunk
from src.models.evidence import Evidence
from src.models.filing import Filing
from src.models.memo import Memo
from src.models.question import DiligenceQuestion
from src.models.risk import RiskFinding

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_SNIPPET_RADIUS = 60
_MAX_SNIPPET = 200
_DEFAULT_LIMIT = 20


@dataclass(frozen=True)
class _Source:
    """One searchable artifact table and the columns that make up its text."""

    artifact_type: str
    model: type
    id_col: InstrumentedAttribute
    title_col: InstrumentedAttribute
    body_cols: tuple[InstrumentedAttribute, ...]


# Every artifact table is scoped by ``workspace_id`` (see the models). Body columns include the
# title column so a term in the title is also matched by the body scan.
_SOURCES: tuple[_Source, ...] = (
    _Source(
        "evidence", Evidence, Evidence.id, Evidence.claim,
        (Evidence.claim, Evidence.evidence_text, Evidence.source_name, Evidence.source_section),
    ),
    _Source(
        "risk", RiskFinding, RiskFinding.id, RiskFinding.title,
        (RiskFinding.title, RiskFinding.finding, RiskFinding.follow_up_question),
    ),
    _Source(
        "question", DiligenceQuestion, DiligenceQuestion.id, DiligenceQuestion.question,
        (DiligenceQuestion.question, DiligenceQuestion.rationale),
    ),
    _Source(
        "memo", Memo, Memo.id, Memo.title,
        (Memo.title, Memo.markdown_content),
    ),
    _Source(
        "filing", Filing, Filing.id, Filing.company_name,
        (Filing.company_name, Filing.form_type, Filing.ticker, Filing.cik),
    ),
    _Source(
        "document_chunk", DocumentChunk, DocumentChunk.id, DocumentChunk.section,
        (DocumentChunk.section, DocumentChunk.chunk_text),
    ),
)


@dataclass
class SearchHit:
    artifact_type: str
    artifact_id: str
    title: str
    snippet: str
    rank: float


@dataclass
class SearchResult:
    query: str
    hits: list[SearchHit]
    engine: str
    total: int


def engine_for_dialect(dialect: str) -> str:
    """The backend label reported in the ``engine`` field for a given SQLAlchemy dialect."""
    if dialect == "postgresql":
        return "postgresql_tsvector"
    if dialect == "sqlite":
        return "sqlite_like"
    return f"{dialect}_like"


def active_engine(session: Session) -> str:
    return engine_for_dialect(_dialect_name(session))


def _dialect_name(session: Session) -> str:
    bind = session.bind
    return bind.dialect.name if bind is not None else "sqlite"


def _tokenize(text: str | None) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())


def _truncate(text: str, length: int) -> str:
    text = text.strip()
    if len(text) <= length:
        return text
    return text[: max(0, length - 1)].rstrip() + "…"


def search_workspace(
    session: Session, workspace_id: str, query: str, limit: int = _DEFAULT_LIMIT
) -> SearchResult:
    """Rank searchable artifacts in one workspace against ``query``.

    Returns at most ``limit`` hits ordered by descending relevance. An empty / all-punctuation
    query yields no hits (never an error). Results are always scoped to ``workspace_id``.
    """
    engine = active_engine(session)
    tokens = _tokenize(query)
    limit = max(1, limit)
    if not tokens:
        return SearchResult(query=query, hits=[], engine=engine, total=0)

    if _dialect_name(session) == "postgresql":
        hits = _search_postgres(session, workspace_id, query, limit)
        return SearchResult(query=query, hits=hits, engine=engine, total=len(hits))

    all_hits = _search_generic(session, workspace_id, tokens, query)
    return SearchResult(query=query, hits=all_hits[:limit], engine=engine, total=len(all_hits))


def _search_generic(
    session: Session, workspace_id: str, tokens: list[str], query: str
) -> list[SearchHit]:
    """Deterministic tokenized LIKE scan scored in Python (SQLite / non-Postgres engines)."""
    query_low = query.lower().strip()
    unique_tokens = list(dict.fromkeys(tokens))
    hits: list[SearchHit] = []
    for src in _SOURCES:
        cols = list(src.body_cols)
        conditions = [
            func.lower(func.coalesce(col, "")).like(f"%{tok}%")
            for col in cols
            for tok in unique_tokens
        ]
        stmt = (
            select(src.id_col, src.title_col, *cols)
            .where(src.model.workspace_id == workspace_id)
            .where(or_(*conditions))
        )
        for row in session.execute(stmt):
            artifact_id = row[0]
            title_val = row[1]
            body_vals = row[2:]
            body = " ".join(str(v) for v in body_vals if v not in (None, ""))
            score, snippet = _score_and_snippet(body, unique_tokens, query_low)
            if score <= 0:
                continue
            title = (str(title_val).strip() if title_val else "") or _truncate(body, 80)
            hits.append(
                SearchHit(
                    artifact_type=src.artifact_type,
                    artifact_id=str(artifact_id),
                    title=_truncate(title, 120),
                    snippet=snippet,
                    rank=float(score),
                )
            )
    # Descending relevance, then a stable tiebreak so ordering is fully deterministic.
    hits.sort(key=lambda h: (-h.rank, h.artifact_type, h.artifact_id))
    return hits


def _score_and_snippet(
    body: str, tokens: list[str], query_low: str
) -> tuple[int, str]:
    low = body.lower()
    score = 0
    first_pos = -1
    for tok in tokens:
        whole = re.findall(r"\b" + re.escape(tok) + r"\b", low)
        if whole:
            score += 100 + (len(whole) - 1) * 10
        elif tok in low:
            score += 10  # partial substring match only
        match = re.search(r"\b" + re.escape(tok) + r"\b", low)
        pos = match.start() if match else low.find(tok)
        if pos >= 0 and (first_pos < 0 or pos < first_pos):
            first_pos = pos
    if query_low and len(tokens) > 1 and query_low in low:
        score += 60  # full-phrase bonus
    return score, _snippet(body, first_pos)


def _snippet(body: str, pos: int) -> str:
    if not body:
        return ""
    if pos < 0:
        return _truncate(body, _MAX_SNIPPET)
    start = max(0, pos - _SNIPPET_RADIUS)
    end = min(len(body), start + _MAX_SNIPPET)
    fragment = body[start:end].strip()
    if start > 0:
        fragment = "…" + fragment
    if end < len(body):
        fragment = fragment + "…"
    return fragment


def build_postgres_statement(workspace_id: str, query: str, limit: int) -> Any:
    """Build the PostgreSQL tsvector search statement (one ``UNION ALL`` across artifact tables).

    Kept a pure query-builder so the Postgres path can be asserted well-formed against the
    PostgreSQL dialect without a live server.
    """
    tsquery = func.plainto_tsquery("english", query)
    parts = []
    for src in _SOURCES:
        body = func.concat_ws(" ", *[func.coalesce(col, "") for col in src.body_cols])
        tsv = func.to_tsvector("english", body)
        title = func.coalesce(cast(src.title_col, String), "")
        parts.append(
            select(
                literal(src.artifact_type).label("artifact_type"),
                cast(src.id_col, String).label("artifact_id"),
                cast(title, String).label("title"),
                func.ts_headline("english", body, tsquery).label("snippet"),
                func.ts_rank(tsv, tsquery).label("rank"),
            )
            .where(src.model.workspace_id == workspace_id)
            .where(tsv.op("@@")(tsquery))
        )
    combined = parts[0].union_all(*parts[1:]).subquery("artifact_search")
    return (
        select(
            combined.c.artifact_type,
            combined.c.artifact_id,
            combined.c.title,
            combined.c.snippet,
            combined.c.rank,
        )
        .order_by(combined.c.rank.desc(), combined.c.artifact_type, combined.c.artifact_id)
        .limit(limit)
    )


def _search_postgres(
    session: Session, workspace_id: str, query: str, limit: int
) -> list[SearchHit]:
    stmt = build_postgres_statement(workspace_id, query, limit)
    hits: list[SearchHit] = []
    for row in session.execute(stmt):
        title = (row.title or "").strip() or _truncate(row.snippet or "", 80)
        hits.append(
            SearchHit(
                artifact_type=row.artifact_type,
                artifact_id=str(row.artifact_id),
                title=_truncate(title, 120),
                snippet=row.snippet or "",
                rank=float(row.rank),
            )
        )
    return hits


__all__ = [
    "SearchHit",
    "SearchResult",
    "search_workspace",
    "build_postgres_statement",
    "engine_for_dialect",
    "active_engine",
]
