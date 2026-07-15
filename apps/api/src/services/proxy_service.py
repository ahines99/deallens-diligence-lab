"""DEF 14A proxy ingestion — executive compensation table + governance red flags.

Fetches a target's most recent DEF 14A proxy statement (HTML) via ``edgar_client``, then:
  (a) parses the Summary Compensation Table into named-executive-officer rows
      (name, title, salary, bonus, stock awards, total). Proxy comp tables are notoriously
      messy HTML; parsing is defensive and NEVER imputes — an unextractable value stays ``None``.
  (b) runs governance red-flag heuristics over the proxy text (staggered/classified board,
      dual-class share structure, combined CEO/Chair, poison pill / rights plan), each a
      boolean with the evidence snippet that fired it.

The pure functions ``parse_summary_compensation_table`` and ``detect_red_flags`` take raw
strings and are unit-tested offline against synthetic proxy HTML — no live SEC access.

``source_status`` preserves the available/partial/unavailable contract: a failed fetch is
reported (and persisted) as ``unavailable`` with empty comp/flags, never a false-clean result.
"""
from __future__ import annotations

import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models import GovernanceProfile
from src.services import edgar_client
from src.services.common import NotFound, get_workspace_or_404
from src.services.edgar_client import EdgarError
from src.services.workspace_service import get_target

logger = logging.getLogger("deallens.proxy")

DEF14A_FORMS = ("DEF 14A", "DEFA14A", "DEFR14A")


# --- money / cell parsing --------------------------------------------------
_EMPTY_TOKENS = {"", "—", "–", "-", "--", "n/a", "na", "nil", "*", "†", "(1)", "$"}


def _parse_money(raw: str | None) -> float | None:
    """Parse a compensation cell like ``$1,234,567`` into a float; unextractable -> None.

    Never imputes: blanks, dashes, and footnote markers become ``None`` rather than 0.
    """
    s = (raw or "").strip()
    if s.lower() in _EMPTY_TOKENS:
        return None
    cleaned = s.replace(",", "").replace("$", "").strip()
    if cleaned.lower() in _EMPTY_TOKENS:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", cleaned)
    if m is None:
        return None
    try:
        return float(m.group())
    except ValueError:
        return None


def _cell_text(cell) -> str:
    return cell.get_text(" ", strip=True).replace("\xa0", " ").strip()


def _split_name_title(cell) -> tuple[str, str | None]:
    """Split a 'Name and Principal Position' cell into (name, title).

    Real proxies stack the name and title on separate lines (``<br>``); some collapse them
    into ``Name, Title``. Falls back to (whole text, None) when no separator is present.
    """
    parts = [p.strip() for p in cell.get_text("\n").split("\n") if p.strip()]
    if len(parts) >= 2:
        return parts[0], " ".join(parts[1:])
    single = parts[0] if parts else _cell_text(cell)
    if "," in single:
        name, _, title = single.partition(",")
        return name.strip(), title.strip() or None
    return single, None


