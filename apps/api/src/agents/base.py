"""Agent base class and the LLM provider protocol.

Agents assemble diligence artifacts deterministically from real SEC data. The ONLY sanctioned
path to the external LLM is ``llm_provider.polish_markdown``, which enforces per-workspace consent
(``external_llm_allowed`` and non-``restricted`` classification) and fails closed on citation or
numeric drift. There is deliberately no unguarded provider hook on the agents: adding one would let
a future agent reach the LLM without the consent check, silently breaking the determinism invariant.
Agents are named so every generated artifact records which "analyst" produced it.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMProvider(Protocol):
    """Structural type for a live LLM backend (see ``llm_provider.LiveProvider``)."""

    name: str

    def complete(self, system: str, user: str) -> str: ...


class BaseAgent:
    """Base for the named, deterministic diligence agents.

    Agents assemble artifacts purely from real data. They intentionally hold no reference to an
    LLM provider — narrative polish happens only in the consent-gated ``polish_markdown`` boundary.
    """

    name: str = "agent"
    role: str = ""
