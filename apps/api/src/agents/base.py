"""Agent base class and the (optional) live LLM provider hook.

Agents assemble diligence artifacts deterministically from real SEC data. When LLM_MODE=live,
they may optionally re-voice narrative via LiveProvider — the numbers never change.
Agents are named so every generated artifact records which "analyst" produced it.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    def complete(self, system: str, user: str) -> str: ...


def get_provider() -> LLMProvider:
    """Return the live LLM provider. Only used when LLM_MODE=live (optional prose polish)."""
    from src.agents.llm_provider import LiveProvider

    return LiveProvider()


class BaseAgent:
    """Base for the named diligence agents.

    Agents assemble artifacts deterministically from real data. The LLM provider is lazy and only
    built if a live-mode agent actually accesses it (optional narrative polish).
    """

    name: str = "agent"
    role: str = ""

    def __init__(self, provider: LLMProvider | None = None) -> None:
        self._provider = provider

    @property
    def provider(self) -> LLMProvider:
        if self._provider is None:
            self._provider = get_provider()
        return self._provider