def parse_summary_compensation_table(html: str) -> list[dict]:
    """Parse the Summary Compensation Table from proxy HTML into NEO rows.

    Returns ``[{name, title, salary, bonus, stock_awards, total}, ...]``. A value that cannot
    be extracted (missing column, blank/dash cell) is left ``None`` — never imputed.
    """
    if not html:
        return []
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        header_idx = None
        headers: list[str] = []
        for i, tr in enumerate(rows):
            cells = [_cell_text(c).lower() for c in tr.find_all(["th", "td"])]
            joined = " ".join(cells)
            # The Summary Compensation Table header always carries a Salary and a Total column.
            if "salary" in joined and "total" in joined:
                header_idx = i
                headers = cells
                break
        if header_idx is None:
            continue

        col: dict[str, int] = {}
        for j, h in enumerate(headers):
            if ("name" in h or "principal position" in h) and "name" not in col:
                col["name"] = j
            elif "salary" in h and "salary" not in col:
                col["salary"] = j
            elif "bonus" in h and "bonus" not in col:
                col["bonus"] = j
            elif "stock award" in h and "stock_awards" not in col:
                col["stock_awards"] = j
            elif "total" in h and "total" not in col:
                col["total"] = j
        name_col = col.get("name", 0)

        neos: list[dict] = []
        for tr in rows[header_idx + 1:]:
            cells = tr.find_all(["th", "td"])
            if not cells or name_col >= len(cells):
                continue
            texts = [_cell_text(c) for c in cells]
            if not any(texts):
                continue
            name, title = _split_name_title(cells[name_col])
            if not name:
                continue

            def _val(field: str, cells_texts: list[str] = texts) -> float | None:
                idx = col.get(field)
                if idx is None or idx >= len(cells_texts):
                    return None
                return _parse_money(cells_texts[idx])

            neos.append(
                {
                    "name": name,
                    "title": title,
                    "salary": _val("salary"),
                    "bonus": _val("bonus"),
                    "stock_awards": _val("stock_awards"),
                    "total": _val("total"),
                }
            )
        if neos:
            return neos
    return []


# --- governance red-flag heuristics ----------------------------------------
# Each rule: (key, label, [regex patterns]). A single pattern match sets the flag with the
# surrounding text as evidence. Heuristic and honest about it — a flag is a lead, not a verdict.
_FLAG_RULES: list[tuple[str, str, list[str]]] = [
    (
        "staggered_board",
        "Staggered / classified board",
        [
            r"classified board",
            r"staggered board",
            r"staggered terms",
            r"(?:three|3) classes of directors",
            r"divided into (?:three|3) classes",
            r"board .{0,40}?(?:three|3) classes",
        ],
    ),
    (
        "dual_class",
        "Dual-class share structure",
        [
            r"class\s+b\s+common",
            r"super[-\s]?voting",
            r"(?:ten|10)\s+votes?\s+per\s+share",
            r"class\s+a\s+common\s+stock.{0,120}?class\s+b",
            r"high[-\s]?vote\s+(?:class|shares)",
        ],
    ),
    (
        "combined_ceo_chair",
        "Combined CEO and Board Chair",
        [
            r"chairman\s+and\s+chief executive officer",
            r"chair(?:man|person|woman)?\s*,?\s*(?:president\s+and\s+)?chief executive officer",
            r"chairman[^.]{0,30}?\band\b[^.]{0,10}?\bceo\b",
            r"combined\s+(?:role|position)s?\s+of\s+(?:chair|chairman)",
        ],
    ),
    (
        "poison_pill",
        "Poison pill / shareholder rights plan",
        [
            r"poison pill",
            r"(?:share|stock)holder rights plan",
            r"rights agreement",
        ],
    ),
]


def _first_match(text: str, patterns: list[str]) -> re.Match | None:
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE | re.DOTALL)
        if m:
            return m
    return None


