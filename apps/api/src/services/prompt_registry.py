"""G10 — Prompt & model-config registry.

A versioned, hashed manifest for every prompt that can touch an LLM-generated artifact. Each
registered prompt carries a stable ``prompt_id``, a human ``prompt_version`` label, and the exact
``template`` text; its ``prompt_hash`` is the SHA-256 of that text. ``manifest(prompt_id)`` binds
those together with the configured model so a sealed ``AnalysisRun`` (and grounded QA runs) can
record precisely which prompt produced their prose — making LLM ops reproducible and tamper-evident.

The registry is pure and offline: it needs no network and no LLM. Changing a template's text
changes its hash, which is exactly the tamper signal the acceptance tests assert.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from src.agents.llm_provider import PROMPT_VERSION as MEMO_POLISH_VERSION
from src.agents.llm_provider import SYSTEM_PROMPT as MEMO_POLISH_PROMPT
from src.config import settings

# G04 grounded-synthesis prompt: re-voice retrieved extracts for fluency without adding any fact,
# number, or citation. Kept here (not inline in the QA path) so it is versioned and hashed like
# every other LLM-touched prompt. Bump the version whenever this text changes.
GROUNDED_SYNTHESIS_VERSION = "grounded-synth-v1"
GROUNDED_SYNTHESIS_PROMPT = (
    "You are an investment diligence writer. You are given verbatim extracts quoted from a "
    "company's SEC filings. Rewrite them into a single fluent, well-organized answer. You may "
    "reorder and connect sentences for readability, but you MUST NOT introduce any fact, figure, "
    "percentage, date, or citation that is not already present in the extracts, and you MUST NOT "
    "drop or alter any number that is present. Do not add commentary, speculation, or investment "
    "advice. Preserve every numeric value exactly as written."
)


@dataclass(frozen=True)
class PromptSpec:
    """One registered prompt: id, version label, and the exact template that gets hashed."""

    prompt_id: str
    prompt_version: str
    template: str

    @property
    def prompt_hash(self) -> str:
        return hashlib.sha256(self.template.encode("utf-8")).hexdigest()


# G52 — LLM-first risk extraction. The model proposes findings with exact supporting quotes; a
# deterministic verifier accepts a finding ONLY if its quote appears verbatim in the source chunk,
# so nothing the model writes enters the governed record unverified.
RISK_EXTRACTION_VERSION = "risk-extract-v1"
RISK_EXTRACTION_PROMPT = (
    "You are a private-equity diligence analyst reading excerpts of a company's SEC filing. "
    "Identify concrete risk findings. Respond with ONLY a JSON object of the form "
    '{"findings": [{"category": "<one of the allowed category slugs>", '
    '"title": "<short headline>", "finding": "<one-paragraph analyst summary>", '
    '"severity_score": <integer 1-10>, "quote": "<EXACT verbatim sentence(s) copied from the '
    'excerpt that support the finding>", "chunk_index": <integer index of the excerpt the quote '
    "came from>}]}. The quote MUST be copied character-for-character from one excerpt — do not "
    "paraphrase, correct, abbreviate, or merge text. Only report risks the excerpts explicitly "
    "support; if the excerpts support none, return {\"findings\": []}. Never speculate and never "
    "present output as investment advice."
)

# G53 — schema-constrained claim extraction from data-room documents. Same verbatim-quote
# discipline: the claimed value must appear inside the quoted span or the claim is rejected.
CLAIM_EXTRACTION_VERSION = "claim-extract-v1"
CLAIM_EXTRACTION_PROMPT = (
    "You are extracting structured, checkable claims from excerpts of a private deal document. "
    "Respond with ONLY a JSON object of the form "
    '{"claims": [{"category": "<one of the allowed category slugs>", '
    '"field_name": "<snake_case field>", "value_text": "<the claimed value as written>", '
    '"value_number": <number or null>, "unit": "<unit or null>", "period": "<period or null>", '
    '"quote": "<EXACT verbatim sentence(s) copied from the excerpt containing the value>", '
    '"chunk_index": <integer index of the excerpt>}]}. '
    "The quote MUST be copied character-for-character from one excerpt and MUST contain the "
    "claimed value. Extract only what the text states explicitly; return {\"claims\": []} when "
    "nothing qualifies. Never estimate or infer values."
)

# G54 — cross-corpus grounded synthesis: like G04's prompt, plus provenance discipline — the
# rewrite must keep public-filing and confidential-data-room content attributable.
CROSS_CORPUS_SYNTHESIS_VERSION = "cross-corpus-synth-v1"
CROSS_CORPUS_SYNTHESIS_PROMPT = (
    "You are an investment diligence writer. You are given verbatim extracts, each labeled either "
    "[PUBLIC] (from SEC filings) or [CONFIDENTIAL] (from a private deal data room). Rewrite them "
    "into a single fluent, well-organized answer. You may reorder and connect sentences for "
    "readability, but you MUST NOT introduce any fact, figure, percentage, date, or citation that "
    "is not already present in the extracts, and you MUST NOT drop or alter any number. Never "
    "attribute confidential information to a public source or vice versa. Do not add commentary, "
    "speculation, or investment advice."
)


# The registered prompts. The memo-polish entry reuses the invariant SYSTEM_PROMPT/PROMPT_VERSION
# defined in ``llm_provider`` so there is a single source of truth for the editor prompt.
_REGISTRY: dict[str, PromptSpec] = {
    "memo_polish": PromptSpec("memo_polish", MEMO_POLISH_VERSION, MEMO_POLISH_PROMPT),
    "grounded_synthesis": PromptSpec(
        "grounded_synthesis", GROUNDED_SYNTHESIS_VERSION, GROUNDED_SYNTHESIS_PROMPT
    ),
    "risk_extraction": PromptSpec(
        "risk_extraction", RISK_EXTRACTION_VERSION, RISK_EXTRACTION_PROMPT
    ),
    "claim_extraction": PromptSpec(
        "claim_extraction", CLAIM_EXTRACTION_VERSION, CLAIM_EXTRACTION_PROMPT
    ),
    "cross_corpus_synthesis": PromptSpec(
        "cross_corpus_synthesis", CROSS_CORPUS_SYNTHESIS_VERSION, CROSS_CORPUS_SYNTHESIS_PROMPT
    ),
}


class UnknownPrompt(KeyError):
    """Raised when a caller asks for a prompt id that is not registered."""


def get(prompt_id: str) -> PromptSpec:
    try:
        return _REGISTRY[prompt_id]
    except KeyError as exc:  # pragma: no cover - defensive
        raise UnknownPrompt(f"unknown prompt id: {prompt_id!r}") from exc


def prompt_ids() -> list[str]:
    return sorted(_REGISTRY)


def manifest(prompt_id: str, *, model: str | None = None) -> dict[str, str]:
    """Reproducible manifest bound into an LLM-touched run.

    ``model`` defaults to the configured model so a run records the exact (prompt, model) pair that
    produced it. The returned hash is deterministic for a given template text.
    """
    spec = get(prompt_id)
    return {
        "prompt_id": spec.prompt_id,
        "prompt_version": spec.prompt_version,
        "prompt_hash": spec.prompt_hash,
        "model": model or settings.llm_model,
    }


def all_manifests(*, model: str | None = None) -> list[dict[str, str]]:
    """Manifests for every registered prompt (registry view / endpoint payload)."""
    return [manifest(pid, model=model) for pid in prompt_ids()]
