"""G03 — Retrieval evaluation harness + CI regression gate.

This test IS the gate: CI runs the full pytest suite, so a metric slipping below the committed
baseline (``src/eval/retrieval_metrics.json``) fails this test and therefore fails CI. The eval
is fully offline and deterministic (pure-Python BM25, feature-hashing embedding, RRF), so the
numbers are byte-stable across machines.
"""
from __future__ import annotations

from src.db.session import SessionLocal
from src.eval import harness

# Regression tolerance: metrics are deterministic, so this only absorbs float rounding.
_EPSILON = 1e-6
# Absolute quality floors, independent of the baseline snapshot, that must always hold.
_RECALL5_FLOOR = 0.8
_MRR_FLOOR = 0.8


def _run() -> dict:
    with SessionLocal() as session:
        metrics = harness.run_retrieval_eval(session)
        # Read-only eval: never persist the throwaway fixture workspace.
        session.rollback()
    return metrics


def test_eval_computes_recall_and_mrr_for_every_ranker(client):
    """recall@1/3/5 and MRR are produced for BM25, vector, and hybrid over the golden set."""
    metrics = _run()
    assert metrics["num_questions"] >= 10, "golden set should be non-trivial"
    assert metrics["recall_ks"] == [1, 3, 5]
    for ranker in harness.RANKERS:
        m = metrics["rankers"][ranker]
        assert set(m) == {"recall@1", "recall@3", "recall@5", "mrr"}
        for value in m.values():
            assert 0.0 <= value <= 1.0
        # recall is monotone non-decreasing in k by construction.
        assert m["recall@1"] <= m["recall@3"] <= m["recall@5"]


def test_metrics_meet_absolute_quality_floors(client):
    """Every ranker clears the hard floors regardless of the committed baseline."""
    metrics = _run()
    for ranker in harness.RANKERS:
        m = metrics["rankers"][ranker]
        assert m["recall@5"] >= _RECALL5_FLOOR, f"{ranker} recall@5 below floor"
        assert m["mrr"] >= _MRR_FLOOR, f"{ranker} MRR below floor"


def test_no_regression_below_committed_baseline(client):
    """The CI gate: no ranker/metric may fall below the committed baseline (minus epsilon)."""
    metrics = _run()
    baseline = harness.load_baseline()
    assert metrics["num_questions"] == baseline["num_questions"], (
        "golden set changed — regenerate the baseline with `python -m src.eval.harness`"
    )
    for ranker in harness.RANKERS:
        current = metrics["rankers"][ranker]
        base = baseline["rankers"][ranker]
        for metric_name, base_value in base.items():
            assert current[metric_name] >= base_value - _EPSILON, (
                f"REGRESSION: {ranker} {metric_name} = {current[metric_name]} "
                f"< baseline {base_value}. If intentional, rerun `python -m src.eval.harness`."
            )


def test_hybrid_never_underperforms_bm25(client):
    """RRF's fallback guarantee, measured: hybrid matches or beats BM25 on the golden set.

    On this lexically-separable corpus BM25 already saturates, so the relationship is a tie —
    the point is that fusing the vector signal never *drops* a ranking BM25 got right.
    """
    metrics = _run()
    bm25 = metrics["rankers"]["bm25"]
    hybrid = metrics["rankers"]["hybrid"]
    for metric_name in ("recall@1", "recall@3", "recall@5", "mrr"):
        assert hybrid[metric_name] >= bm25[metric_name] - _EPSILON, (
            f"hybrid regressed vs BM25 on {metric_name}"
        )
