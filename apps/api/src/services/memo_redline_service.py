"""G47 — Memo redlines: side-by-side diff of two analysis runs with changed-claim highlighting.

Each ``AnalysisRun`` seals a memo/artifact.  The memo text is retained per run on the
``ArtifactVersion`` linked by ``analysis_run_id`` (``content_json['ic_memo_markdown']`` for the
deterministic diligence pack, falling back to ``content_text`` and finally the run's
``output_summary``).  This service diffs that sealed content between two runs at *claim* (sentence)
granularity: sentences added, removed, and — crucially — *changed*, where a claim's surrounding
text is stable but a NUMBER moved.  Numeric changes are detected with the citation auditor's
``extract_numeric_tokens`` so a "$120 million" → "$135 million" edit is flagged specifically as a
changed numeric claim rather than an unrelated add/remove.
"""
from __future__ import annotations

import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.agents.citation_auditor import NUMERIC_PATTERN, CitationAuditor
from src.models.underwriting_data import AnalysisRun, ArtifactVersion
from src.services.common import NotFound, get_workspace_or_404

# Ordered memo keys probed on a run's linked artifact / output summary.
_MEMO_KEYS = ("ic_memo_markdown", "ic_memo", "memo_markdown", "memo")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


class MemoRedlineError(ValueError):
    """A user-correctable memo-redline request error (mapped to HTTP 4xx)."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _run(session: Session, workspace_id: str, run_id: str) -> AnalysisRun:
    run = session.get(AnalysisRun, run_id)
    if run is None or run.workspace_id != workspace_id:
        raise NotFound(f"Analysis run '{run_id}' not found in workspace '{workspace_id}'")
    return run


def _memo_text(session: Session, run: AnalysisRun) -> tuple[str, str]:
    """Return ``(text, granularity)`` for a run's sealed memo content."""
    artifact = session.scalar(
        select(ArtifactVersion)
        .where(ArtifactVersion.analysis_run_id == run.id)
        .order_by(ArtifactVersion.version.desc())
    )
    if artifact is not None:
        content = artifact.content_json
        if isinstance(content, dict):
            for key in _MEMO_KEYS:
                value = content.get(key)
                if isinstance(value, str) and value.strip():
                    return value, f"artifact.content_json.{key}"
        if artifact.content_text and artifact.content_text.strip():
            return artifact.content_text, "artifact.content_text"
    summary = run.output_summary or {}
    for key in _MEMO_KEYS:
        value = summary.get(key)
        if isinstance(value, str) and value.strip():
            return value, f"output_summary.{key}"
    # No memo text is retained per run; diff the canonical output summary and say so explicitly.
    return json.dumps(summary, sort_keys=True, ensure_ascii=False), "output_summary_json"


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCE_SPLIT.split(text or "") if part.strip()]


def _skeleton(sentence: str) -> str:
    """A sentence with every numeric token masked, so number-only edits share a skeleton."""
    masked = NUMERIC_PATTERN.sub("#", sentence)
    return re.sub(r"\s+", " ", masked).strip().casefold()


def diff_runs(
    session: Session, workspace_id: str, run_a_id: str, run_b_id: str
) -> dict:
    """Diff the sealed memo content of two runs into added / removed / changed claims.

    ``changed`` pairs a before/after sentence sharing a numeric-masked skeleton; each carries
    ``numeric_change`` plus the exact ``numbers_added`` / ``numbers_removed`` tokens. Identical
    memos yield an empty diff.
    """
    get_workspace_or_404(session, workspace_id)
    if run_a_id == run_b_id:
        raise MemoRedlineError("Provide two distinct analysis runs to compare")
    run_a = _run(session, workspace_id, run_a_id)
    run_b = _run(session, workspace_id, run_b_id)
    text_a, granularity_a = _memo_text(session, run_a)
    text_b, granularity_b = _memo_text(session, run_b)

    sentences_a = _sentences(text_a)
    sentences_b = _sentences(text_b)
    set_a, set_b = set(sentences_a), set(sentences_b)
    only_a = [sentence for sentence in sentences_a if sentence not in set_b]
    only_b = [sentence for sentence in sentences_b if sentence not in set_a]

    skeleton_a: dict[str, list[str]] = {}
    for sentence in only_a:
        skeleton_a.setdefault(_skeleton(sentence), []).append(sentence)
    skeleton_b: dict[str, list[str]] = {}
    for sentence in only_b:
        skeleton_b.setdefault(_skeleton(sentence), []).append(sentence)

    changed: list[dict] = []
    matched_a: set[str] = set()
    matched_b: set[str] = set()
    for skeleton, before_list in skeleton_a.items():
        after_list = skeleton_b.get(skeleton)
        if not after_list:
            continue
        for before, after in zip(before_list, after_list):
            before_numbers = CitationAuditor.extract_numeric_tokens(before)
            after_numbers = CitationAuditor.extract_numeric_tokens(after)
            numbers_added = sorted((after_numbers - before_numbers).elements())
            numbers_removed = sorted((before_numbers - after_numbers).elements())
            changed.append(
                {
                    "before": before,
                    "after": after,
                    "numeric_change": bool(numbers_added or numbers_removed),
                    "numbers_added": numbers_added,
                    "numbers_removed": numbers_removed,
                }
            )
            matched_a.add(before)
            matched_b.add(after)

    added = [sentence for sentence in only_b if sentence not in matched_b]
    removed = [sentence for sentence in only_a if sentence not in matched_a]
    numeric_changes = [item for item in changed if item["numeric_change"]]

    return {
        "workspace_id": workspace_id,
        "run_a": {
            "id": run_a.id,
            "version": run_a.version,
            "run_type": run_a.run_type,
            "granularity": granularity_a,
        },
        "run_b": {
            "id": run_b.id,
            "version": run_b.version,
            "run_type": run_b.run_type,
            "granularity": granularity_b,
        },
        "granularity": granularity_b,
        "changed": changed,
        "added": added,
        "removed": removed,
        "numeric_changes": numeric_changes,
        "counts": {
            "changed": len(changed),
            "added": len(added),
            "removed": len(removed),
            "numeric_changes": len(numeric_changes),
        },
        "is_empty": not (changed or added or removed),
    }


__all__ = ["MemoRedlineError", "diff_runs"]
