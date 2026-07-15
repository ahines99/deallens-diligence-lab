"""Comparable companies (real peers by ticker) and the deterministic financial benchmark.

Peers are ingested from SEC XBRL. Market multiples are intentionally omitted (no free source) —
we benchmark real fundamentals only, per the project's no-fabricated-valuation rule.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.agents.financial_analyst import FinancialAnalyst
from src.db.base import now_utc
from src.models import ComparableCompany, Target
from src.schemas.comp import CompCreate
from src.services import edgar_client, embedding_service, sec_financials
from src.services.common import NotFound
from src.services.edgar_client import EdgarError
from src.services.workspace_service import get_target

logger = logging.getLogger("deallens.comps")
_fin = FinancialAnalyst()


def add_comps_by_ticker(session: Session, workspace_id: str, tickers: list[str]) -> list[ComparableCompany]:
    have = {c.ticker for c in list_comps(session, workspace_id)}
    target = get_target(session, workspace_id)
    for raw in tickers:
        tk = (raw or "").strip().upper()
        if not tk or tk in have:
            continue
        if target and target.ticker and tk == target.ticker.upper():
            continue  # don't add the target as its own peer
        try:
            info = edgar_client.resolve_ticker(tk)
            facts = edgar_client.get_company_facts(info["cik"])
            fin = sec_financials.extract_financials(facts)
            sub = edgar_client.get_submissions(info["cik"])
        except EdgarError as exc:
            logger.warning("Skipping comp %s: %s", tk, exc)
            continue
        session.add(
            ComparableCompany(
                workspace_id=workspace_id,
                ticker=info["ticker"],
                company_name=info["name"],
                sector=sub.get("sicDescription", ""),
                business_description=sub.get("sicDescription", ""),
                revenue=fin.get("revenue"),
                gross_margin=fin.get("gross_margin"),
                operating_margin=fin.get("operating_margin"),
                net_margin=fin.get("net_margin"),
                revenue_growth=fin.get("revenue_growth"),
                rnd_pct=fin.get("rnd_pct"),
                market_cap=None,
                enterprise_value=None,
                ev_revenue_multiple=None,
                notes=f"FY ending {fin.get('fiscal_year_end') or 'n/a'} (SEC XBRL).",
                data_source="SEC EDGAR (XBRL)",
                is_illustrative=False,
            )
        )
        have.add(info["ticker"])
    session.flush()
    return list_comps(session, workspace_id)


def add_comps(session: Session, workspace_id: str, comps: list[CompCreate]) -> list[ComparableCompany]:
    for c in comps:
        session.add(
            ComparableCompany(
                workspace_id=workspace_id,
                **c.model_dump(),
                data_source="User-submitted comparable profile (unverified)",
                is_illustrative=True,
            )
        )
    session.flush()
    return list_comps(session, workspace_id)


def list_comps(session: Session, workspace_id: str) -> list[ComparableCompany]:
    return list(
        session.scalars(
            select(ComparableCompany)
            .where(ComparableCompany.workspace_id == workspace_id)
            .order_by(ComparableCompany.revenue.desc().nullslast())
        )
    )


def get_trends(session: Session, workspace_id: str) -> dict:
    target: Target | None = get_target(session, workspace_id)
    if target is None or not target.financials:
        raise NotFound("No financials available; ingest a company with a ticker first.")
    trends = (target.financials or {}).get("trends")
    if not trends or not trends.get("rows"):
        raise NotFound("No multi-year trend data available for this company.")
    return {
        "workspace_id": workspace_id,
        "target_name": target.name,
        "years": trends.get("years", []),
        "rows": trends.get("rows", []),
        "revenue_cagr": trends.get("revenue_cagr"),
        "generated_at": now_utc(),
    }


def _assess(target_value: float | None, peer_median: float | None) -> str:
    if target_value is None or peer_median is None:
        return "n/a"
    if peer_median == 0:
        return "in_line"
    rel = (target_value - peer_median) / abs(peer_median)
    if rel > 0.1:
        return "above"
    if rel < -0.1:
        return "below"
    return "in_line"


def _stats(values: list[float | None]) -> tuple[float | None, float | None, float | None]:
    clean = [v for v in values if v is not None]
    if not clean:
        return None, None, None
    return _fin.median(clean), min(clean), max(clean)


# --- Embedding-similarity comp discovery (G09) -------------------------------
# A second, independent lens on peer selection: rank candidate peers by cosine similarity of their
# business-description embeddings to the target's description, then set that ranking SIDE-BY-SIDE with
# the SIC-code method (same-SIC-description peers) and surface where the two disagree. The embedding
# is deterministic and keyless (see embedding_service), so the ranking is reproducible offline.


def _norm_sic(text: str) -> str:
    """Normalize a SIC/sector description for equality comparison (whitespace + case folded)."""
    return " ".join((text or "").lower().split())


def rank_comps_by_similarity(
    target_description: str,
    target_sic: str,
    peers: list,
    top_n: int = 5,
) -> dict:
    """Pure, deterministic ranking of ``peers`` by description-embedding similarity to the target.

    ``peers`` are duck-typed on ``ticker`` / ``business_description`` / ``sector`` / ``company_name``
    (real ``ComparableCompany`` rows or synthetic stand-ins). The SIC-code method's peer set is the
    subset whose ``sector`` (SIC description) matches ``target_sic``. Returns the data contract:
    ``{target_description, available, embedding_ranked, sic_ranked, disagreements, note}``.

    Honesty invariant: if the target has no usable description, or no peer has one, ``available`` is
    False and no similarity is fabricated — the SIC method is still reported since it needs no text.
    """
    peers = list(peers)
    tgt_sic = _norm_sic(target_sic)

    def _in_sic(peer) -> bool:
        return bool(tgt_sic) and _norm_sic(getattr(peer, "sector", "")) == tgt_sic

    def _sic_rows(top_tickers: set[str]) -> list[dict]:
        # Deterministic order for the SIC method (which carries no similarity score): by ticker.
        sic_peers = sorted((p for p in peers if _in_sic(p)), key=lambda p: p.ticker)
        return [
            {
                "ticker": p.ticker,
                "company_name": getattr(p, "company_name", "") or "",
                "in_embedding_top": p.ticker in top_tickers,
            }
            for p in sic_peers
        ]

    target_vec = embedding_service.embed(target_description or "")
    target_has_desc = any(value != 0.0 for value in target_vec)

    # Peers that actually carry a description signal (never assign a fabricated similarity to those
    # that don't — they are simply excluded from the embedding ranking).
    scored: list[tuple[object, float]] = []
    undescribed = 0
    for peer in peers:
        peer_vec = embedding_service.embed(getattr(peer, "business_description", "") or "")
        if target_has_desc and any(value != 0.0 for value in peer_vec):
            scored.append((peer, embedding_service.cosine(target_vec, peer_vec)))
        else:
            undescribed += 1

    sic_count = sum(1 for p in peers if _in_sic(p))

    if not target_has_desc or not scored:
        reason = (
            "the target has no business description"
            if not target_has_desc
            else "no candidate peer has a business description"
        )
        return {
            "target_description": target_description or "",
            "available": False,
            "embedding_ranked": [],
            "sic_ranked": _sic_rows(set()),
            "disagreements": {
                "embedding_only": [],
                "sic_only": [row["ticker"] for row in _sic_rows(set())],
            },
            "note": (
                f"Embedding-similarity comp discovery is unavailable: {reason}. Similarity is never "
                "fabricated; only the SIC-code peer set is shown."
            ),
        }

    # Deterministic ordering: similarity desc, ticker asc as a stable tie-break.
    scored.sort(key=lambda item: (-item[1], item[0].ticker))
    top_tickers = {peer.ticker for peer, _ in scored[:top_n]}

    embedding_ranked = [
        {
            "ticker": peer.ticker,
            "company_name": getattr(peer, "company_name", "") or "",
            "similarity": round(sim, 6),
            "in_sic_set": _in_sic(peer),
        }
        for peer, sim in scored
    ]
    embedding_only = [peer.ticker for peer, _ in scored[:top_n] if not _in_sic(peer)]
    sic_only = [row["ticker"] for row in _sic_rows(top_tickers) if not row["in_embedding_top"]]

    note = (
        f"Ranked {len(scored)} peer(s) by embedding similarity of business descriptions"
        + (f" ({undescribed} peer(s) lacked descriptions and were excluded)" if undescribed else "")
        + f"; the SIC-code method matched {sic_count} peer(s). The two methods disagree on "
        f"{len(embedding_only) + len(sic_only)} peer(s): "
        f"{len(embedding_only)} embedding-only, {len(sic_only)} SIC-only."
    )
    return {
        "target_description": target_description or "",
        "available": True,
        "embedding_ranked": embedding_ranked,
        "sic_ranked": _sic_rows(top_tickers),
        "disagreements": {"embedding_only": embedding_only, "sic_only": sic_only},
        "note": note,
    }


def similarity_comps(session: Session, workspace_id: str, top_n: int = 5) -> dict:
    """DB-facing wrapper: discover comps by description similarity for a workspace's target + peers."""
    target: Target | None = get_target(session, workspace_id)
    if target is None:
        raise NotFound("No target set for this workspace; cannot discover comps by similarity.")
    comps = list_comps(session, workspace_id)
    result = rank_comps_by_similarity(
        target_description=target.description or "",
        target_sic=target.sector or "",
        peers=comps,
        top_n=top_n,
    )
    return {
        "workspace_id": workspace_id,
        "target_name": target.name,
        "generated_at": now_utc(),
        **result,
    }


