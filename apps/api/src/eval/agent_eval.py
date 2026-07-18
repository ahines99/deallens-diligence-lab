"""G62 — Agent evaluation harness: canonical objectives through the G57 substrate, offline.

What this measures — and what it honestly does not. Providers here are SCRIPTED (the committed
``fixtures/agent_golden.json`` scripts replay Anthropic-format tool_use/text blocks; no LLM is
ever constructed and no network exists), so the harness does not measure model intelligence.
What IS under test is everything a real model's output must pass through — governed tool
execution, the fail-closed grounding gate, the step budget, transcript sealing — plus the
metrics machinery itself. That is the useful CI question for Theme I: "does the agent substrate
still behave correctly on canonical objectives?", answered deterministically on every run.

Same golden-set + committed-baseline pattern as ``harness.py`` (G03): the golden cases live in
``fixtures/agent_golden.json``, the committed baseline in ``agent_metrics.json``, and
``tests/test_agent_eval.py`` is the CI regression gate. Regenerate the baseline intentionally
with ``python -m src.eval.agent_eval`` after a deliberate substrate change.

Each golden case supplies an objective, the provider script, a workspace fixture (chunks and/or
risk findings materialized into an ephemeral in-memory database — the caller's database is never
touched unless a ``session_factory`` is passed explicitly), and expectations: the tools the run
must use, the terminal status, and whether a grounded completion is required. Metrics:

* ``tool_selection_accuracy`` — fraction of cases whose expected tools all appear in the run's
  ``tools_used``;
* ``grounding_pass_rate`` — fraction of ``must_ground`` cases that completed with a grounded
  answer;
* ``rejection_correctness`` — fraction of cases expecting ``rejected_ungrounded`` that the gate
  actually rejected;
* ``status_accuracy`` — fraction of cases whose terminal status matched exactly.

The runner temporarily flips ``settings.llm_mode``/``llm_api_key`` to satisfy the agent's
consent gate (restored in a ``finally``); the "key" is a placeholder that is never sent
anywhere because the scripted provider replaces the live client entirely.
"""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.config import settings
from src.db.base import Base
from src.models import DocumentChunk, Filing, RiskFinding, Workspace
from src.services import agent_service

_FIXTURES = Path(__file__).parent / "fixtures" / "agent_golden.json"
_BASELINE = Path(__file__).parent / "agent_metrics.json"

METRIC_NAMES = (
    "tool_selection_accuracy",
    "grounding_pass_rate",
    "rejection_correctness",
    "status_accuracy",
)
# Terminal statuses a golden case may expect from the substrate.
EXPECTED_STATUSES = ("completed", "rejected_ungrounded", "budget_exhausted")


class ScriptedProvider:
    """Replays a committed response script verbatim; a network call is impossible by shape.

    An exhausted script raises, which the agent loop records as ``status="error"`` — a
    mis-authored case therefore surfaces as a failed per-case entry, never a silent pass.
    """

    model = "scripted-eval-provider"

    def __init__(self, responses: list[dict]) -> None:
        self._responses = list(responses)

    def complete_with_tools(self, system: str, messages: list[dict], tools: list[dict]) -> dict:
        if not self._responses:
            raise RuntimeError("scripted provider exhausted: the case script is too short")
        return self._responses.pop(0)


def load_golden_cases() -> list[dict]:
    """The committed golden cases (``fixtures/agent_golden.json``)."""
    return json.loads(_FIXTURES.read_text(encoding="utf-8"))["cases"]


def _materialize_workspace(session: Session, case: dict, index: int) -> str:
    """Build the case's ephemeral consenting workspace: chunks under one filing, plus risks."""
    workspace = Workspace(
        name=f"agent-eval-{case['id']}",
        deal_type="buyout",
        status="complete",
        external_llm_allowed=True,
    )
    session.add(workspace)
    session.flush()
    fixture = case.get("workspace_fixture") or {}
    chunks = fixture.get("chunks") or []
    if chunks:
        filing = Filing(
            workspace_id=workspace.id,
            company_name="Agent Eval Corp",
            ticker="AEV",
            cik="0000000062",
            form_type="10-K",
            filing_date="2025-02-01",
            accession_number=f"0000000062-25-{index:06d}",
            document_url="https://www.sec.gov/Archives/agent-eval-10k.htm",
            is_synthetic=False,
        )
        session.add(filing)
        session.flush()
        for chunk_index, entry in enumerate(chunks):
            session.add(
                DocumentChunk(
                    filing_id=filing.id,
                    workspace_id=workspace.id,
                    section=entry["section"],
                    chunk_index=chunk_index,
                    chunk_text=entry["text"],
                    source_url=filing.document_url,
                )
            )
    for risk in fixture.get("risks") or []:
        session.add(
            RiskFinding(
                workspace_id=workspace.id,
                risk_category=risk.get("category", "other"),
                risk_category_label=risk.get("category_label", ""),
                title=risk.get("title", ""),
                finding=risk.get("finding", ""),
                severity=risk.get("severity", "medium"),
                severity_score=int(risk.get("severity_score", 5)),
                evidence_ref=risk.get("evidence_ref"),
            )
        )
    session.flush()
    return workspace.id


