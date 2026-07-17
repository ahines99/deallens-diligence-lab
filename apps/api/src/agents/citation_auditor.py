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
# Spelled-out cardinalities. A rewrite inventing "one in seven revenue dollars" or "top five
# customers" is numeric drift exactly like digit drift, so the rewrite gate normalizes these
# into the digit token space. Deliberately conservative: an innocuous added "one" fails closed
# to the deterministic text, which is the safe direction for a faithfulness guard.
NUMBER_WORD_PATTERN = re.compile(
    r"\b(zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen"
    r"|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty"
    r"|sixty|seventy|eighty|ninety|hundred|thousand|dozen|million|billion|trillion)s?\b",
    re.IGNORECASE,
)
_NUMBER_WORD_VALUES = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11",
    "twelve": "12", "thirteen": "13", "fourteen": "14", "fifteen": "15", "sixteen": "16",
    "seventeen": "17", "eighteen": "18", "nineteen": "19", "twenty": "20", "thirty": "30",
    "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70", "eighty": "80",
    "ninety": "90", "hundred": "100", "thousand": "1000", "dozen": "12",
    "million": "million", "billion": "billion", "trillion": "trillion",
}


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
    def extract_quantity_tokens(cls, text: str) -> Counter[str]:
        """Digit tokens plus spelled-out cardinalities normalized into the same token space.

        Used by the rewrite gate: without this, only digit-form drift was caught and an LLM
        re-voicing could invent quantities in word form without tripping the audit.
        """
        tokens = cls.extract_numeric_tokens(text)
        without_refs = EV_REF_PATTERN.sub("", text or "")
        for match in NUMBER_WORD_PATTERN.finditer(without_refs):
            tokens[_NUMBER_WORD_VALUES[match.group(1).casefold()]] += 1
        return tokens

    @classmethod
    def citation_numeric_context(cls, text: str) -> list[tuple[str, tuple[str, ...]]]:
        """Bind each citation occurrence to quantity tokens in its sentence/line."""
        contexts: list[tuple[str, tuple[str, ...]]] = []
        for segment in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
            numbers = tuple(sorted(cls.extract_quantity_tokens(segment).elements()))
            contexts.extend((ref, numbers) for ref in cls.extract_ref_sequence(segment))
        return contexts

    @classmethod
    def audit_rewrite(cls, source: str, candidate: str) -> FaithfulnessAudit:
        """Fail closed when a rewrite changes citation order/count or any quantity token."""
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
        source_numbers = cls.extract_quantity_tokens(source)
        candidate_numbers = cls.extract_quantity_tokens(candidate)
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