def compute_benchmark(session: Session, workspace_id: str) -> dict:
    target: Target | None = get_target(session, workspace_id)
    if target is None:
        raise NotFound("No target set for this workspace; cannot compute a benchmark.")
    comps = list_comps(session, workspace_id)
    if not comps:
        raise NotFound("No comparable companies added; add peer tickers before benchmarking.")

    def col(attr: str) -> list[float | None]:
        return [getattr(c, attr) for c in comps]

    specs = [
        ("revenue", "Revenue", "usd", target.revenue, col("revenue"), "Scale vs. the real peer set."),
        ("revenue_growth", "Revenue growth", "pct", target.revenue_growth, col("revenue_growth"),
         "Growth relative to peers."),
        ("gross_margin", "Gross margin", "pct", target.gross_margin, col("gross_margin"),
         "Gross profitability vs. peers."),
        ("operating_margin", "Operating margin", "pct", target.operating_margin, col("operating_margin"),
         "GAAP operating profitability vs. peers."),
        ("net_margin", "Net margin", "pct", target.net_margin, col("net_margin"),
         "Bottom-line profitability vs. peers."),
        ("rnd_pct", "R&D % of revenue", "pct", target.rnd_pct, col("rnd_pct"),
         "R&D intensity vs. peers."),
    ]
    metrics = []
    for key, label, unit, tval, peers, note in specs:
        med, lo, hi = _stats(peers)
        metrics.append({
            "key": key, "label": label, "unit": unit,
            "target_value": tval, "peer_median": med, "peer_min": lo, "peer_max": hi,
            "assessment": _assess(tval, med), "commentary": note,
        })

    # Rule of 40 (growth + operating margin), computed per peer.
    peer_r40 = [
        c.revenue_growth + c.operating_margin
        for c in comps
        if c.revenue_growth is not None and c.operating_margin is not None
    ]
    med, lo, hi = _stats(peer_r40)
    metrics.append({
        "key": "rule_of_40", "label": "Rule of 40", "unit": "pct",
        "target_value": target.rule_of_40, "peer_median": med, "peer_min": lo, "peer_max": hi,
        "assessment": _assess(target.rule_of_40, med),
        "commentary": "Growth + operating margin; a balanced growth/profitability read.",
    })

    verified_count = sum(comp.data_source == "SEC EDGAR (XBRL)" for comp in comps)
    user_count = len(comps) - verified_count
    summary = (
        f"{target.name} benchmarked against {len(comps)} peer(s): {verified_count} SEC-verified "
        f"and {user_count} user-submitted/unverified. Valuation multiples are intentionally omitted "
        "unless explicitly supplied by the analyst."
    )
    notes = [
        (
            f"{verified_count} peer(s) use SEC XBRL company facts; {user_count} user-submitted "
            "peer(s) remain unverified and illustrative."
        ),
        "Fiscal-year periods may differ across peers; comparisons are directional.",
        "Market multiples require analyst or licensed-source inputs and are never inferred.",
    ]
    return {
        "workspace_id": workspace_id,
        "target_name": target.name,
        "peer_count": len(comps),
        "summary": summary,
        "metrics": metrics,
        "notes": notes,
        "generated_at": now_utc(),
    }
