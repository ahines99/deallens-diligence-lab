"""Heuristic 10-K section extraction (Business / Risk Factors / MD&A).

10-K text repeats item headers in the table of contents, so for each section we pick the
candidate start whose span to the next section boundary is LARGEST — that reliably selects the
real section body over the short TOC entry. Purely heuristic and honest about it.
"""
from __future__ import annotations

import bisect
import re

# Item boundary patterns. Lookaheads avoid matching 1A/1B/10.. when we want "Item 1", etc.
_P_ITEM1 = r"item\s*1(?![0-9ab])"
_P_ITEM1A = r"item\s*1a\b"
_P_ITEM1B = r"item\s*1b\b"
_P_ITEM2 = r"item\s*2(?![0-9])"
_P_ITEM7 = r"item\s*7(?![0-9a])"
_P_ITEM7A = r"item\s*7a\b"
_P_ITEM8 = r"item\s*8(?![0-9])"


def _starts(text: str, pat: str) -> list[int]:
    return [m.start() for m in re.finditer(pat, text, re.IGNORECASE)]


def _largest_span(text: str, start_pat: str, end_pats: list[str]) -> str:
    starts = _starts(text, start_pat)
    ends: list[int] = []
    for ep in end_pats:
        ends += _starts(text, ep)
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
            buf = f"{buf} {s}" if buf else s
    if buf.strip() and len(buf.strip()) >= min_len:
        chunks.append(buf.strip())
    return chunks
