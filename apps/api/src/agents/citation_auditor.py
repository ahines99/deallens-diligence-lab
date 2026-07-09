"""Citation auditor — verifies that cited refs resolve to real evidence."""
from __future__ import annotations

import re

from src.agents.base import BaseAgent

EV_REF_PATTERN = re.compile(r"EV-\d{3,}")


class CitationAuditor(BaseAgent):
    name = "citation_auditor"
    role = "Verifies that every cited EV-### resolves to a real evidence row (faithfulness guard)."

    @staticmethod
    def extract_refs(text: str) -> set[str]:
        return set(EV_REF_PATTERN.findall(text or ""))

    @staticmethod
    def find_uncited(cited_refs: set[str], known_refs: set[str]) -> set[str]:
        return {r for r in cited_refs if r not in known_refs}
