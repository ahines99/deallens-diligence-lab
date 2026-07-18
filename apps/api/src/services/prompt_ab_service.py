"""G81 — prompt A/B evaluation over the committed golden set, judged for faithfulness.

``run_ab`` pits the REGISTERED template for a ``prompt_id`` (side A, from ``prompt_registry``)
against a candidate template (side B). Both sides answer the same golden-set questions
(``src/eval/fixtures/golden_set.json``) through the provider seam, each answer is scored by the
existing G05 faithfulness judge against that question's golden context, and the report carries a
side-by-side faithful rate with a declared winner. The judged subset is documented and bounded:
the answerable golden questions (``should_answer`` with at least one relevant chunk), in fixture
order, capped at ``_MAX_CASES`` — so a live A/B run costs at most ``2 * _MAX_CASES`` provider
calls. The judge is ``judge_service.default_judge()`` (the deterministic mock judge — offline
and CI-safe); verdicts are scored directly and never persisted as ``JudgeEvalRun`` rows, so an
A/B run cannot pollute the workspace judge-eval quality view.

Persistence needs no migration: reports land in the blob store
(``model-ops/prompt-ab/{prompt_id}.json``) as ``{"history": [...]}`` — newest first, capped at
``_HISTORY_CAP`` — and ``latest_reports`` reads the newest report per registered prompt for the
``/quality`` dashboard (missing or unreadable blobs are skipped, never fabricated).

PROMOTION CONVENTION (the G81 eval gate): a registered prompt template may only be changed in a
PR that includes a winning A/B report for that ``prompt_id`` — the report is the eval artifact
justifying the promotion, and the registry's ``prompt_hash`` change is the tamper signal that a
template changed. A template edit without an accompanying report is a review-rejectable change;
the candidate hash in the report (``b.prompt_hash_candidate``) must match the newly registered
template's hash, which is what makes the pairing checkable.

Gating matches every other LLM path and fails closed: mock mode, a missing API key, a provider
error, or missing consent all return an honest ``{"status": "not_run", "reason": ...}`` and
persist nothing — CI never reaches a provider. Consent is two-track: a workspace-bound run
inherits that workspace's ``external_llm_allowed``/classification, and a workspace-unbound run
(whose payload is only the committed golden set) requires the operator-level
``GOLDEN_EVAL_LLM_ALLOWED`` opt-in — no path constructs a provider ungated.
"""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from src.agents.llm_provider import LiveProvider
from src.config import settings
from src.eval import harness
from src.services import judge_service, prompt_registry, storage_service
from src.services.common import get_workspace_or_404

_BLOB_PREFIX = "model-ops/prompt-ab"
_HISTORY_CAP = 20
# Bounds a live A/B run to 2 * _MAX_CASES provider calls (the golden set currently has 12
# answerable questions; the first 10 in fixture order are the documented subset).
_MAX_CASES = 10


def _blob_key(prompt_id: str) -> str:
    return f"{_BLOB_PREFIX}/{prompt_id}.json"


def _golden_cases() -> list[tuple[str, str]]:
    """The judged ``(question, context)`` pairs: answerable golden questions, capped.

    Context is the concatenated text of the question's relevant corpus chunks — exactly the
    ground truth the judge scores each answer against.
    """
    data = harness.load_golden_set()
    text_by_key = {entry["key"]: entry["text"] for entry in data["corpus"]}
    cases: list[tuple[str, str]] = []
    for q in data["questions"]:
        keys = q.get("relevant") or []
        if not q.get("should_answer") or not keys:
            continue
        cases.append((q["question"], "\n\n".join(text_by_key[key] for key in keys)))
        if len(cases) == _MAX_CASES:
            break
    return cases


def _score_template(provider, judge, template: str, cases: list[tuple[str, str]]) -> dict:
    """Answer every case with ``template`` as the system prompt and judge each answer."""
    faithful = 0
    for question, context in cases:
        answer = provider.complete(template, f"QUESTION:\n{question}\n\nEXTRACTS:\n{context}")
        if judge(question, answer, context).faithful:
            faithful += 1
    judged = len(cases)
    return {
        "faithful": faithful,
        "faithful_rate": round(faithful / judged, 4) if judged else 0.0,
        "judged": judged,
    }


