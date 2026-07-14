"""Citation auditor — verifies that cited refs resolve to real evidence."""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from src.agents.base import BaseAgent

EV_REF_PATTERN = re.compile(r"EV-\d{3,}")
NUMERIC_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(?:FY\s*)?(?:[$€£]\s*)?-?\d[\d,]*(?:\.\d+)?"
    r"(?:\s*(?:%|x|bps|basis\s+points|thousand|million|billion|k|m|mm|bn))?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class FaithfulnessAudit:
    faithful: bool
    citation_sequence_changed: bool
    citation_numeric_context_changed: bool
    numeric_tokens_added: tuple[str, ...]
    numeric_tokens_removed: tuple[str, ...]
    reason: str | None = None


class CitationAuditor(BaseAgent):
    name = "citation_auditor"
    role = "Verifies that every cited EV-### resolves to a real evidence row (faithfulness guard)."

    @staticmethod
    def extract_refs(text: str) -> set[str]:
        return set(EV_REF_PATTERN.findall(text or ""))

    @staticmethod
    def find_uncited(cited_refs: set[str], known_refs: set[str]) -> set[str]:
        return {r for r in cited_refs if r not in known_refs}

    @staticmethod
    def extract_ref_sequence(text: str) -> list[str]:
        return EV_REF_PATTERN.findall(text or "")

    @staticmethod
    def extract_numeric_tokens(text: str) -> Counter[str]:
        # Citation identifiers are provenance, not memo economics; audit them separately.
        without_refs = EV_REF_PATTERN.sub("", text or "")
        values = (
            re.sub(r"\s+", "", match.group(0).casefold()).replace(",", "")
            for match in NUMERIC_PATTERN.finditer(without_refs)
        )
        return Counter(values)

    @classmethod
    def citation_numeric_context(cls, text: str) -> list[tuple[str, tuple[str, ...]]]:
        """Bind each citation occurrence to numeric tokens in its sentence/line."""
        contexts: list[tuple[str, tuple[str, ...]]] = []
        for segment in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
            numbers = tuple(sorted(cls.extract_numeric_tokens(segment).elements()))
            contexts.extend((ref, numbers) for ref in cls.extract_ref_sequence(segment))
        return contexts

    @classmethod
    def audit_rewrite(cls, source: str, candidate: str) -> FaithfulnessAudit:
        """Fail closed when a rewrite changes citation order/count or any numeric token."""
        if not candidate or not candidate.strip():
            return FaithfulnessAudit(
                faithful=False,
                citation_sequence_changed=False,
                citation_numeric_context_changed=False,
                numeric_tokens_added=(),
                numeric_tokens_removed=(),
                reason="empty candidate",
            )
        source_refs = cls.extract_ref_sequence(source)
        candidate_refs = cls.extract_ref_sequence(candidate)
        citation_changed = source_refs != candidate_refs
        citation_context_changed = (
            cls.citation_numeric_context(source) != cls.citation_numeric_context(candidate)
        )
        source_numbers = cls.extract_numeric_tokens(source)
        candidate_numbers = cls.extract_numeric_tokens(candidate)
        added = tuple(sorted((candidate_numbers - source_numbers).elements()))
        removed = tuple(sorted((source_numbers - candidate_numbers).elements()))
        faithful = not citation_changed and not citation_context_changed and not added and not removed
        reason = None
        if citation_changed:
            reason = "citation sequence or count changed"
        elif citation_context_changed:
            reason = "numeric content moved between citations"
        elif added or removed:
            reason = "numeric content changed"
        return FaithfulnessAudit(
            faithful=faithful,
            citation_sequence_changed=citation_changed,
            citation_numeric_context_changed=citation_context_changed,
            numeric_tokens_added=added,
            numeric_tokens_removed=removed,
            reason=reason,
        )
