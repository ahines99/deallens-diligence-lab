"""Heuristic 10-K section extraction (Business / Risk Factors / MD&A).

10-K text repeats item headers in the table of contents, so for each section we pick the
candidate start whose span to the next section boundary is LARGEST — that reliably selects the
real section body over the short TOC entry. Inline cross-references ("see Item 3 ... below")
are excluded as candidate starts first, since a pointer in an EARLIER section would otherwise
open the largest span of all. Purely heuristic and honest about it.
"""
from __future__ import annotations

import bisect
import re

# Item boundary patterns. Lookaheads avoid matching 1A/1B/1C/10.. when we want "Item 1", etc.
# Excluding every trailing letter (not an explicit a/b list) keeps this correct as the SEC
# adds subsections — Item 1C (Cybersecurity) is mandatory for fiscal years ending >= 2023-12-15.
_P_ITEM1 = r"item\s*1(?![0-9a-z])"
_P_ITEM1A = r"item\s*1a\b"
_P_ITEM1B = r"item\s*1b\b"
_P_ITEM2 = r"item\s*2(?![0-9])"
_P_ITEM7 = r"item\s*7(?![0-9a])"
_P_ITEM7A = r"item\s*7a\b"
_P_ITEM8 = r"item\s*8(?![0-9])"
# G67: Item 3 (Legal Proceedings) is bounded by the Item 4 (Mine Safety Disclosures) header.
# The same trailing-character lookahead discipline as _P_ITEM1 applies: excluding every trailing
# digit/letter keeps "Item 3" from matching "Item 30" or a future "Item 3A", and keeps "Item 4"
# from matching "Item 401 of Regulation S-K" cross-references.
_P_ITEM3 = r"item\s*3(?![0-9a-z])"
_P_ITEM4 = r"item\s*4(?![0-9a-z])"
_P_ITEM5 = r"item\s*5(?![0-9a-z])"


def _starts(text: str, pat: str) -> list[int]:
    return [m.start() for m in re.finditer(pat, text, re.IGNORECASE)]


# An "Item N" occurrence that is textually an inline cross-reference — "see Item 3, Legal
# Proceedings, below", "described in Part I, Item 1A" — must not act as a section boundary in
# either direction. As a candidate START, a pointer in an earlier section opens a LARGER span
# than the real body (it swallows everything up to the boundary) and the largest-span rule
# would prefer it: the extracted "section" would be another section's prose. As an END, a
# pointer inside the real body ("for mine safety matters, see Item 4") would truncate the
# section at the pointer. Occurrences whose immediately preceding words read as a
# cross-reference lead-in are therefore excluded on both sides. The lead-in list is a
# heuristic enumeration of the common phrasings — an unlisted phrasing falls back to the
# pre-filter behavior — and a false positive on a real header degrades to a not-located
# section, the honest degradation every caller already handles.
_CROSS_REF_LEAD = re.compile(
    r"(?:\bsee|\brefer\s+to|\bpursuant\s+to|\breference\s+to|"
    r"\b(?:described|discussed|included|reported|set\s+forth|contained)\s+(?:in|under)|"
    r"\bincorporated\s+(?:herein\s+)?by\s+reference(?:\s+(?:from|to|in))?)"
    r"\s*[:,]?\s*(?:part\s+[ivx]+\s*[,.]?\s*)?[\"'“”‘’(]*\s*\Z",
    re.IGNORECASE,
)


def _is_cross_reference(text: str, start: int) -> bool:
    return bool(_CROSS_REF_LEAD.search(text, max(0, start - 60), start))


def _largest_span(text: str, start_pat: str, end_pats: list[str]) -> str:
    starts = [s for s in _starts(text, start_pat) if not _is_cross_reference(text, s)]
    ends: list[int] = []
    for ep in end_pats:
        ends += [e for e in _starts(text, ep) if not _is_cross_reference(text, e)]
    ends.sort()
    best = ""
    for s in starts:
        idx = bisect.bisect_right(ends, s)
        e = ends[idx] if idx < len(ends) else len(text)
        if e - s > len(best):
            best = text[s:e]
    return best.strip()


def extract_sections(text: str) -> dict[str, str]:
    """Return {'Business':..., 'Risk Factors':..., "Management's Discussion & Analysis":...}.

    Values may be empty strings if a section can't be located.
    """
    if not text:
        return {}
    business = _largest_span(text, _P_ITEM1, [_P_ITEM1A])
    risk = _largest_span(text, _P_ITEM1A, [_P_ITEM1B, _P_ITEM2])
    mdna = _largest_span(text, _P_ITEM7, [_P_ITEM7A, _P_ITEM8])
    out: dict[str, str] = {}
    if len(business) > 400:
        out["Business (Item 1)"] = business
    if len(risk) > 400:
        out["Risk Factors (Item 1A)"] = risk
    if len(mdna) > 400:
        out["Management's Discussion & Analysis (Item 7)"] = mdna
    return out


def extract_legal_proceedings(text: str) -> str:
    """Item 3 (Legal Proceedings) body from 10-K text, or ``""`` when it cannot be located.

    Same largest-span heuristic as ``extract_sections`` (the real body beats the short TOC
    entry). Deliberately NOT gated on a minimum length: a terse Item 3 ("None.") is still a
    located section. Callers must treat a non-located section honestly — the heuristic failing
    to find Item 3 is NOT evidence that the company has no legal proceedings.

    Kept out of ``extract_sections`` so ingestion-time chunking (and every surface derived from
    it) is unchanged; litigation extraction (G67) calls this directly.
    """
    if not text:
        return ""
    # Item 5 is a fallback boundary (mirrors Risk Factors' Item 1B + Item 2 bounds) so a filing
    # with an unusual Item 4 header can never make Item 3 swallow the rest of the document.
    return _largest_span(text, _P_ITEM3, [_P_ITEM4, _P_ITEM5])


def split_paragraphs(section_text: str, min_len: int = 200, max_len: int = 1600) -> list[str]:
    """Split a section into paragraph-ish chunks suitable for retrieval / evidence snippets."""
    # 10-K risk factors are often bolded lead-ins; split on sentence-ish boundaries into windows.
    sentences = re.split(r"(?<=[.;])\s+(?=[A-Z(])", section_text)
    chunks: list[str] = []
    buf = ""
    for s in sentences:
        if buf and len(buf) + len(s) > max_len:
            if len(buf) >= min_len:
                chunks.append(buf.strip())
                buf = s
            else:
                # Never silently drop filing text: a buffer too short to stand alone rides
                # along with the next sentence even when the merge exceeds max_len (soft bound).
                buf = f"{buf} {s}"
        else:
            buf = f"{buf} {s}" if buf else s
    if buf.strip() and len(buf.strip()) >= min_len:
        chunks.append(buf.strip())
    return chunks
