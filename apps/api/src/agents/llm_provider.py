"""Optional live LLM provider (Anthropic Messages API by default).

Only used when LLM_MODE=live and LLM_API_KEY is set. It re-voices deterministic, already-grounded
narrative — it never invents numbers or citations. Any failure falls back to the deterministic text.
This is a wired extension point; the default demo path is fully deterministic.
"""
from __future__ import annotations

import httpx

from src.agents.citation_auditor import CitationAuditor
from src.config import settings

SYSTEM_PROMPT = (
    "You are an investment diligence editor. You will be given a source-grounded draft memo with "
    "bracketed evidence citations like [EV-001]. Improve clarity and flow ONLY. Do NOT change any "
    "number, fact, or citation, do NOT add claims, and keep every [EV-###] tag exactly where it is. "
    "Never present the output as investment advice."
)


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


def polish_markdown(markdown: str, *, external_allowed: bool = False) -> str:
    """Re-voice a grounded draft only with workspace consent, failing closed on drift."""
    if not external_allowed or settings.is_mock or not settings.llm_api_key:
        return markdown
    try:
        candidate = LiveProvider().complete(SYSTEM_PROMPT, markdown)
        audit = CitationAuditor.audit_rewrite(markdown, candidate)
        return candidate if audit.faithful else markdown
    except Exception:
        return markdown
