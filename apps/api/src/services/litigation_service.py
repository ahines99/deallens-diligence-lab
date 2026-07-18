"""G67 — Litigation & proceedings: 10-K Item 3 extraction + 8-K legal-events timeline.

Two keyless SEC sources, each honest about its limits:

* Item 3 ("Legal Proceedings") is pulled from the latest 10-K's text with the same heuristic
  section machinery as the other items and chunked with citations to the filing. A workspace
  whose 10-K text cannot be fetched reports ``unavailable`` — the ABSENCE of an extracted
  section is never presented as "no litigation".
* The 8-K timeline includes only item codes whose taxonomy label is explicitly a legal
  proceeding (Item 1.03 "Bankruptcy or Receivership"). The SEC 8-K item taxonomy has NO
  dedicated litigation item — material legal developments usually ride under Item 8.01 "Other
  Events", which the item code alone cannot classify as legal. That limitation is stated in the
  payload note instead of being papered over with keyword guesses.

``risk_flags`` follows the forensics/sec_feeds degraded-source contract (same signature and
finding shape; degrades to ``[]`` on any failure). Integration note: the orchestrator's
``analysis_service`` extension loop iterates ``("forensics_service", "sec_feeds_service")`` by
module name — adding ``"litigation_service"`` to that tuple is the (integrator-owned) wiring
step that surfaces these flags in analysis runs.
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from src.db.base import now_utc
from src.models import Filing
from src.services import edgar_client, sec_feeds_service
from src.services.common import NotFound, get_workspace_or_404
from src.services.edgar_client import EdgarError
from src.services.filing_sections import extract_legal_proceedings, split_paragraphs
from src.services.workspace_service import get_target

logger = logging.getLogger("deallens.litigation")

# 8-K item codes whose EIGHT_K_ITEMS label is explicitly a legal proceeding. Item 1.03
# (Bankruptcy or Receivership) is the only such label; 8.01 "Other Events" often carries legal
# matters but is NOT classifiable as legal from the code alone, so it is excluded and the
# limitation is disclosed.
LEGAL_8K_ITEM_CODES = frozenset({"1.03"})

# Deterministic review screen: an Item 3 body at least this long is flagged for review. This is
# a length-based screen for the PRESENCE of substantive disclosure, never a merits judgment —
# short bodies ("None.") are still reported in the payload, just not flagged.
ITEM3_REVIEW_MIN_CHARS = 600

# Cap the excerpt chunks returned inline (the full section remains cited via the filing URL).
MAX_EXCERPT_CHUNKS = 20

TAXONOMY_LIMITATION_NOTE = (
    "The SEC 8-K item taxonomy has no dedicated litigation item; only explicitly legal item "
    "codes (1.03 Bankruptcy or Receivership) are classified here. Material legal developments "
    "often ride under Item 8.01 'Other Events', which cannot be identified as legal from the "
    "item code alone — this timeline is therefore NOT a complete litigation history."
)


def _latest_tenk(session, workspace_id: str) -> Filing | None:
    return session.scalar(
        select(Filing)
        .where(Filing.workspace_id == workspace_id, Filing.form_type == "10-K")
        .order_by(Filing.filing_date.desc())
    )


def _filing_ref(filing: Filing) -> dict:
    return {
        "form_type": filing.form_type,
        "filing_date": filing.filing_date,
        "accession_number": filing.accession_number,
        "document_url": filing.document_url,
    }


def _item3(session, workspace_id: str) -> tuple[dict, str | None, str]:
    """Extract Item 3 from the latest 10-K.

    Returns ``(item3_payload, raw_section_text | None, status)`` where status is ``available``
    (section located), ``partial`` (text fetched but the heuristic could not locate Item 3), or
    ``unavailable`` (no 10-K on file / text unfetchable) — never a false-clean.
    """
    filing = _latest_tenk(session, workspace_id)
    if filing is None or not filing.document_url:
        return (
            {
                "present": False,
                "excerpt_chunks": [],
                "filing": _filing_ref(filing) if filing is not None else None,
                "note": (
                    "No 10-K document is on file for this workspace — Item 3 could not be "
                    "examined. This is NOT evidence of an absence of legal proceedings."
                ),
            },
            None,
            "unavailable",
        )
    try:
        text = edgar_client.fetch_document_text(filing.document_url)
    except EdgarError as exc:
        logger.warning("litigation: 10-K text fetch failed for %s: %s", workspace_id, exc)
        text = ""
    if not text:
        return (
            {
                "present": False,
                "excerpt_chunks": [],
                "filing": _filing_ref(filing),
                "note": (
                    "The 10-K text is temporarily unavailable — Item 3 could not be examined. "
                    "This is NOT evidence of an absence of legal proceedings."
                ),
            },
            None,
            "unavailable",
        )

    section = extract_legal_proceedings(text)
    if not section:
        return (
            {
                "present": False,
                "excerpt_chunks": [],
                "filing": _filing_ref(filing),
                "note": (
                    "Item 3 (Legal Proceedings) could not be located by the heuristic section "
                    "extractor. This is NOT evidence of an absence of legal proceedings."
                ),
            },
            None,
            "partial",
        )

    # A terse Item 3 ("None.") falls under split_paragraphs' min_len and must not vanish:
    # filing text never silently disappears from the chunks.
    chunks = split_paragraphs(section) or [section]
    truncated = len(chunks) > MAX_EXCERPT_CHUNKS
    excerpt_chunks = [
        {
            "chunk_index": i,
            "section": "Legal Proceedings (Item 3)",
            "text": chunk,
            "source_url": filing.document_url,
        }
        for i, chunk in enumerate(chunks[:MAX_EXCERPT_CHUNKS])
    ]
    note = (
        f"Excerpts truncated to the first {MAX_EXCERPT_CHUNKS} of {len(chunks)} chunks; the "
        "full section remains available at the cited filing."
        if truncated
        else None
    )
    return (
        {"present": True, "excerpt_chunks": excerpt_chunks, "filing": _filing_ref(filing), "note": note},
        section,
        "available",
    )


def _legal_events(session, workspace_id: str) -> tuple[list[dict], str, str | None]:
    """8-K events carrying an explicitly legal item code, plus the feed's source status."""
    try:
        feed = sec_feeds_service.events(session, workspace_id)
    except NotFound as exc:
        return [], "unavailable", str(exc)
    rows: list[dict] = []
    for event in feed.get("events", []):
        legal_items = [
            item for item in event.get("items", []) if item.get("code") in LEGAL_8K_ITEM_CODES
        ]
        if legal_items:
            rows.append(
                {
                    "date": event.get("date"),
                    "form": event.get("form"),
                    "items": legal_items,
                    "accession": event.get("accession"),
                    "url": event.get("url"),
                }
            )
    return rows, feed.get("source_status", "unavailable"), feed.get("source_error")


