"""G05 — run and persist LLM-as-judge faithfulness evaluations.

``judge_answer`` scores one (question, answer, context) case with a supplied judge and persists a
:class:`JudgeEvalRun` carrying the judge name and model/prompt provenance. ``quality_summary``
aggregates persisted runs into a faithful-rate / mean-score view grouped by (model, prompt), which
is exactly the per-model / per-prompt quality dashboard the roadmap item calls for.
"""
from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.eval.judge import JudgeVerdict, MockJudge
from src.models.eval_run import JudgeEvalRun

# A judge is any callable (question, answer, context) -> JudgeVerdict.
Judge = Callable[[str, str, str], JudgeVerdict]


def default_judge() -> Judge:
    """The deterministic mock judge used in CI/offline. Live mode injects a LiveJudge explicitly."""
    return MockJudge()


def judge_answer(
    session: Session,
    *,
    question: str,
    answer: str,
    context: str,
    judge: Judge | None = None,
    model_version: str | None = None,
    prompt_version: str | None = None,
    prompt_hash: str | None = None,
    workspace_id: str | None = None,
    created_by: str | None = None,
    commit: bool = True,
) -> JudgeEvalRun:
    """Score one answer for faithfulness and persist the judged run."""
    judge = judge or default_judge()
    verdict = judge(question, answer, context)
    # Prefer an explicit model_version; else fall back to a name the judge advertises.
    resolved_model = model_version or getattr(judge, "model_version", None)
    run = JudgeEvalRun(
        workspace_id=workspace_id,
        question=question,
        answer=answer,
        context=context,
        judge_name=getattr(judge, "name", judge.__class__.__name__),
        model_version=resolved_model,
        prompt_version=prompt_version,
        prompt_hash=prompt_hash,
        faithful=verdict.faithful,
        score=verdict.score,
        reason=verdict.reason,
        details={
            "unsupported_numbers": list(verdict.unsupported_numbers),
            "unsupported_citations": list(verdict.unsupported_citations),
        },
        created_by=created_by,
    )
    session.add(run)
    if commit:
        session.commit()
    else:
        session.flush()
    return run


def quality_summary(session: Session, workspace_id: str | None = None) -> dict:
    """Faithful-rate and mean-score grouped by (model_version, prompt_version).

    Shape: ``{"total": int, "faithful": int, "faithful_rate": float, "groups": [
    {"model_version", "prompt_version", "count", "faithful", "faithful_rate", "mean_score"}, ...]}``.
    Groups are sorted by (model_version, prompt_version) for a stable dashboard ordering.
    """
    stmt = select(JudgeEvalRun)
    if workspace_id is not None:
        stmt = stmt.where(JudgeEvalRun.workspace_id == workspace_id)
    runs = list(session.scalars(stmt))

    buckets: dict[tuple[str | None, str | None], list[JudgeEvalRun]] = {}
    for run in runs:
        buckets.setdefault((run.model_version, run.prompt_version), []).append(run)

    groups = []
    for (model_version, prompt_version), rows in sorted(
        buckets.items(), key=lambda kv: (kv[0][0] or "", kv[0][1] or "")
    ):
        count = len(rows)
        faithful = sum(1 for r in rows if r.faithful)
        groups.append(
            {
                "model_version": model_version,
                "prompt_version": prompt_version,
                "count": count,
                "faithful": faithful,
                "faithful_rate": round(faithful / count, 4) if count else 0.0,
                "mean_score": round(sum(r.score for r in rows) / count, 4) if count else 0.0,
            }
        )

    total = len(runs)
    total_faithful = sum(1 for r in runs if r.faithful)
    return {
        "total": total,
        "faithful": total_faithful,
        "faithful_rate": round(total_faithful / total, 4) if total else 0.0,
        "groups": groups,
    }
