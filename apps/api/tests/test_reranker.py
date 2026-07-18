"""G82 — optional cross-encoder reranker over the hybrid top-k.

Default OFF and eval-gated. What these tests pin:

* with the default ``RERANKER_BACKEND=off`` the hybrid path is byte-identical to a no-rerank
  call, and provenance says so ("off");
* a configured-but-unusable reranker degrades EXPLICITLY (provenance carries the note) —
  never a crash, never a silent skip;
* an available reranker reorders the hybrid top-k by cross-encoder score with a stable
  chunk-id tie-break, and provenance records the model's method tag;
* the eval harness grows a fourth ranker ("hybrid+rerank") ONLY when a reranker is truly
  available — the committed CI baseline honestly omits it — and ``eval_gate`` promotes only a
  strict win over hybrid on BOTH MRR and recall@5.

The neural backend is faked at the module seam (``onnx_reranker``), exactly like the G55
embedding tests: CI must not download models or install the optional extra.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

from src.config import settings
from src.db.session import SessionLocal
from src.eval import harness
from src.models import DocumentChunk, Filing
from src.services import embedding_service, onnx_reranker, retrieval_service

_FAKE_METHOD = "onnx-rerank-fake12345678"

_QUERY = "customer concentration revenue risk"
_TEXTS = [
    "Customer concentration remains a material risk to consolidated revenue.",
    "Revenue increased twelve percent driven by subscription growth.",
    "Our supply chain depends on a limited number of component vendors.",
]


def _activate_fake_reranker(monkeypatch, score_fn=None) -> None:
    """Simulate a configured, loadable local cross-encoder without any real dependency."""
    monkeypatch.setattr(settings, "reranker_backend", "onnx_local")
    monkeypatch.setattr(onnx_reranker, "available", lambda: (True, None))
    monkeypatch.setattr(onnx_reranker, "method", lambda: _FAKE_METHOD)
    monkeypatch.setattr(
        onnx_reranker,
        "score",
        score_fn or (lambda query, texts: [float(i) for i in range(len(texts))]),
    )


def _seed_workspace(client, name: str) -> str:
    ws_id = client.post(
        "/api/workspaces", json={"name": name, "deal_type": "public_equity"}
    ).json()["id"]
    with SessionLocal() as session:
        filing = Filing(
            workspace_id=ws_id,
            company_name="Rerank Corp",
            ticker="RRK",
            cik="0000000082",
            form_type="10-K",
            filing_date="2025-02-01",
            accession_number="0000000082-25-000001",
            document_url="https://www.sec.gov/Archives/rerank-10k.htm",
            is_synthetic=False,
        )
        session.add(filing)
        session.flush()
        for idx, text in enumerate(_TEXTS):
            chunk = DocumentChunk(
                filing_id=filing.id, workspace_id=ws_id, section=f"Item {idx}",
                chunk_index=idx, chunk_text=text,
            )
            embedding_service.embed_chunk(chunk)
            session.add(chunk)
        session.commit()
    return ws_id


# ------------------------------------------------------------------- default off = unchanged
def test_default_off_is_byte_identical_with_off_provenance(client):
    ws_id = _seed_workspace(client, "Rerank default off")
    with SessionLocal() as session:
        plain = retrieval_service.retrieve_hybrid(session, ws_id, _QUERY, k=3)
        with_prov, provenance = retrieval_service.retrieve_hybrid_with_provenance(
            session, ws_id, _QUERY, k=3
        )
    assert provenance == {"applied": False, "reason": "off", "method": None}
    assert [r.chunk.id for r in with_prov] == [r.chunk.id for r in plain]
    assert [r.score for r in with_prov] == [r.score for r in plain]
    assert plain, "fixture should produce hybrid results"


def test_retrieve_hybrid_signature_unchanged():
    sig = inspect.signature(retrieval_service.retrieve_hybrid)
    assert list(sig.parameters) == ["session", "workspace_id", "query", "k"]
    assert sig.parameters["k"].default == 5


def test_maybe_rerank_off_returns_the_same_objects():
    items = [
        retrieval_service.RetrievedChunk(
            chunk=SimpleNamespace(id=f"c{i}", section="S", chunk_text="t"), score=1.0 - i / 10
        )
        for i in range(3)
    ]
    ranked, provenance = retrieval_service.maybe_rerank("q", items, k=3)
    assert provenance == {"applied": False, "reason": "off", "method": None}
    assert [r is item for r, item in zip(ranked, items)] == [True, True, True]


# ------------------------------------------------------------------- applied reordering
def test_fake_reranker_reorders_hybrid_topk_with_provenance(client, monkeypatch):
    ws_id = _seed_workspace(client, "Rerank reorder")
    with SessionLocal() as session:
        baseline = retrieval_service.retrieve_hybrid(session, ws_id, _QUERY, k=3)
        assert len(baseline) == 3
        # Positional scores 0.0, 1.0, 2.0: the fake ranks the fused list back-to-front, so an
        # applied rerank must exactly reverse the RRF ordering.
        _activate_fake_reranker(monkeypatch)
        ranked, provenance = retrieval_service.retrieve_hybrid_with_provenance(
            session, ws_id, _QUERY, k=3
        )
    assert provenance == {"applied": True, "reason": "applied", "method": _FAKE_METHOD}
    assert [r.chunk.id for r in ranked] == [r.chunk.id for r in reversed(baseline)]
    # Scores are the cross-encoder scores now, not RRF magnitudes.
    assert [r.score for r in ranked] == [2.0, 1.0, 0.0]


def test_rerank_ties_break_by_chunk_id_and_k_is_respected(monkeypatch):
    _activate_fake_reranker(monkeypatch, score_fn=lambda query, texts: [7.0] * len(texts))
    items = [
        retrieval_service.RetrievedChunk(
            chunk=SimpleNamespace(id=chunk_id, section="S", chunk_text="text"), score=0.5
        )
        for chunk_id in ("aa", "cc", "bb")
    ]
    ranked, provenance = retrieval_service.maybe_rerank("q", items, k=2)
    assert provenance["applied"] is True
    # All scores tie -> stable ordering by chunk id, descending (the module-wide convention).
    assert [r.chunk.id for r in ranked] == ["cc", "bb"]


# ------------------------------------------------------------------- explicit degradation
def test_unavailable_reranker_degrades_with_note(client, monkeypatch):
    ws_id = _seed_workspace(client, "Rerank unavailable")
    with SessionLocal() as session:
        baseline = retrieval_service.retrieve_hybrid(session, ws_id, _QUERY, k=3)
        monkeypatch.setattr(settings, "reranker_backend", "onnx_local")
        monkeypatch.setattr(
            onnx_reranker, "available", lambda: (False, "reranker extra not installed")
        )
        ranked, provenance = retrieval_service.retrieve_hybrid_with_provenance(
            session, ws_id, _QUERY, k=3
        )
    assert provenance == {
        "applied": False,
        "reason": "unavailable: reranker extra not installed",
        "method": None,
    }
    assert [r.chunk.id for r in ranked] == [r.chunk.id for r in baseline]
    assert [r.score for r in ranked] == [r.score for r in baseline]


def test_real_backend_reports_missing_model_path(monkeypatch):
    """No fakes here: the actual onnx_reranker note surfaces through provenance."""
    monkeypatch.setattr(settings, "reranker_backend", "onnx_local")
    monkeypatch.setattr(settings, "reranker_model_path", "")
    ranked, provenance = retrieval_service.maybe_rerank("q", [], k=5)
    assert ranked == []
    assert provenance == {
        "applied": False,
        "reason": "unavailable: RERANKER_MODEL_PATH is not set",
        "method": None,
    }


# ------------------------------------------------------------------- eval harness integration
def test_eval_adds_fourth_ranker_only_when_reranker_available(client, monkeypatch):
    _activate_fake_reranker(monkeypatch)
    with SessionLocal() as session:
        metrics = harness.run_retrieval_eval(session)
        session.rollback()
    assert set(metrics["rankers"]) == set(harness.RANKERS) | {harness.RERANK_RANKER}
    reranked = metrics["rankers"][harness.RERANK_RANKER]
    assert set(reranked) == {"recall@1", "recall@3", "recall@5", "mrr"}
    for value in reranked.values():
        assert 0.0 <= value <= 1.0


def test_eval_omits_rerank_ranker_by_default(client):
    """CI honesty: without a reranker the committed metrics shape has exactly three rankers."""
    with SessionLocal() as session:
        metrics = harness.run_retrieval_eval(session)
        session.rollback()
    assert set(metrics["rankers"]) == set(harness.RANKERS)
    assert harness._reranker_active() is False


def test_eval_hybrid_ranker_stays_pure_rrf_when_reranker_active(client, monkeypatch):
    """The gate compares rerank against TRUE RRF: 'hybrid' must never itself be reranked."""
    ws_id = _seed_workspace(client, "Rerank purity")
    _activate_fake_reranker(monkeypatch)  # positional scores would reverse any list they touch
    with SessionLocal() as session:
        hybrid_via_harness = harness._rank_for(session, "hybrid", ws_id, _QUERY, k=3)
        pure_fusion = retrieval_service._hybrid_fused(session, ws_id, _QUERY, k=3)
        reranked = harness._rank_for(session, harness.RERANK_RANKER, ws_id, _QUERY, k=3)
    assert [r.chunk.id for r in hybrid_via_harness] == [r.chunk.id for r in pure_fusion]
    assert [r.chunk.id for r in reranked] == [r.chunk.id for r in reversed(pure_fusion)]


# ------------------------------------------------------------------- promotion gate
def _metrics(hybrid: dict, reranked: dict | None) -> dict:
    rankers = {
        "bm25": {"recall@1": 0.9, "recall@3": 0.95, "recall@5": 0.95, "mrr": 0.92},
        "vector": {"recall@1": 0.85, "recall@3": 0.9, "recall@5": 0.95, "mrr": 0.9},
        "hybrid": hybrid,
    }
    if reranked is not None:
        rankers[harness.RERANK_RANKER] = reranked
    return {"num_questions": 13, "recall_ks": [1, 3, 5], "rankers": rankers}


_HYBRID = {"recall@1": 0.9, "recall@3": 0.95, "recall@5": 0.95, "mrr": 0.92}


def test_eval_gate_promotes_only_a_strict_double_win():
    verdict = harness.eval_gate(
        _metrics(_HYBRID, {"recall@1": 0.95, "recall@3": 1.0, "recall@5": 0.96, "mrr": 0.93})
    )
    assert verdict["promote"] is True
    assert "beats hybrid" in verdict["reason"]


def test_eval_gate_rejects_ties_on_either_metric():
    # MRR tie (boundary): equal is NOT better.
    tie_mrr = harness.eval_gate(
        _metrics(_HYBRID, {"recall@1": 0.95, "recall@3": 1.0, "recall@5": 0.96, "mrr": 0.92})
    )
    assert tie_mrr["promote"] is False
    assert "mrr" in tie_mrr["reason"]
    # recall@5 tie with a better MRR: still rejected — both must strictly improve.
    tie_recall = harness.eval_gate(
        _metrics(_HYBRID, {"recall@1": 0.95, "recall@3": 1.0, "recall@5": 0.95, "mrr": 0.99})
    )
    assert tie_recall["promote"] is False
    assert "recall@5" in tie_recall["reason"]


def test_eval_gate_rejects_a_regression_even_with_an_mrr_win():
    verdict = harness.eval_gate(
        _metrics(_HYBRID, {"recall@1": 0.95, "recall@3": 1.0, "recall@5": 0.9, "mrr": 0.99})
    )
    assert verdict["promote"] is False
    assert "recall@5" in verdict["reason"]


def test_eval_gate_never_promotes_when_rerank_metrics_are_absent():
    verdict = harness.eval_gate(_metrics(_HYBRID, None))
    assert verdict["promote"] is False
    assert "not available" in verdict["reason"]