def build(session, workspace_id: str) -> dict:
    """Litigation & proceedings view: Item 3 excerpts + the explicitly-legal 8-K timeline.

    ``status`` is ``unavailable`` when the 10-K text cannot be examined (never presented as
    clean), ``partial`` when Item 3 could not be located or the 8-K feed degraded, and
    ``available`` otherwise. An empty ``events`` list with an available feed means no
    explicitly-legal 8-K item codes were filed — subject to the taxonomy limitation note.
    """
    get_workspace_or_404(session, workspace_id)
    target = get_target(session, workspace_id)
    if target is None:
        raise NotFound("No target set; ingest a company with a ticker first.")

    item3, _section, item3_status = _item3(session, workspace_id)
    events, events_status, events_error = _legal_events(session, workspace_id)

    if item3_status == "unavailable":
        status = "unavailable"
    elif item3_status == "partial" or events_status != "available":
        status = "partial"
    else:
        status = "available"

    notes = [TAXONOMY_LIMITATION_NOTE]
    if events_status != "available":
        notes.append(
            "8-K event feed degraded"
            + (f": {events_error}" if events_error else "")
            + " — the legal-events timeline may be incomplete."
        )
    return {
        "workspace_id": workspace_id,
        "status": status,
        "item3": item3,
        "events": events,
        "note": " ".join(notes),
        "generated_at": now_utc(),
    }


