"""G62 — agent evaluation harness + CI regression gate.

This test IS the gate: CI runs the full pytest suite, so a metric slipping below the committed
baseline (``src/eval/agent_metrics.json``) fails here and therefore fails CI. Everything is
offline and deterministic — providers are the committed scripts in
``fixtures/agent_golden.json``, replayed through the REAL G57 loop (governed tool execution,
fail-closed grounding, budgets, sealing) against an ephemeral in-memory database. What these
tests pin:

* the harness runs green end-to-end with zero LLM/network dependency and leaks nothing into
  the shared application database;
* per-case failures are REPORTED (named in ``failures``), never swallowed — corrupting an
  expectation or a script in-memory must surface as a failed case and degraded metrics;
* the committed baseline is a floor: no metric may fall below it, and (being deterministic)
  the per-case outcomes must match it exactly;
* the golden set actually covers the substrate's terminal statuses and only real tools.
"""
from __future__ import annotations

import copy

from sqlalchemy import select

from src.config import settings
from src.db.session import SessionLocal
from src.eval import agent_eval
from src.models import Workspace
from src.services.agent_tools import tool_definitions


def test_golden_set_covers_the_substrate_contract():
    """Fixture sanity: unique ids, real tools only, and all three terminal statuses covered."""
    cases = agent_eval.load_golden_cases()
    assert len(cases) == 8
    ids = [case["id"] for case in cases]
    assert len(set(ids)) == len(ids)
    known_tools = {tool["name"] for tool in tool_definitions()}
    expected_statuses = set()
    for case in cases:
        assert case["objective"].strip()
        assert case["script"], f"{case['id']}: empty provider script"
        expectations = case["expectations"]
        assert expectations["expected_status"] in agent_eval.EXPECTED_STATUSES
        assert expectations["expected_tools"], f"{case['id']}: no expected tools"
        assert set(expectations["expected_tools"]) <= known_tools
        expected_statuses.add(expectations["expected_status"])
    assert expected_statuses == set(agent_eval.EXPECTED_STATUSES)


def test_harness_runs_green_end_to_end_offline(client):
    """The full pipeline passes on the committed golden set with scripted providers only."""
    assert settings.is_mock, "precondition: the suite runs in mock mode; the harness flips live"
    prior_mode, prior_key = settings.llm_mode, settings.llm_api_key

    report = agent_eval.run_agent_eval()

    assert report["cases"] == len(agent_eval.load_golden_cases()) == 8
    assert set(report["metrics"]) == set(agent_eval.METRIC_NAMES)
    failed = [entry for entry in report["per_case"] if not entry["passed"]]
    assert failed == [], f"golden cases failed: {failed}"
    assert all(value == 1.0 for value in report["metrics"].values())
    # The temporary live-mode flip is restored even though no pytest fixture managed it.
    assert (settings.llm_mode, settings.llm_api_key) == (prior_mode, prior_key)
    # Ephemeral database: no fixture workspace (or its sealed transcript) leaks into the app DB.
    with SessionLocal() as session:
        leaked = session.scalars(
            select(Workspace).where(Workspace.name.like("agent-eval-%"))
        ).all()
    assert leaked == []


def test_corrupted_expectation_yields_a_reported_failed_case(monkeypatch):
    """The harness detects regressions: flip one expected status and the case must fail loudly."""
    cases = copy.deepcopy(agent_eval.load_golden_cases())
    assert cases[0]["expectations"]["expected_status"] == "completed"
    cases[0]["expectations"]["expected_status"] = "rejected_ungrounded"
    monkeypatch.setattr(agent_eval, "load_golden_cases", lambda: cases)

    report = agent_eval.run_agent_eval()

    entry = next(e for e in report["per_case"] if e["id"] == cases[0]["id"])
    assert entry["passed"] is False
    assert entry["actual_status"] == "completed"
    assert any(failure.startswith("status:") for failure in entry["failures"])
    assert report["metrics"]["status_accuracy"] < 1.0
    # The corrupted case now expects a rejection it never gets, so that metric degrades too.
    assert report["metrics"]["rejection_correctness"] < 1.0


def test_corrupted_tool_expectation_is_named_not_swallowed(monkeypatch):
    """A tool-selection miss is reported as its own named failure with the missing tools."""
    cases = copy.deepcopy(agent_eval.load_golden_cases())
    cases[0]["expectations"]["expected_tools"] = ["get_evidence"]
    monkeypatch.setattr(agent_eval, "load_golden_cases", lambda: cases)

    report = agent_eval.run_agent_eval()

    entry = next(e for e in report["per_case"] if e["id"] == cases[0]["id"])
    assert entry["passed"] is False
    assert any("get_evidence" in failure for failure in entry["failures"])
    assert report["metrics"]["tool_selection_accuracy"] < 1.0


def test_exhausted_script_surfaces_as_an_error_case_not_a_crash(monkeypatch):
    """A too-short script becomes ``status='error'`` on that case; the run itself survives."""
    cases = copy.deepcopy(agent_eval.load_golden_cases())
    cases[0]["script"] = cases[0]["script"][:1]  # drop the final answer turn
    monkeypatch.setattr(agent_eval, "load_golden_cases", lambda: cases)

    report = agent_eval.run_agent_eval()

    entry = next(e for e in report["per_case"] if e["id"] == cases[0]["id"])
    assert entry["passed"] is False
    assert entry["actual_status"] == "error"
    # Every other case still ran and passed.
    others = [e for e in report["per_case"] if e["id"] != cases[0]["id"]]
    assert others and all(e["passed"] for e in others)


def test_no_regression_below_committed_baseline(client):
    """The CI gate: metrics must be at or above the committed baseline. The eval is fully
    deterministic, so the per-case outcomes must also match the baseline exactly."""
    report = agent_eval.run_agent_eval()
    baseline = agent_eval.load_baseline()
    assert report["cases"] == baseline["cases"], (
        "golden set changed — regenerate the baseline with `python -m src.eval.agent_eval`"
    )
    for name, base_value in baseline["metrics"].items():
        current = report["metrics"][name]
        assert current is not None and current >= base_value, (
            f"REGRESSION: {name} = {current} < baseline {base_value}. "
            "If intentional, rerun `python -m src.eval.agent_eval`."
        )
    assert report["per_case"] == baseline["per_case"]
