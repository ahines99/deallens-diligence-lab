"""G04 — Grounded synthesis mode (live LLM), gated by the citation auditor.

The default filings Q&A is purely extractive: verbatim sentences from real filings. When a
workspace has consented to an external LLM *and* live mode is configured, this module offers an
optional fluency pass: it asks the LLM to re-voice the already-retrieved extracts into one smooth
answer, then runs :class:`CitationAuditor` against the extractive source. The rewrite is served
only if it added or dropped no number and no citation. On any drift — a fabricated figure, an
invented ``[EV-###]`` ref, an empty or errored response — it FALLS BACK to the extractive answer.

Fail-closed guarantees, all exercised by ``tests/test_grounded_synthesis.py``:

* No consent / mock mode / no API key  → extractive answer, unchanged.
* A provider that fabricates a number or citation → rejected, extractive answer served.
* An abstention stays an abstention; the LLM is never asked to invent evidence.
"""
from __future__ import annotations

from collections.abc import Callable

from src.agents.citation_auditor import CitationAuditor
from src.agents.llm_provider import LiveProvider
from src.config import settings
from src.services import prompt_registry

# Statuses whose answers carry real extracted evidence and are eligible for a fluency pass.
_ELIGIBLE = {"answered", "partial"}


def _user_prompt(result: dict) -> str:
    """Compose the LLM instruction from the retrieved extracts only (no outside context)."""
    quotes = [c.get("quote", "") for c in result.get("citations", []) if c.get("quote")]
    if not quotes:
        quotes = [result.get("answer", "")]
    numbered = "\n".join(f"- {q}" for q in quotes)
    return (
        "Rewrite the following verbatim filing extracts into one fluent answer to the question "
        f"{result.get('question', '')!r}. Preserve every number and citation exactly.\n\n"
        f"Extracts:\n{numbered}"
    )


def maybe_synthesize(
    result: dict,
    *,
    external_allowed: bool,
    provider_factory: Callable[[], LiveProvider] = LiveProvider,
) -> dict:
    """Optionally re-voice ``result``'s extractive answer for fluency, failing closed on drift.

    Returns a new dict. When the fluency pass does not run or does not pass the auditor, the
    extractive answer is preserved byte-for-byte and a machine-readable ``grounded`` provenance
    block records why. The auditor's source is the extractive answer, so no number or citation can
    be introduced or lost without triggering fallback.
    """
    # Abstention is preserved untouched: never ask an LLM to compose evidence that does not exist.
    if result.get("status") not in _ELIGIBLE:
        return {**result, "grounded": {"applied": False, "reason": "not_eligible"}}
    if not external_allowed:
        return {**result, "grounded": {"applied": False, "reason": "no_consent"}}
    if settings.is_mock:
        return {**result, "grounded": {"applied": False, "reason": "mock"}}
    if not settings.llm_api_key:
        return {**result, "grounded": {"applied": False, "reason": "no_api_key"}}

    extractive_answer = result.get("answer", "")
    try:
        provider = provider_factory()
        candidate = provider.complete(
            prompt_registry.GROUNDED_SYNTHESIS_PROMPT, _user_prompt(result)
        )
        audit = CitationAuditor.audit_rewrite(extractive_answer, candidate)
        man = prompt_registry.manifest("grounded_synthesis", model=provider.model)
        if audit.faithful:
            return {
                **result,
                "answer": candidate,
                "method": f"{result.get('method', 'extractive')}+grounded_llm",
                "grounded": {"applied": True, "reason": "applied", "manifest": man},
            }
        return {
            **result,
            "grounded": {"applied": False, "reason": "audit_rejected", "manifest": man},
        }
    except Exception:
        # Any provider/parse failure falls back to the deterministic extractive answer.
        return {**result, "grounded": {"applied": False, "reason": "error"}}
