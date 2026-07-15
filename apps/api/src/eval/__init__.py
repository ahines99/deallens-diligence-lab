"""Offline retrieval and generation evaluation harnesses.

* ``harness`` — G03 retrieval eval: recall@k and MRR for BM25 / vector / hybrid over a committed
  golden question set, with a committed metrics baseline (``retrieval_metrics.json``) that a
  pytest gate defends against regression.
* ``calibration`` — G06 abstention calibration: coverage-score distributions for answered vs
  abstained/partial questions and the justified thresholds (see ``calibration_study.md``).

Both are fully deterministic and need no network: they reuse the production retrieval and Q&A
services over the fixture corpus in ``fixtures/golden_set.json``.
"""