def _snippet(text: str, match: re.Match, width: int = 160) -> str:
    start = max(0, match.start() - width // 2)
    end = min(len(text), match.end() + width // 2)
    return re.sub(r"\s+", " ", text[start:end]).strip()


def detect_red_flags(text: str) -> list[dict]:
    """Run governance red-flag heuristics over proxy text.

    Returns one entry per rule: ``{flag, label, present, evidence}``. ``evidence`` is the
    surrounding text snippet when the flag fires, else ``None``.
    """
    src = text or ""
    out: list[dict] = []
    for key, label, patterns in _FLAG_RULES:
        m = _first_match(src, patterns)
        out.append(
            {
                "flag": key,
                "label": label,
                "present": m is not None,
                "evidence": _snippet(src, m) if m is not None else None,
            }
        )
    return out


# --- fetch + orchestration -------------------------------------------------
def _html_to_text(html: str) -> str:
    if not html:
        return ""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(" ").replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _cik10(cik: str) -> str:
    return str(cik).lstrip("0").zfill(10) if cik else ""


def _unavailable(note: str, *, accession: str | None = None, filing_date: str | None = None) -> dict:
    return {
        "def14a_accession": accession,
        "filing_date": filing_date,
        "exec_comp": [],
        "red_flags": [],
        "source_status": "unavailable",
        "raw_note": note,
    }


def fetch_proxy_governance(cik10: str) -> dict:
    """Fetch the most recent DEF 14A for a CIK and parse comp + red flags.

    Pure of persistence; returns the parsed payload dict with an explicit ``source_status``.
    Any EDGAR outage or missing proxy yields ``unavailable`` — never a false-clean empty parse.
    """
    try:
        filings = edgar_client.recent_filings(cik10, DEF14A_FORMS, limit=1)
    except EdgarError as exc:
        logger.warning("proxy: submissions fetch failed for %s: %s", cik10, exc)
        return _unavailable("SEC EDGAR submissions are temporarily unavailable.")
    if not filings:
        return _unavailable("No DEF 14A proxy statement is on file for this company.")

    proxy = filings[0]
    try:
        html = edgar_client.fetch_document_html(proxy.primary_doc_url)
    except EdgarError as exc:
        logger.warning("proxy: document fetch failed for %s: %s", proxy.primary_doc_url, exc)
        return _unavailable(
            "The DEF 14A proxy document could not be retrieved.",
            accession=proxy.accession,
            filing_date=proxy.filing_date,
        )
    if not html:
        return _unavailable(
            "The DEF 14A proxy document was empty.",
            accession=proxy.accession,
            filing_date=proxy.filing_date,
        )

    exec_comp = parse_summary_compensation_table(html)
    red_flags = detect_red_flags(_html_to_text(html))

    if exec_comp:
        status, note = "available", None
    else:
        # The proxy was retrieved and red flags scanned, but the comp table could not be parsed.
        status, note = (
            "partial",
            "Proxy retrieved and scanned, but the Summary Compensation Table could not be parsed.",
        )
    return {
        "def14a_accession": proxy.accession,
        "filing_date": proxy.filing_date,
        "exec_comp": exec_comp,
        "red_flags": red_flags,
        "source_status": status,
        "raw_note": note,
    }


def _upsert(session: Session, workspace_id: str, payload: dict) -> GovernanceProfile:
    profile = session.scalar(
        select(GovernanceProfile).where(GovernanceProfile.workspace_id == workspace_id)
    )
    if profile is None:
        profile = GovernanceProfile(workspace_id=workspace_id)
        session.add(profile)
    profile.def14a_accession = payload["def14a_accession"]
    profile.filing_date = payload["filing_date"]
    profile.exec_comp = payload["exec_comp"]
    profile.red_flags = payload["red_flags"]
    profile.source_status = payload["source_status"]
    profile.raw_note = payload["raw_note"]
    session.flush()
    return profile


def build_profile(session: Session, workspace_id: str) -> GovernanceProfile:
    """Fetch + parse the target's DEF 14A and persist the governance profile (re-runs on demand)."""
    get_workspace_or_404(session, workspace_id)
    target = get_target(session, workspace_id)
    if target is None or not target.cik:
        raise NotFound(
            "Target has no SEC CIK; DEF 14A proxy ingestion requires a public (EDGAR) company."
        )
    payload = fetch_proxy_governance(_cik10(target.cik))
    return _upsert(session, workspace_id, payload)


def get(session: Session, workspace_id: str) -> GovernanceProfile:
    profile = session.scalar(
        select(GovernanceProfile).where(GovernanceProfile.workspace_id == workspace_id)
    )
    if profile is None:
        raise NotFound("No governance profile generated yet.")
    return profile


def get_optional(session: Session, workspace_id: str) -> GovernanceProfile | None:
    return session.scalar(
        select(GovernanceProfile).where(GovernanceProfile.workspace_id == workspace_id)
    )