def _score_case(case: dict, record: dict) -> dict:
    """Per-case verdict with every individual failure named — never a bare boolean."""
    expectations = case["expectations"]
    expected_status = expectations["expected_status"]
    actual_status = record["status"]
    failures: list[str] = []
    if actual_status != expected_status:
        failures.append(f"status: expected {expected_status!r}, got {actual_status!r}")
    missing = sorted(set(expectations.get("expected_tools", [])) - set(record["tools_used"]))
    if missing:
        failures.append(f"tools: expected tools never used: {missing}")
    if expectations.get("must_ground"):
        grounding = record.get("grounding") or {}
        if not (actual_status == "completed" and grounding.get("grounded")):
            failures.append("grounding: expected a grounded completed answer")
    return {
        "id": case["id"],
        "passed": not failures,
        "expected_status": expected_status,
        "actual_status": actual_status,
        "failures": failures,
    }


def _rate(numerator: int, denominator: int) -> float | None:
    """A rounded rate, or ``None`` when the golden set has no case in the denominator —
    honest absence, never a fabricated 0.0 or 1.0."""
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)


def _aggregate(results: list[tuple[dict, dict]]) -> dict:
    tool_ok = sum(
        1
        for case, record in results
        if set(case["expectations"].get("expected_tools", [])) <= set(record["tools_used"])
    )
    must_ground = [
        (case, record) for case, record in results if case["expectations"].get("must_ground")
    ]
    grounded_ok = sum(
        1
        for _case, record in must_ground
        if record["status"] == "completed" and (record.get("grounding") or {}).get("grounded")
    )
    expect_rejected = [
        (case, record)
        for case, record in results
        if case["expectations"]["expected_status"] == "rejected_ungrounded"
    ]
    rejected_ok = sum(
        1 for _case, record in expect_rejected if record["status"] == "rejected_ungrounded"
    )
    status_ok = sum(
        1
        for case, record in results
        if record["status"] == case["expectations"]["expected_status"]
    )
    return {
        "tool_selection_accuracy": _rate(tool_ok, len(results)),
        "grounding_pass_rate": _rate(grounded_ok, len(must_ground)),
        "rejection_correctness": _rate(rejected_ok, len(expect_rejected)),
        "status_accuracy": _rate(status_ok, len(results)),
    }


def run_agent_eval(session_factory=None) -> dict:
    """Run every golden case through the real agent loop and return the aggregated report.

    Shape: ``{"cases": n, "metrics": {...}, "per_case": [{"id", "passed", "expected_status",
    "actual_status", "failures"}]}``. With no ``session_factory`` (the default, and what CI
    uses) each run builds a private in-memory SQLite database, so nothing — fixture workspaces
    or the sealed transcripts the agent commits — ever lands in the application database.
    Passing a factory redirects those commits at the caller's database; the caller owns cleanup.
    """
    cases = load_golden_cases()
    engine = None
    if session_factory is None:
        engine = create_engine(
            "sqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(engine)

        def session_factory() -> Session:
            return Session(engine, expire_on_commit=False)

    # Satisfy the agent's consent gate without pytest: flip settings, restore in ``finally``.
    # The placeholder key is never transmitted — the scripted provider is the whole client.
    prior_mode, prior_key = settings.llm_mode, settings.llm_api_key
    settings.llm_mode = "live"
    settings.llm_api_key = "scripted-eval-placeholder"
    results: list[tuple[dict, dict]] = []
    try:
        for index, case in enumerate(cases):
            with session_factory() as session:
                workspace_id = _materialize_workspace(session, case, index)
                record = agent_service.run_diligence_agent(
                    session,
                    workspace_id,
                    case["objective"],
                    max_steps=int(case.get("max_steps", 8)),
                    provider_factory=lambda case=case: ScriptedProvider(case["script"]),
                )
            results.append((case, record))
    finally:
        settings.llm_mode = prior_mode
        settings.llm_api_key = prior_key
        if engine is not None:
            engine.dispose()

    return {
        "cases": len(cases),
        "metrics": _aggregate(results),
        "per_case": [_score_case(case, record) for case, record in results],
    }


def load_baseline() -> dict:
    """The committed baseline report used as the CI regression floor."""
    return json.loads(_BASELINE.read_text(encoding="utf-8"))


def write_baseline(report: dict) -> None:
    """Overwrite the committed baseline. Intentional-only: ``python -m src.eval.agent_eval``."""
    _BASELINE.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def _main() -> None:  # pragma: no cover - operator convenience, not part of the test gate
    report = run_agent_eval()
    write_baseline(report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":  # pragma: no cover
    _main()
