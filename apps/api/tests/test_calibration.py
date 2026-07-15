"""G06 — Abstention / partial-answer calibration: boundary + drift-guard tests.

Offline and deterministic. These pin the calibrated thresholds documented in
``src/eval/calibration_study.md`` to the behavior the service actually ships, so neither can move
without the other failing.
"""
from __future__ import annotations

import pytest

from src.db.session import SessionLocal
from src.eval import calibration
from src.services import filings_qa_service


def _ask_on_corpus(question: str) -> dict:
    """Load the fixture corpus into a throwaway workspace and ask one question (BM25, offline)."""
    with SessionLocal() as session:
        workspace_id, _ = calibration.harness.load_corpus(session)
        result = filings_qa_service.ask(session, workspace_id, question, use_hybrid=False)
        session.rollback()
    return result


# --------------------------------------------------------------------------- drift guard
def test_service_threshold_matches_calibrated_value():
    """The shipped threshold must equal the calibrated value — guards against silent drift."""
    assert (
        filings_qa_service._PARTIAL_COVERAGE_THRESHOLD
        == calibration.PARTIAL_COVERAGE_THRESHOLD
        == 0.5
    )


# --------------------------------------------------------------------------- boundary flips
def test_coverage_at_threshold_is_answered(client):
    """One of two terms matched → coverage 0.5 → answered (the boundary is inclusive)."""
    body = _ask_on_corpus("concentration antarctica")
    assert body["retrieval"]["coverage"] == pytest.approx(0.5)
    assert body["status"] == "answered"
    assert body["citations"], "an answered result still resolves citations"


def test_coverage_just_below_threshold_flips_to_partial(client):
    """Adding one unmatched term (1 of 3 → 0.333) is the minimal change that flips to partial."""
    body = _ask_on_corpus("concentration antarctica zeppelin")
    assert body["retrieval"]["coverage"] < calibration.PARTIAL_COVERAGE_THRESHOLD
    assert body["retrieval"]["coverage"] == pytest.approx(1 / 3, abs=1e-3)
    assert body["status"] == "partial"
    assert body["citations"], "a partial result still resolves citations"


def test_no_overlap_question_abstains(client):
    """No question term shares evidence with any chunk → abstain, no citations fabricated."""
    body = _ask_on_corpus("antarctica zeppelin lithium")
    assert body["status"] == "abstained"
    assert body["citations"] == []
    assert body["retrieval"]["coverage"] == 0.0


# --------------------------------------------------------------------------- study invariants
def test_calibration_classes_are_separable_at_the_threshold(client):
    """The committed study's core claim: the two labeled classes don't overlap around 0.5."""
    with SessionLocal() as session:
        study = calibration.run_calibration(session)
        session.rollback()

    # Every answerable question lands at/above the threshold; every non-answerable below it.
    for row in study["rows"]:
        if row["should_answer"]:
            assert row["status"] == "answered"
            assert row["coverage"] >= calibration.PARTIAL_COVERAGE_THRESHOLD
        else:
            assert row["status"] in {"abstained", "partial"}
            assert row["coverage"] < calibration.PARTIAL_COVERAGE_THRESHOLD

    # Non-overlapping classes: lowest answered coverage strictly exceeds highest not-answerable.
    assert study["separation"] > 0
    assert study["max_partial_coverage"] < calibration.PARTIAL_COVERAGE_THRESHOLD
    assert study["min_answered_coverage"] >= calibration.PARTIAL_COVERAGE_THRESHOLD
    # The chosen threshold sits inside the empirical safe band (derived midpoint, min answered].
    assert (
        study["derived_partial_threshold"]
        <= calibration.PARTIAL_COVERAGE_THRESHOLD
        <= study["min_answered_coverage"]
    )
