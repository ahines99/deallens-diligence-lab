"""G05 — LLM-as-judge faithfulness scoring.

A judge is a callable ``(question, answer, context) -> JudgeVerdict`` that scores whether an
answer is faithful to its supporting context. Two implementations ship:

* :class:`MockJudge` — deterministic and offline, for CI. It reuses the citation-auditor tokenizers
  to check that every number and every ``[EV-###]`` citation in the answer actually appears in the
  context. An answer that asserts a figure the context never states is unfaithful. This is the same
  fail-closed logic that gates grounded synthesis, applied as a scorer instead of a gate.
* :class:`LiveJudge` — wraps the live provider for real LLM grading in ``live`` mode. It is never
  exercised in CI (no API key); tests use the mock judge.

Both return the identical :class:`JudgeVerdict` shape so persistence and quality views are
judge-agnostic.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from src.agents.citation_auditor import CitationAuditor


@dataclass(frozen=True)
class JudgeVerdict:
    faithful: bool
    score: float
    reason: str
    unsupported_numbers: tuple[str, ...] = ()
    unsupported_citations: tuple[str, ...] = ()


class MockJudge:
    """Deterministic faithfulness judge: every answer number/citation must be in the context."""

    name = "mock-faithfulness-v1"
    model_version = "mock-judge"

    def __call__(self, question: str, answer: str, context: str) -> JudgeVerdict:
        del question  # the mock judge grades grounding, not relevance
        answer_numbers = CitationAuditor.extract_numeric_tokens(answer)
        context_numbers = CitationAuditor.extract_numeric_tokens(context)
        unsupported_numbers = tuple(sorted((answer_numbers - context_numbers).elements()))

        answer_refs = set(CitationAuditor.extract_ref_sequence(answer))
        context_refs = set(CitationAuditor.extract_ref_sequence(context))
        unsupported_citations = tuple(sorted(answer_refs - context_refs))

        total_claims = sum(answer_numbers.values()) + len(answer_refs)
        unsupported_count = len(unsupported_numbers) + len(unsupported_citations)
        # An answer with no checkable claims is trivially grounded (score 1.0); otherwise the score
        # is the supported fraction of its numeric/citation claims.
        if total_claims == 0:
            score = 1.0
        else:
            score = round((total_claims - unsupported_count) / total_claims, 4)

        faithful = unsupported_count == 0
        if faithful:
            reason = "every number and citation in the answer is supported by the context"
        else:
            parts = []
            if unsupported_numbers:
                parts.append(f"unsupported numbers {list(unsupported_numbers)}")
            if unsupported_citations:
                parts.append(f"unsupported citations {list(unsupported_citations)}")
            reason = "answer asserts claims absent from the context: " + "; ".join(parts)
        return JudgeVerdict(
            faithful=faithful,
            score=score,
            reason=reason,
            unsupported_numbers=unsupported_numbers,
            unsupported_citations=unsupported_citations,
        )


class LiveJudge:  # pragma: no cover - requires a live provider/network, never run in CI
    """LLM faithfulness judge for ``live`` mode. Falls back to a conservative unfaithful verdict
    if the model response cannot be parsed, so an unparseable grade never reads as a pass."""

    name = "live-faithfulness-v1"

    SYSTEM_PROMPT = (
        "You are a strict faithfulness grader for an investment-diligence Q&A system. Given a "
        "QUESTION, an ANSWER, and the CONTEXT the answer must be grounded in, decide whether every "
        "factual claim and number in the answer is supported by the context. Respond ONLY with a "
        'JSON object: {"faithful": true|false, "score": 0.0-1.0, "reason": "..."}.'
    )

    def __init__(self, provider=None) -> None:
        if provider is None:
            from src.agents.llm_provider import LiveProvider

            provider = LiveProvider()
        self.provider = provider
        self.model_version = getattr(provider, "model", "external-llm")

    def __call__(self, question: str, answer: str, context: str) -> JudgeVerdict:
        user = f"QUESTION:\n{question}\n\nANSWER:\n{answer}\n\nCONTEXT:\n{context}"
        try:
            raw = self.provider.complete(self.SYSTEM_PROMPT, user)
            data = json.loads(raw)
            return JudgeVerdict(
                faithful=bool(data["faithful"]),
                score=float(data.get("score", 0.0)),
                reason=str(data.get("reason", "")),
            )
        except Exception as exc:
            return JudgeVerdict(
                faithful=False,
                score=0.0,
                reason=f"judge response could not be parsed ({exc}); failing closed",
            )
