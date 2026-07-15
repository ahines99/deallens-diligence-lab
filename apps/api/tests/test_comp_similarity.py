"""G09 — embedding-similarity comp discovery, side-by-side with the SIC-code method.

Offline tests. The pure ranking function (``rank_comps_by_similarity``) is fed synthetic
``ComparableCompany``-like peers (SimpleNamespace) so no SEC/network access is needed. One endpoint
test exercises the data contract the UI comparison consumes, using an in-process workspace + target
+ peers written directly to the shared SQLite DB.
"""
from __future__ import annotations

from types import SimpleNamespace

from src.services import financial_benchmark_service as bench


def _peer(ticker: str, description: str, sector: str = "", name: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        ticker=ticker,
        business_description=description,
        sector=sector,
        company_name=name or f"{ticker} Inc",
    )


# --- (a) similarity ranking orders peers by cosine to the target -------------
def test_similarity_orders_peers_by_closeness_to_target():
    target = "cloud enterprise software subscription platform for business analytics"
    peers = [
        _peer("NEAR", "cloud enterprise software subscription platform for analytics"),
        _peer("MID", "enterprise software licensing for on-premise data warehousing"),
        _peer("FAR", "offshore oil and gas drilling rigs and pipeline equipment"),
    ]
    result = bench.rank_comps_by_similarity(target, target_sic="", peers=peers)

    assert result["available"] is True
    ranked = [row["ticker"] for row in result["embedding_ranked"]]
    assert ranked == ["NEAR", "MID", "FAR"]
    sims = [row["similarity"] for row in result["embedding_ranked"]]
    assert sims == sorted(sims, reverse=True)
    assert sims[0] > sims[-1]


# --- (b) disagreement detection: embedding_only and sic_only -----------------
def test_disagreements_surface_embedding_only_and_sic_only():
    target_sic = "Prepackaged Software"
    target_desc = "cloud enterprise software subscription analytics platform"
    peers = [
        # High embedding similarity but a DIFFERENT SIC -> embedding_only.
        _peer("EMB", "cloud enterprise software subscription analytics platform",
              sector="Computer Integrated Systems Design"),
        # Same SIC but an unrelated description -> low embedding rank -> sic_only.
        _peer("SIC", "retail grocery store chain operating supermarkets nationwide",
              sector="Prepackaged Software"),
    ]
    result = bench.rank_comps_by_similarity(target_desc, target_sic=target_sic, peers=peers, top_n=1)

    assert result["available"] is True
    assert result["disagreements"]["embedding_only"] == ["EMB"]
    assert result["disagreements"]["sic_only"] == ["SIC"]

    emb_row = next(r for r in result["embedding_ranked"] if r["ticker"] == "EMB")
    assert emb_row["in_sic_set"] is False
    sic_row = next(r for r in result["sic_ranked"] if r["ticker"] == "SIC")
    assert sic_row["in_embedding_top"] is False


def test_peer_in_both_sets_is_not_a_disagreement():
    target_sic = "Prepackaged Software"
    target_desc = "cloud enterprise software subscription analytics platform"
    peers = [
        _peer("BOTH", "cloud enterprise software subscription analytics platform",
              sector="Prepackaged Software"),
    ]
    result = bench.rank_comps_by_similarity(target_desc, target_sic=target_sic, peers=peers, top_n=5)
    assert result["disagreements"] == {"embedding_only": [], "sic_only": []}
    assert result["embedding_ranked"][0]["in_sic_set"] is True
    assert result["sic_ranked"][0]["in_embedding_top"] is True


# --- (c) missing descriptions -> unavailable, never fabricated ---------------
def test_missing_target_description_is_unavailable_not_fabricated():
    peers = [_peer("AAA", "some real description", sector="Prepackaged Software")]
    result = bench.rank_comps_by_similarity("", target_sic="Prepackaged Software", peers=peers)

    assert result["available"] is False
    assert result["embedding_ranked"] == []
    # No similarity is invented; the SIC method still reports its set.
    assert [r["ticker"] for r in result["sic_ranked"]] == ["AAA"]
    assert result["disagreements"]["sic_only"] == ["AAA"]
    assert "unavailable" in result["note"].lower()


