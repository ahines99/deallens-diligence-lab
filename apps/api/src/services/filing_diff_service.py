"""Cross-year 10-K risk-factor drift (G07).

Given a workspace holding two or more 10-K filings, this service diffs the *Item 1A — Risk
Factors* disclosure of the two most recent 10-Ks and classifies each risk item as **added**
(present only in the newer year), **removed** (present only in the older year), or **changed**
(the same risk, materially reworded). Every result carries a citation into the filing(s) it came
from — section, filing date, and document URL — so a reviewer can open both disclosures.

Alignment is by semantic similarity, not string equality: each risk item is embedded with the
deterministic local hashing embedding (see ``embedding_service``) and items are matched one-to-one
across the two years by greedy best-cosine assignment. The thresholds below split the outcome:

* cosine ``>= _SAME_THRESHOLD``  → the item is essentially identical → **unchanged** (omitted).
* ``_MATCH_THRESHOLD <= cosine < _SAME_THRESHOLD`` → same risk, materially changed → **changed**.
* best cosine ``< _MATCH_THRESHOLD`` → no counterpart → **added** (newer) or **removed** (older).

The service never fabricates: if the workspace has fewer than two 10-Ks, or the risk-factor
section is absent from a filing, it returns ``source_status = "unavailable"`` with a plain reason.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models import DocumentChunk, Filing
from src.services import embedding_service
from src.services.common import get_workspace_or_404

METHOD = "risk-factor-embedding-diff-v1"

# Calibrated against the local hashing embedding: identical text scores 1.0, a same-topic risk
# reworded with new figures scores ~0.94, and unrelated risk topics stay below ~0.35. A 0.50 floor
# leaves comfortable margin above cross-topic noise, and a 0.98 ceiling treats only near-identical
# text as unchanged so any material rewrite surfaces as "changed".
_MATCH_THRESHOLD = 0.50
_SAME_THRESHOLD = 0.98
# Risk items shorter than this are section headers / fragments, not disclosures worth diffing.
_MIN_ITEM_CHARS = 40
_PARAGRAPH = re.compile(r"\n\s*\n")


def _is_risk_section(section: str) -> bool:
    lowered = (section or "").lower()
    return "1a" in lowered or "risk factor" in lowered


def _risk_chunks(session: Session, filing_id: str) -> list[DocumentChunk]:
    chunks = session.scalars(
        select(DocumentChunk)
        .where(DocumentChunk.filing_id == filing_id)
        .order_by(DocumentChunk.chunk_index, DocumentChunk.id)
    )
    return [chunk for chunk in chunks if _is_risk_section(chunk.section)]


def _risk_items(chunks: list[DocumentChunk]) -> list[tuple[str, DocumentChunk]]:
    """Split each risk-factor chunk into individual risk items on blank-line boundaries.

    A chunk that is a single paragraph is itself one item, so a corpus chunked one-risk-per-chunk
    and one chunked several-risks-per-block both yield clean per-item units.
    """
    items: list[tuple[str, DocumentChunk]] = []
    for chunk in chunks:
        parts = _PARAGRAPH.split(chunk.chunk_text or "")
        for part in parts:
            cleaned = part.strip()
            if len(cleaned) >= _MIN_ITEM_CHARS:
                items.append((cleaned, chunk))
    return items


def _citation(item_text: str, chunk: DocumentChunk, filing: Filing) -> dict:
    return {
        "filing_id": filing.id,
        "form_type": filing.form_type,
        "filing_date": filing.filing_date,
        "section": chunk.section,
        "document_url": chunk.source_url or filing.document_url,
        "chunk_index": chunk.chunk_index,
        "quote": item_text,
    }


def _filing_ref(filing: Filing) -> dict:
    return {
        "filing_id": filing.id,
        "form_type": filing.form_type,
        "filing_date": filing.filing_date,
        "document_url": filing.document_url,
    }


def _unavailable(workspace_id: str, note: str, **extra) -> dict:
    return {
        "workspace_id": workspace_id,
        "source_status": "unavailable",
        "note": note,
        "older_filing": None,
        "newer_filing": None,
        "added": [],
        "removed": [],
        "changed": [],
        "method": METHOD,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **extra,
    }


def diff_risk_factors(session: Session, workspace_id: str) -> dict:
    """Diff Item 1A risk factors between the two most recent 10-Ks in the workspace."""
    get_workspace_or_404(session, workspace_id)
    filings = list(
        session.scalars(
            select(Filing)
            .where(Filing.workspace_id == workspace_id, Filing.form_type == "10-K")
            .order_by(Filing.filing_date.desc(), Filing.id.desc())
        )
    )
    if len(filings) < 2:
        return _unavailable(
            workspace_id,
            "A cross-year risk-factor diff needs at least two 10-K filings in this workspace; "
            f"found {len(filings)}. No drift was inferred.",
        )

    newer, older = filings[0], filings[1]
    newer_items = _risk_items(_risk_chunks(session, newer.id))
    older_items = _risk_items(_risk_chunks(session, older.id))
    if not newer_items or not older_items:
        missing = "newer" if not newer_items else "older"
        return _unavailable(
            workspace_id,
            "Item 1A risk factors were not found in the "
            f"{missing} 10-K (filed {(newer if missing == 'newer' else older).filing_date}); "
            "risk-factor drift is unavailable rather than fabricated.",
            older_filing=_filing_ref(older),
            newer_filing=_filing_ref(newer),
        )

    newer_vectors = [embedding_service.embed(text) for text, _ in newer_items]
    older_vectors = [embedding_service.embed(text) for text, _ in older_items]

    # Candidate matches above the floor, ranked best-first with deterministic index tie-breaks.
    pairs: list[tuple[float, int, int]] = []
    for i, new_vec in enumerate(newer_vectors):
        for j, old_vec in enumerate(older_vectors):
            similarity = embedding_service.cosine(new_vec, old_vec)
            if similarity >= _MATCH_THRESHOLD:
                pairs.append((similarity, i, j))
    pairs.sort(key=lambda pair: (-pair[0], pair[1], pair[2]))

    matched_new: dict[int, tuple[int, float]] = {}
    matched_old: set[int] = set()
    for similarity, i, j in pairs:
        if i in matched_new or j in matched_old:
            continue
        matched_new[i] = (j, similarity)
        matched_old.add(j)

    added = [
        _citation(newer_items[i][0], newer_items[i][1], newer)
        for i in range(len(newer_items))
        if i not in matched_new
    ]
    removed = [
        _citation(older_items[j][0], older_items[j][1], older)
        for j in range(len(older_items))
        if j not in matched_old
    ]
    changed = []
    for i in range(len(newer_items)):
        if i not in matched_new:
            continue
        j, similarity = matched_new[i]
        if similarity >= _SAME_THRESHOLD:
            continue  # essentially identical — not a material change
        changed.append(
            {
                "old": _citation(older_items[j][0], older_items[j][1], older),
                "new": _citation(newer_items[i][0], newer_items[i][1], newer),
                "similarity": round(similarity, 4),
            }
        )

    return {
        "workspace_id": workspace_id,
        "source_status": "ok",
        "note": (
            f"Compared Item 1A risk factors between the 10-K filed {older.filing_date} and the "
            f"10-K filed {newer.filing_date}: {len(added)} added, {len(removed)} removed, "
            f"{len(changed)} materially changed."
        ),
        "older_filing": _filing_ref(older),
        "newer_filing": _filing_ref(newer),
        "added": added,
        "removed": removed,
        "changed": changed,
        "method": METHOD,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
