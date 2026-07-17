"""G52 — LLM-first risk extraction with verbatim-span verification.

The Wave 5 inversion of :class:`RiskAnalyst.scan_text`: an LLM reads the same filing chunks the
signal-phrase scanner reads and proposes findings, each anchored to an exact quote and the index
of the excerpt it came from. A deterministic verifier then accepts a finding ONLY if:

* its ``chunk_index`` points at an excerpt that was actually sent to the model;
* its ``quote`` appears VERBATIM in that excerpt's text under whitespace normalization only
  (runs of whitespace collapse; no case folding, no punctuation stripping — a paraphrase fails);
* its ``category`` is a registered taxonomy slug;
* its ``severity_score`` coerces to an int (then clamped to 1..10).

Verified findings are mapped into exactly the dict shape ``RiskAnalyst.scan_text`` returns, so
the rest of the pipeline (evidence rows, risk findings, plan, memo) is engine-agnostic. The
Evidence row's claim/quote are deterministic-template + verified span — no unverified LLM prose
ever becomes evidence. Everything else fails closed: when the structured-LLM substrate does not
apply (mock CI, no consent, no key, parse/schema failure) or nothing survives verification, the
caller runs the deterministic scanner, which remains the offline path and the recall baseline.
"""
from __future__ import annotations

import re

from pydantic import BaseModel

from src.agents.llm_provider import structured_llm
from src.agents.risk_analyst import _CONDITIONAL_MARKERS, _REALIZED_MARKERS, RiskAnalyst

# Recorded as Evidence.agent_name so the audit trail shows which engine produced a finding.
AGENT_NAME = "llm_risk_extractor"

# Span-verified quotes warrant more confidence than the scanner's keyword heuristics (capped at
# 0.72), but still short of XBRL-bound calculations (0.85-0.95): the quote is proven real, the
# risk interpretation is still a model judgment.
_VERIFIED_CONFIDENCE = 0.85

# ~30k chars ≈ ~7.5k tokens of excerpt text: bounds live-call cost and context use while still
# covering the risk-factor/MD&A material the deterministic scanner reads, so both engines see
# substantially the same source pool for the G52 comparison artifact.
_MAX_EXCERPT_CHARS = 30_000

_WS_RUN = re.compile(r"\s+")


class ProposedFinding(BaseModel):
    """One LLM-proposed finding — untrusted until the verifier accepts its quote."""

    category: str
    quote: str
    chunk_index: int
    # Loosely typed on purpose: a float severity truncates rather than sinking the whole payload
    # to schema_mismatch; title/finding may be absent and get deterministic fallbacks.
    severity_score: float
    title: str = ""
    finding: str = ""


class RiskExtractionPayload(BaseModel):
    """The JSON object shape the registered ``risk_extraction`` prompt demands."""

    findings: list[ProposedFinding]


def _normalize_ws(text: str) -> str:
    """Collapse whitespace runs to single spaces — the ONLY tolerance the verifier grants.

    No case folding and no punctuation stripping: a reworded, re-cased, or "cleaned up" quote
    must fail verification, because verbatim-ness is the entire anti-fabrication guarantee.
    """
    return _WS_RUN.sub(" ", text).strip()


def _select_excerpts(chunks: list) -> list:
    """The chunk pool the scanner prefers, capped at ``_MAX_EXCERPT_CHARS`` of excerpt text.

    Mirrors ``RiskAnalyst.scan_text``: risk-factor/MD&A sections first, all chunks as fallback.
    The verifier later indexes into exactly this list, so ``chunk_index`` always refers to an
    excerpt the model actually saw.
    """
    focus = [c for c in chunks if "Risk Factors" in c.section or "Discussion" in c.section]
    pool = focus or list(chunks)
    excerpts: list = []
    total = 0
    for chunk in pool:
        # Always include at least one excerpt so a single oversized chunk is not silently dropped.
        if excerpts and total + len(chunk.chunk_text) > _MAX_EXCERPT_CHARS:
            break
        excerpts.append(chunk)
        total += len(chunk.chunk_text)
    return excerpts


def _user_prompt(excerpts: list, taxonomy: dict, filing_ctx: dict) -> str:
    """Compose the user prompt: allowed slugs plus the numbered excerpts (0..N-1)."""
    slugs = "\n".join(
        f"- {cat['slug']} ({cat['label']}; workstream: {cat['workstream_owner']})"
        for cat in taxonomy["categories"]
    )
    numbered = "\n\n".join(
        f"Excerpt {i} ({chunk.section}):\n{chunk.chunk_text}"
        for i, chunk in enumerate(excerpts)
    )
    return (
        f"Company: {filing_ctx.get('company', 'the target')}\n\n"
        f"Allowed category slugs:\n{slugs}\n\n"
        "Filing excerpts (copy quotes verbatim; report the excerpt number as chunk_index):\n\n"
        f"{numbered}"
    )


def _severity_band(score: int) -> str:
    """Mirror the scanner's ``_severity_from_hits`` thresholds so bands mean the same thing."""
    if score >= 7:
        return "high"
    if score >= 4:
        return "medium"
    return "low"