def test_no_peer_descriptions_is_unavailable():
    peers = [_peer("AAA", "", sector="X"), _peer("BBB", "   ", sector="Y")]
    result = bench.rank_comps_by_similarity("a real target description", target_sic="X", peers=peers)
    assert result["available"] is False
    assert result["embedding_ranked"] == []


def test_undescribed_peer_is_excluded_not_scored_zero():
    target = "cloud enterprise software analytics platform"
    peers = [
        _peer("HAS", "cloud enterprise software analytics platform"),
        _peer("NONE", ""),  # no description -> excluded, never assigned a fabricated similarity
    ]
    result = bench.rank_comps_by_similarity(target, target_sic="", peers=peers)
    tickers = [r["ticker"] for r in result["embedding_ranked"]]
    assert tickers == ["HAS"]
    assert "NONE" not in tickers


# --- (d) determinism ---------------------------------------------------------
def test_ranking_is_deterministic():
    target = "cloud enterprise software subscription analytics platform"
    peers = [
        _peer("A", "cloud enterprise software subscription analytics"),
        _peer("B", "enterprise data warehousing and business intelligence"),
        _peer("C", "consumer mobile gaming and entertainment apps"),
    ]
    first = bench.rank_comps_by_similarity(target, target_sic="", peers=list(peers))
    second = bench.rank_comps_by_similarity(target, target_sic="", peers=list(peers))
    assert first == second


def test_equal_similarity_ties_break_by_ticker():
    target = "identical description text here"
    peers = [
        _peer("ZZZ", "identical description text here"),
        _peer("AAA", "identical description text here"),
    ]
    result = bench.rank_comps_by_similarity(target, target_sic="", peers=peers)
    # Same cosine -> deterministic tie-break by ticker ascending.
    assert [r["ticker"] for r in result["embedding_ranked"]] == ["AAA", "ZZZ"]


# --- endpoint: UI comparison data contract -----------------------------------
def test_similarity_endpoint_returns_comparison_contract(client):
    from src.db.session import SessionLocal
    from src.models import ComparableCompany, Target

    ws = client.post(
        "/api/workspaces", json={"name": "Comp similarity WS", "deal_type": "buyout"}
    ).json()
    ws_id = ws["id"]

    with SessionLocal() as session:
        session.add(
            Target(
                workspace_id=ws_id,
                name="Target Co",
                sector="Prepackaged Software",
                description="cloud enterprise software subscription analytics platform",
            )
        )
        session.add(
            ComparableCompany(
                workspace_id=ws_id,
                ticker="EMB",
                company_name="Embedding Peer",
                sector="Computer Integrated Systems Design",
                business_description="cloud enterprise software subscription analytics platform",
            )
        )
        session.add(
            ComparableCompany(
                workspace_id=ws_id,
                ticker="SIC",
                company_name="SIC Peer",
                sector="Prepackaged Software",
                business_description="retail grocery supermarket chain operations",
            )
        )
        session.commit()

    resp = client.get(f"/api/workspaces/{ws_id}/comps/similarity?top_n=1")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["available"] is True
    assert body["target_description"].startswith("cloud enterprise")
    assert {"embedding_ranked", "sic_ranked", "disagreements", "note"} <= body.keys()
    assert body["disagreements"]["embedding_only"] == ["EMB"]
    assert body["disagreements"]["sic_only"] == ["SIC"]
    emb_row = next(r for r in body["embedding_ranked"] if r["ticker"] == "EMB")
    assert emb_row["in_sic_set"] is False and 0.0 <= emb_row["similarity"] <= 1.0
