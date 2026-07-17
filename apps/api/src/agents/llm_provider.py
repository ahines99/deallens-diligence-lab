"""Optional live LLM provider (Anthropic Messages API by default).

Only used when LLM_MODE=live and LLM_API_KEY is set. Two call shapes:

* ``polish_markdown`` — re-voices deterministic, already-grounded narrative; never invents
  numbers or citations; any failure falls back to the deterministic text.
* ``structured_llm`` (G51) — a schema-constrained JSON call for the Wave 5 LLM-first paths
  (risk extraction, claim extraction). It shares the exact consent/mock/no-key gating, binds a
  registry-hashed prompt manifest, and FAILS CLOSED: malformed JSON, schema mismatch, or any
  provider error returns ``data=None`` with a machine-readable reason so callers fall back to
  their deterministic path. Verification of the *content* (verbatim quotes, locators) is the
  caller's job — this layer only guarantees shape and provenance.

The default demo path is fully deterministic.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

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

    def complete_with_tools(self, system: str, messages: list[dict], tools: list[dict]) -> dict:
        """One tool-use turn (G57): returns ``{"stop_reason", "content"}`` in Anthropic block form.

        The agent loop appends tool_result blocks and calls again. Tool use is Anthropic-format
        only; a non-Anthropic base URL raises so the agent service can fail closed with an
        explicit reason instead of mistranslating a different provider's tool protocol.
        """
        if not self.is_anthropic:
            raise RuntimeError(
                "The diligence agent's tool loop requires an Anthropic-format endpoint"
            )
        headers = {
            "x-api-key": settings.llm_api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": self.model,
            "max_tokens": 4096,
            "system": system,
            "messages": messages,
            "tools": tools,
        }
        with httpx.Client(timeout=180) as client:
            resp = client.post(f"{self.base_url}/messages", headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return {
                "stop_reason": data.get("stop_reason"),
                "content": data.get("content", []),
            }


SchemaT = TypeVar("SchemaT", bound=BaseModel)

# LLMs often wrap JSON in code fences or preamble; extract the outermost object non-greedily.
_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(frozen=True)
class StructuredOutcome:
    """Result of a schema-constrained LLM call (G51), with honest provenance.

    ``data`` is a validated schema instance only when a live call returned parseable,
    schema-conforming JSON; every other path (no consent, mock, no key, malformed JSON, schema
    mismatch, provider error) carries ``data=None`` and a machine-readable ``reason`` so callers
    fall back to their deterministic engine and can record why.
    """

    data: BaseModel | None
    applied: bool
    reason: str
    manifest: dict[str, str] | None = None


def structured_llm(
    prompt_id: str,
    user_prompt: str,
    schema: type[SchemaT],
    *,
    external_allowed: bool = False,
    provider_factory=None,
) -> StructuredOutcome:
    """Run a registry-versioned prompt expecting a ``schema``-shaped JSON object, failing closed.

    ``prompt_id`` must be registered in ``prompt_registry`` (raises ``UnknownPrompt`` otherwise —
    a programmer error, not a runtime condition). ``provider_factory`` exists for tests.
    """
    # Lazy import avoids a module-load cycle (prompt_registry imports this module).
    from src.services import prompt_registry

    spec = prompt_registry.get(prompt_id)
    if not external_allowed:
        return StructuredOutcome(None, False, "no_consent")
    if settings.is_mock:
        return StructuredOutcome(None, False, "mock")
    if not settings.llm_api_key:
        return StructuredOutcome(None, False, "no_api_key")
    try:
        provider = (provider_factory or LiveProvider)()
        raw = provider.complete(spec.template, user_prompt)
        manifest = prompt_registry.manifest(prompt_id, model=provider.model)
    except Exception:
        return StructuredOutcome(None, False, "error")
    match = _JSON_OBJECT.search(raw or "")
    if match is None:
        return StructuredOutcome(None, False, "parse_error", manifest)
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return StructuredOutcome(None, False, "parse_error", manifest)
    try:
        data = schema.model_validate(payload)
    except ValidationError:
        return StructuredOutcome(None, False, "schema_mismatch", manifest)
    return StructuredOutcome(data, True, "applied", manifest)


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