def _to_scanner_shape(
    proposed: ProposedFinding, chunk, category: dict, filing_ctx: dict
) -> dict:
    """Map a verified proposal into exactly the dict shape ``RiskAnalyst.scan_text`` returns.

    The evidence claim is a deterministic template and ``evidence_text`` is the verified span, so
    the Evidence row contains no free-form LLM prose; the LLM's narrative lives only in the
    finding/title fields, which are anchored to that verified span.
    """
    label = category["label"]
    quote = _normalize_ws(proposed.quote)
    # Same realized/conditional vocabulary as the scanner, applied to the verified span, so the
    # likelihood field keeps identical semantics across engines.
    padded = f" {quote.lower()} "
    realized = any(marker in padded for marker in _REALIZED_MARKERS)
    conditional = any(marker in padded for marker in _CONDITIONAL_MARKERS)
    score = max(1, min(10, int(proposed.severity_score)))
    return {
        "risk_category": category["slug"],
        "risk_category_label": label,
        "title": _normalize_ws(proposed.title) or f"{label} disclosure requires diligence",
        "finding": _normalize_ws(proposed.finding)
        or (
            f"The {filing_ctx['company']} 10-K discusses {label.lower()} "
            f"({chunk.section}). Representative disclosure: “{quote}”"
        ),
        "severity": _severity_band(score),
        "severity_score": score,
        "likelihood": "low" if conditional and not realized else "medium",
        "confidence": _VERIFIED_CONFIDENCE,
        "workstream_owner": category["workstream_owner"],
        "follow_up_question": (
            f"Quantify the {label.lower()} exposure and management's mitigation, "
            f"beyond the risk-factor language."
        ),
        "evidence": {
            "claim": f"{filing_ctx['company']}'s 10-K discusses {label.lower()}.",
            "claim_type": "fact",
            "evidence_text": quote,
            "source_name": f"{filing_ctx['company']} 10-K — {chunk.section}",
            "source_type": "sec_filing",
            "source_url": filing_ctx.get("url"),
            "source_date": filing_ctx.get("date"),
            "source_section": chunk.section,
            "confidence": _VERIFIED_CONFIDENCE,
            "agent_name": AGENT_NAME,
        },
    }


def _fallback(
    reason: str, manifest: dict | None = None, proposed: int = 0, rejected: int = 0
) -> dict:
    """Deterministic-engine provenance: the caller must run the scanner and record why."""
    return {
        "engine": "deterministic",
        "reason": reason,
        "manifest": manifest,
        "proposed": proposed,
        "verified": 0,
        "rejected": rejected,
    }


def extract(
    chunks: list,
    taxonomy: dict,
    filing_ctx: dict,
    *,
    external_allowed: bool,
    provider_factory=None,
) -> tuple[list[dict], dict]:
    """Propose findings via the LLM and keep only span-verified ones, failing closed.

    Returns ``(findings, provenance)``. ``findings`` are scanner-shaped dicts; ``provenance`` is
    ``{"engine", "reason", "manifest", "proposed", "verified", "rejected"}``. Whenever ``engine``
    is ``"deterministic"`` the findings list is empty and the caller runs ``scan_text`` — this
    module never partially substitutes for the scanner.
    """
    excerpts = _select_excerpts(chunks)
    outcome = structured_llm(
        "risk_extraction",
        _user_prompt(excerpts, taxonomy, filing_ctx),
        RiskExtractionPayload,
        external_allowed=external_allowed,
        provider_factory=provider_factory,
    )
    if not outcome.applied or outcome.data is None:
        # Substrate did not apply (no_consent / mock / no_api_key / parse_error /
        # schema_mismatch / error): no LLM output exists to verify.
        return [], _fallback(outcome.reason, outcome.manifest)

    categories = {cat["slug"]: cat for cat in taxonomy["categories"]}
    proposals: list[ProposedFinding] = outcome.data.findings
    verified: list[dict] = []
    rejected = 0
    for proposal in proposals:
        if not (0 <= proposal.chunk_index < len(excerpts)):
            rejected += 1
            continue
        chunk = excerpts[proposal.chunk_index]
        quote = _normalize_ws(proposal.quote)
        # The verbatim gate: an empty, paraphrased, or fabricated quote is dropped here.
        if not quote or quote not in _normalize_ws(chunk.chunk_text):
            rejected += 1
            continue
        category = categories.get(proposal.category)
        if category is None:
            rejected += 1
            continue
        try:
            int(proposal.severity_score)
        except (OverflowError, ValueError):  # e.g. NaN/inf survived pydantic's float coercion
            rejected += 1
            continue
        verified.append(_to_scanner_shape(proposal, chunk, category, filing_ctx))

    if not verified:
        # The model answered but nothing survived verification (or it proposed nothing). The
        # scanner serves the result so a hallucinating model can never blank out risk coverage.
        return [], _fallback(
            "no_verified_findings", outcome.manifest, proposed=len(proposals), rejected=rejected
        )
    return verified, {
        "engine": "llm",
        "reason": outcome.reason,
        "manifest": outcome.manifest,
        "proposed": len(proposals),
        "verified": len(verified),
        "rejected": rejected,
    }


def compare_with_scanner(
    chunks: list,
    taxonomy: dict,
    filing_ctx: dict,
    *,
    external_allowed: bool,
    provider_factory=None,
) -> dict:
    """G52 comparison artifact: both engines over the same chunks, diffed by category slug.

    The scanner doubles as the recall baseline here — a category it flags that the LLM missed
    (``scanner_only``) is a recall gap worth investigating, while ``llm_only`` slugs are the
    LLM's verified additions beyond the signal phrases.
    """
    llm_findings, llm_provenance = extract(
        chunks,
        taxonomy,
        filing_ctx,
        external_allowed=external_allowed,
        provider_factory=provider_factory,
    )
    scanner_findings = RiskAnalyst().scan_text(chunks, taxonomy, filing_ctx)
    llm_slugs = {f["risk_category"] for f in llm_findings}
    scanner_slugs = {f["risk_category"] for f in scanner_findings}
    return {
        "llm_only": sorted(llm_slugs - scanner_slugs),
        "scanner_only": sorted(scanner_slugs - llm_slugs),
        "both": sorted(llm_slugs & scanner_slugs),
        "llm_provenance": llm_provenance,
    }