# --- Red-flag findings (same contract as forensics_service/sec_feeds_service.risk_flags) -----
def risk_flags(session, workspace_id: str) -> list[dict]:
    """Deterministic litigation flags, same shape as RiskAnalyst findings.

    Degrades to ``[]`` on any network/data problem so it never breaks the analysis pipeline
    (the caller records the degraded source; an empty list here is never an assertion of
    cleanliness). Signature matches the ``analysis_service`` extension-loop contract.
    """
    target = get_target(session, workspace_id)
    if target is None or not target.cik:
        return []
    name = target.name
    flags: list[dict] = []

    # 1) Bankruptcy/receivership 8-K (Item 1.03) — the explicitly legal 8-K item code.
    try:
        events, _status, _error = _legal_events(session, workspace_id)
    except (NotFound, EdgarError):
        events = []
    for event in events:
        if any(item["code"] == "1.03" for item in event["items"]):
            flags.append(_finding(
                "Bankruptcy or receivership 8-K filed",
                f"{name} filed an Item 1.03 8-K on {event['date']} reporting bankruptcy or "
                f"receivership — a legal proceeding that dominates any diligence thesis.",
                "high", 8, 0.9,
                "What is the scope of the bankruptcy/receivership proceeding, which entities "
                "are included, and what is the expected treatment of existing obligations?",
                {
                    "claim": f"{name} reported bankruptcy or receivership (Item 1.03) on {event['date']}.",
                    "claim_type": "fact",
                    "evidence_text": f"SEC 8-K Item 1.03 filed {event['date']} ({event['form']}).",
                    "source_name": f"SEC {event['form']} ({event['date']})",
                    "source_type": "sec_filing",
                    "source_url": event.get("url"),
                    "source_date": event.get("date"),
                    "source_section": "8-K current report",
                    "confidence": 0.9,
                    "agent_name": "litigation",
                },
            ))
            break  # one bankruptcy flag is enough

    # 2) Substantive Item 3 disclosure -> review flag. A deterministic length screen
    #    (ITEM3_REVIEW_MIN_CHARS), honestly framed as presence-of-disclosure, not a merits call.
    try:
        item3, section, item3_status = _item3(session, workspace_id)
    except Exception as exc:  # noqa: BLE001 — flags must never break the analysis pipeline
        logger.warning("litigation: Item 3 flag check failed for %s: %s", workspace_id, exc)
        return flags
    if item3_status == "available" and section and len(section) >= ITEM3_REVIEW_MIN_CHARS:
        excerpt = section[:500].rsplit(" ", 1)[0]
        filing = item3["filing"] or {}
        flags.append(_finding(
            "Legal Proceedings disclosure in the 10-K (Item 3)",
            f"{name}'s latest 10-K carries a substantive Item 3 (Legal Proceedings) disclosure "
            f"({len(section):,} characters). This is a deterministic length screen flagging the "
            f"presence of disclosure for counsel review — not a judgment of merit or exposure.",
            "medium", 4, 0.6,
            "Which disclosed proceedings are material, what are the accrued/reasonably possible "
            "loss ranges, and are any indemnification or insurance offsets available?",
            {
                "claim": f"{name}'s 10-K Item 3 contains a legal-proceedings disclosure.",
                "claim_type": "fact",
                "evidence_text": excerpt,
                "source_name": f"{name} 10-K ({filing.get('filing_date') or 'date unknown'})",
                "source_type": "sec_filing",
                "source_url": filing.get("document_url"),
                "source_date": filing.get("filing_date"),
                "source_section": "Legal Proceedings (Item 3)",
                "confidence": 0.6,
                "agent_name": "litigation",
            },
        ))
    return flags


def _finding(title, finding, severity, score, conf, followup, evidence) -> dict:
    return {
        "risk_category": "legal_regulatory",
        "risk_category_label": "Legal / regulatory",
        "title": title,
        "finding": finding,
        "severity": severity,
        "severity_score": score,
        "likelihood": "high" if score >= 6 else "medium",
        "confidence": conf,
        "workstream_owner": "legal_regulatory",
        "follow_up_question": followup,
        "evidence": evidence,
    }
