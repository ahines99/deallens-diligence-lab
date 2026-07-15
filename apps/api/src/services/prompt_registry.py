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


# The registered prompts. The memo-polish entry reuses the invariant SYSTEM_PROMPT/PROMPT_VERSION
# defined in ``llm_provider`` so there is a single source of truth for the editor prompt.
_REGISTRY: dict[str, PromptSpec] = {
    "memo_polish": PromptSpec("memo_polish", MEMO_POLISH_VERSION, MEMO_POLISH_PROMPT),
    "grounded_synthesis": PromptSpec(
        "grounded_synthesis", GROUNDED_SYNTHESIS_VERSION, GROUNDED_SYNTHESIS_PROMPT
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
