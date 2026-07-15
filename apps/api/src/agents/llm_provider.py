"""Optional live LLM provider (Anthropic Messages API by default).

Only used when LLM_MODE=live and LLM_API_KEY is set. It re-voices deterministic, already-grounded
narrative — it never invents numbers or citations. Any failure falls back to the deterministic text.
This is a wired extension point; the default demo path is fully deterministic.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from src.agents.citation_auditor import CitationAuditor
from src.config import settings

SYSTEM_PROMPT = (
    "You are an investment diligence editor. You will be given a source-grounded draft memo with "
    "bracketed evidence citations like [EV-001]. Improve clarity and flow ONLY. Do NOT change any "
    "number, fact, or citation, do NOT add claims, and keep every [EV-###] tag exactly where it is. "
    "Never present the output as investment advice."
)
# Bump when SYSTEM_PROMPT changes so sealed runs record which prompt produced their prose.
PROMPT_VERSION = "ic-editor-v1"


@dataclass(frozen=True)
class PolishOutcome:
    """Result of an attempted LLM re-voice, carrying honest provenance for the sealed run.

    ``applied`` is True only when a live rewrite passed the citation auditor and was used;
    every other path (mock, no consent, no key, audit rejection, error) returns the original
    deterministic text with ``applied=False`` and a machine-readable ``reason``.
    """

    text: str
    applied: bool
    reason: str
    model: str | None = None
    prompt_version: str | None = None
    # G10: the content hash of the exact prompt template used, so a sealed run is reproducible
    # and tamper-evident. Set only when a live rewrite is actually applied.
    prompt_hash: str | None = None


class LiveProvider:
    name = "live"

    def __init__(self) -> None:
        if not settings.llm_api_key:
            raise RuntimeError(
                "LLM_MODE=live requires LLM_API_KEY. Unset it to use the deterministic default."
            )
        self.base_url = settings.llm_base_url.rstrip("/")
        self.model = settings.llm_model
        self.is_anthropic = "anthropic" in self.base_url

    def complete(self, system: str, user: str) -> str:
        if self.is_anthropic:
            headers = {
                "x-api-key": settings.llm_api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
            payload = {
                "model": self.model,
                "max_tokens": 4096,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            }
            with httpx.Client(timeout=120) as client:
                resp = client.post(f"{self.base_url}/messages", headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                return "".join(block.get("text", "") for block in data.get("content", []))
        headers = {"Authorization": f"Bearer {settings.llm_api_key}", "content-type": "application/json"}
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        with httpx.Client(timeout=120) as client:
            resp = client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]


def polish_markdown(markdown: str, *, external_allowed: bool = False) -> PolishOutcome:
    """Re-voice a grounded draft only with workspace consent, failing closed on drift.

    Returns a :class:`PolishOutcome` so callers can record whether the external LLM actually
    touched the artifact — the sealed ``AnalysisRun`` must never claim determinism when it did.
    """
    if not external_allowed:
        return PolishOutcome(markdown, False, "no_consent")
    if settings.is_mock:
        return PolishOutcome(markdown, False, "mock")
    if not settings.llm_api_key:
        return PolishOutcome(markdown, False, "no_api_key")
    try:
        provider = LiveProvider()
        candidate = provider.complete(SYSTEM_PROMPT, markdown)
        audit = CitationAuditor.audit_rewrite(markdown, candidate)
        if audit.faithful:
            # Lazy import avoids a module-load cycle (prompt_registry imports this module).
            from src.services import prompt_registry

            prompt_hash = prompt_registry.get("memo_polish").prompt_hash
            return PolishOutcome(
                candidate, True, "applied", provider.model, PROMPT_VERSION, prompt_hash
            )
        return PolishOutcome(markdown, False, "audit_rejected", provider.model, PROMPT_VERSION)
    except Exception:
        return PolishOutcome(markdown, False, "error")
