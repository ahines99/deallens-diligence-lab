"""G03 — Retrieval evaluation harness.

Computes recall@k (k=1,3,5) and mean reciprocal rank (MRR) for the three rankers the app
ships — lexical BM25, the local feature-hashing vector ranker, and their RRF hybrid — over a
committed golden question set (``fixtures/golden_set.json``). The numbers are deterministic:
BM25, the embedding, and RRF are all pure Python, so identical fixtures produce byte-identical
metrics offline and in CI.

The committed baseline lives in ``retrieval_metrics.json``. ``tests/test_retrieval_eval.py``
runs ``run_retrieval_eval`` and fails if any ranker slips below the baseline (minus a small
epsilon) — that failing test is the CI regression gate. Regenerate the baseline intentionally
with ``python -m src.eval.harness`` after a deliberate retrieval change.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from src.models import DocumentChunk, Filing, Workspace
from src.services import embedding_service, retrieval_service

_FIXTURES = Path(__file__).parent / "fixtures" / "golden_set.json"
_BASELINE = Path(__file__).parent / "retrieval_metrics.json"

# k values reported for recall. MRR is computed over the full ranked list.
RECALL_KS = (1, 3, 5)
RANKERS = ("bm25", "vector", "hybrid")


@dataclass(frozen=True)
class GoldenQuestion:
    id: str
    question: str
    relevant: frozenset[str]
    should_answer: bool


def load_golden_set() -> dict:
    """Parsed golden-set fixture: ``{"corpus": [...], "questions": [...]}``."""
    return json.loads(_FIXTURES.read_text(encoding="utf-8"))


def golden_questions(only_answerable: bool = False) -> list[GoldenQuestion]:
    """Golden questions as typed records; ``only_answerable`` keeps the retrieval-scored subset."""
    data = load_golden_set()
    out: list[GoldenQuestion] = []
    for q in data["questions"]:
        gq = GoldenQuestion(
            id=q["id"],
            question=q["question"],
            relevant=frozenset(q.get("relevant", [])),
            should_answer=bool(q.get("should_answer", False)),
        )
        if only_answerable and not gq.relevant:
            continue
        out.append(gq)
    return out


def load_corpus(session: Session) -> tuple[str, dict[int, str]]:
    """Materialize the fixture corpus into a fresh workspace and return ``(workspace_id, index->key)``.

    Each chunk is embedded with the production embedding so the vector and hybrid rankers exercise
    the exact code path the app uses at ingest. The returned map lets the harness translate a
    retrieved ``DocumentChunk`` back to its stable golden key via ``chunk_index``.
    """
    data = load_golden_set()
    workspace = Workspace(name="retrieval-eval", deal_type="public_equity", status="complete")
    session.add(workspace)
    session.flush()

    filing = Filing(
        workspace_id=workspace.id,
        company_name="Golden Fixture Corp",
        ticker="GOLD",
        cik="0000000099",
        form_type="10-K",
        filing_date="2025-02-01",
        accession_number="0000000099-25-000001",
        document_url="https://www.sec.gov/Archives/golden-10k.htm",
        is_synthetic=False,
    )
    session.add(filing)
    session.flush()

    index_to_key: dict[int, str] = {}
    for idx, entry in enumerate(data["corpus"]):
        chunk = DocumentChunk(
            filing_id=filing.id,
            workspace_id=workspace.id,
            section=entry["section"],
            chunk_index=idx,
            chunk_text=entry["text"],
        )
        embedding_service.embed_chunk(chunk)
        session.add(chunk)
        index_to_key[idx] = entry["key"]
    session.flush()
    return workspace.id, index_to_key


def _ranked_keys(
    ranked: list[retrieval_service.RetrievedChunk], index_to_key: dict[int, str]
) -> list[str]:
    return [index_to_key[item.chunk.chunk_index] for item in ranked]


def _rank_for(session: Session, ranker: str, workspace_id: str, query: str, k: int):
    if ranker == "bm25":
        return retrieval_service.retrieve(session, workspace_id, query, k=k)
    if ranker == "vector":
        # Read-only use of the vector candidate ranker that hybrid fuses; behavior unchanged.
        return retrieval_service._vector_candidates(session, workspace_id, query, k=k)
    if ranker == "hybrid":
        return retrieval_service.retrieve_hybrid(session, workspace_id, query, k=k)
    raise ValueError(f"unknown ranker {ranker!r}")


def run_retrieval_eval(session: Session) -> dict:
    """Run every ranker over the golden set and return the aggregated metrics dict.

    Shape: ``{"num_questions": int, "recall_ks": [1,3,5], "rankers": {ranker: {"recall@1": ...,
    "recall@3": ..., "recall@5": ..., "mrr": ...}}}``. Metrics are means over the answerable
    golden questions (those with at least one relevant chunk), rounded to 4 places.
    """
    workspace_id, index_to_key = load_corpus(session)
    questions = golden_questions(only_answerable=True)
    corpus_size = len(index_to_key)

    rankers: dict[str, dict[str, float]] = {}
    for ranker in RANKERS:
        recall_sums = {k: 0.0 for k in RECALL_KS}
        rr_sum = 0.0
        for gq in questions:
            # Retrieve the whole corpus so MRR sees the true rank of the first relevant chunk.
            ranked = _rank_for(session, ranker, workspace_id, gq.question, k=corpus_size)
            keys = _ranked_keys(ranked, index_to_key)
            relevant = gq.relevant
            for k in RECALL_KS:
                hits = len(relevant & set(keys[:k]))
                recall_sums[k] += hits / len(relevant)
            rr = 0.0
            for rank, key in enumerate(keys, start=1):
                if key in relevant:
                    rr = 1.0 / rank
                    break
            rr_sum += rr
        n = len(questions)
        metrics = {f"recall@{k}": round(recall_sums[k] / n, 4) for k in RECALL_KS}
        metrics["mrr"] = round(rr_sum / n, 4)
        rankers[ranker] = metrics

    return {
        "num_questions": len(questions),
        "recall_ks": list(RECALL_KS),
        "rankers": rankers,
    }


def load_baseline() -> dict:
    """The committed baseline metrics used as the CI regression floor."""
    return json.loads(_BASELINE.read_text(encoding="utf-8"))


def write_baseline(metrics: dict) -> None:
    """Overwrite the committed baseline. Intentional-only: run via ``python -m src.eval.harness``."""
    _BASELINE.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")


def _main() -> None:  # pragma: no cover - operator convenience, not part of the test gate
    from src.db.session import SessionLocal, prepare_schema

    prepare_schema()
    with SessionLocal() as session:
        metrics = run_retrieval_eval(session)
        session.rollback()
    write_baseline(metrics)
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":  # pragma: no cover
    _main()
