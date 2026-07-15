"""G06 — Abstention / partial-answer calibration.

The filings Q&A (``filings_qa_service.ask``) makes two confidence decisions from one signal —
*coverage*, the fraction of the question's content terms that the cited answer actually covers:

* **abstain** when no filing sentence shares any term with the question (coverage is undefined /
  zero, there is nothing to cite);
* **partial** vs **answered** by comparing coverage to ``_PARTIAL_COVERAGE_THRESHOLD`` (0.5): a
  thin single-term hit is flagged ``partial`` rather than dressed up as a confident answer.

This module reproduces the study behind those thresholds: it runs the real ``ask`` over the
labeled golden set (``fixtures/golden_set.json``) and reports the coverage distributions for the
``should_answer`` (answered) class vs the not-answerable (abstain/partial) class, plus the
separation margin that justifies the chosen boundary. It is deterministic and offline.

``calibration_study.md`` is the committed write-up; ``tests/test_calibration.py`` pins the
boundary behavior and guards ``filings_qa_service`` against silent threshold drift.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from src.eval import harness
from src.services import filings_qa_service

# The calibrated thresholds. These MUST equal the constants the service ships; the drift-guard
# test fails if they ever diverge. They are re-derived (not just asserted) in ``run_calibration``.
PARTIAL_COVERAGE_THRESHOLD = 0.5
# Coverage at or below this abstains: there is no lexical evidence to cite at all.
ABSTAIN_COVERAGE = 0.0


@dataclass(frozen=True)
class CalibrationRow:
    id: str
    question: str
    should_answer: bool
    status: str
    coverage: float


def _ask(session: Session, workspace_id: str, question: str) -> dict:
    # Pure BM25 so the study is reproducible independent of whether embeddings are present;
    # coverage is a property of matched question terms, not of the ranker that surfaced them.
    return filings_qa_service.ask(session, workspace_id, question, use_hybrid=False)


def run_calibration(session: Session) -> dict:
    """Score every labeled golden question and summarize the coverage separation.

    Returns ``{"rows": [...], "answered": {...}, "not_answerable": {...}, "separation": float,
    "chosen_partial_threshold": float, "derived_partial_threshold": float}`` where the two class
    blocks carry the min/max/mean coverage and the count by resulting status.
    """
    workspace_id, _ = harness.load_corpus(session)
    rows: list[CalibrationRow] = []
    for gq in harness.golden_questions():
        result = _ask(session, workspace_id, gq.question)
        rows.append(
            CalibrationRow(
                id=gq.id,
                question=gq.question,
                should_answer=gq.should_answer,
                status=result["status"],
                coverage=result["retrieval"]["coverage"],
            )
        )

    answered = [r for r in rows if r.should_answer]
    not_answerable = [r for r in rows if not r.should_answer]
    answered_cov = [r.coverage for r in answered]
    # Coverage seen by the not-answerable class *when it isn't a clean abstain* — the partial hits
    # that a good threshold must keep on the low side of the boundary.
    partial_cov = [r.coverage for r in not_answerable if r.coverage > ABSTAIN_COVERAGE]

    min_answered = min(answered_cov) if answered_cov else 0.0
    max_partial = max(partial_cov) if partial_cov else 0.0
    # A separating threshold sits strictly between the highest not-answerable coverage and the
    # lowest answered coverage. The midpoint is the maximum-margin choice.
    derived = round((max_partial + min_answered) / 2, 4)
    separation = round(min_answered - max_partial, 4)

    return {
        "rows": [r.__dict__ for r in rows],
        "answered": _summary(answered),
        "not_answerable": _summary(not_answerable),
        "separation": separation,
        "min_answered_coverage": round(min_answered, 4),
        "max_partial_coverage": round(max_partial, 4),
        "chosen_partial_threshold": PARTIAL_COVERAGE_THRESHOLD,
        "derived_partial_threshold": derived,
    }


def _summary(rows: list[CalibrationRow]) -> dict:
    covs = [r.coverage for r in rows]
    status_counts: dict[str, int] = {}
    for r in rows:
        status_counts[r.status] = status_counts.get(r.status, 0) + 1
    return {
        "count": len(rows),
        "min_coverage": round(min(covs), 4) if covs else None,
        "max_coverage": round(max(covs), 4) if covs else None,
        "mean_coverage": round(sum(covs) / len(covs), 4) if covs else None,
        "status_counts": status_counts,
    }


def _main() -> None:  # pragma: no cover - operator convenience
    import json

    from src.db.session import SessionLocal, prepare_schema

    prepare_schema()
    with SessionLocal() as session:
        study = run_calibration(session)
        session.rollback()
    print(json.dumps(study, indent=2, default=str))


if __name__ == "__main__":  # pragma: no cover
    _main()