def _read_envelope(store: storage_service.BlobStore, prompt_id: str) -> dict | None:
    """The persisted ``{"history": [...]}`` envelope, or None when absent/unreadable.

    An unreadable blob is treated as absent rather than raised: the quality view must degrade to
    honest omission, and a fresh run must not be blocked by a corrupt historical envelope.
    """
    try:
        raw = store.get(_blob_key(prompt_id))
    except KeyError:
        return None
    try:
        envelope = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(envelope, dict) or not isinstance(envelope.get("history"), list):
        return None
    return envelope


# Serializes the read-modify-write below: two concurrent A/B runs for one prompt would
# otherwise each read the same envelope and the second put() would silently drop the first
# run's report. Process-local, which matches the single-process deployment; cross-process
# writers would need a blob-store compare-and-swap this store does not offer.
_history_lock = threading.Lock()


def _append_history(prompt_id: str, report: dict) -> None:
    store = storage_service.get_store()
    with _history_lock:
        envelope = _read_envelope(store, prompt_id) or {"history": []}
        history = [report, *envelope["history"]][:_HISTORY_CAP]
        store.put(
            _blob_key(prompt_id),
            json.dumps({"history": history}, ensure_ascii=False).encode("utf-8"),
        )


def run_ab(
    session: Session,
    prompt_id: str,
    candidate_template: str,
    *,
    provider_factory=None,
    workspace_id: str | None = None,
) -> dict:
    """Evaluate the registered template (A) against ``candidate_template`` (B) and persist.

    Raises :class:`prompt_registry.UnknownPrompt` for an unregistered ``prompt_id`` and
    ``ValueError`` for a blank candidate (both surface as 422 at the route). Every non-applied
    path returns ``{"status": "not_run", "reason": ...}`` without persisting anything.
    ``provider_factory`` exists for tests, exactly as at the ``structured_llm`` seam.
    """
    spec = prompt_registry.get(prompt_id)
    if not candidate_template or not candidate_template.strip():
        raise ValueError("candidate_template must be a non-empty prompt template")
    if workspace_id is not None:
        # A workspace-bound run inherits that workspace's consent, like every other LLM path.
        ws = get_workspace_or_404(session, workspace_id)
        if not (ws.external_llm_allowed and ws.data_classification != "restricted"):
            return {"status": "not_run", "reason": "no_consent", "prompt_id": prompt_id}
    if settings.is_mock:
        return {"status": "not_run", "reason": "mock", "prompt_id": prompt_id}
    if workspace_id is None and not settings.golden_eval_llm_allowed:
        # Workspace-unbound runs send only the committed golden set, but "every LLM path is
        # consent-gated" admits no exception: the operator-level opt-in stands in for the
        # workspace consent this run does not have. Fail closed without one. (After the mock
        # check so hermetic CI keeps its honest "mock" reason.)
        return {"status": "not_run", "reason": "no_consent", "prompt_id": prompt_id}
    if not settings.llm_api_key:
        return {"status": "not_run", "reason": "no_api_key", "prompt_id": prompt_id}

    cases = _golden_cases()
    judge = judge_service.default_judge()
    try:
        provider = (provider_factory or LiveProvider)()
        side_a = _score_template(provider, judge, spec.template, cases)
        side_b = _score_template(provider, judge, candidate_template, cases)
    except Exception:
        # Fail closed: a provider failure mid-run yields no report and persists nothing — a
        # partial A/B would be a fabricated comparison.
        return {"status": "not_run", "reason": "error", "prompt_id": prompt_id}

    if side_a["faithful_rate"] > side_b["faithful_rate"]:
        winner = "a"
    elif side_b["faithful_rate"] > side_a["faithful_rate"]:
        winner = "b"
    else:
        winner = "tie"

    report = {
        "status": "completed",
        "prompt_id": prompt_id,
        "judge": getattr(judge, "name", judge.__class__.__name__),
        "a": {"prompt_version": spec.prompt_version, "prompt_hash": spec.prompt_hash, **side_a},
        "b": {
            "prompt_hash_candidate": hashlib.sha256(
                candidate_template.encode("utf-8")
            ).hexdigest(),
            **side_b,
        },
        "winner": winner,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    _append_history(prompt_id, report)
    return report


def latest_reports() -> list[dict]:
    """The newest persisted report per registered prompt, for the quality dashboard.

    Prompts that have never been A/B-tested (or whose blob is unreadable) are skipped — absence
    stays absent.
    """
    store = storage_service.get_store()
    reports: list[dict] = []
    for prompt_id in prompt_registry.prompt_ids():
        envelope = _read_envelope(store, prompt_id)
        if envelope and envelope["history"]:
            reports.append(envelope["history"][0])
    return reports
