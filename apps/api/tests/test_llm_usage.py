"""G80 — LLM cost telemetry: capture seam, never-raise guarantees, rollup math, quota view.

Offline and deterministic. ``record_call`` is exercised directly and through the provider seam
(``llm_provider._report_usage``); its hard contract — telemetry must never raise, even with the
schema missing or the session factory broken — is pinned here because a telemetry failure that
propagated would fail the live LLM call it was measuring.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import delete, select

from src.config import settings
from src.db.base import now_utc
from src.db.session import SessionLocal, engine
from src.main import _auth_rate_limiter, _org_quota_limiter
from src.models.llm_usage import LlmUsageEvent
from src.services import llm_usage_service, request_context


@pytest.fixture(autouse=True)
def _clean_usage_table(client):
    """Start each test from an empty telemetry table (the suite DB is session-scoped).

    Depends on ``client`` so app startup has created the schema before direct-session tests run.
    """
    with SessionLocal() as session:
        session.execute(delete(LlmUsageEvent))
        session.commit()
    yield


def _rows() -> list[LlmUsageEvent]:
    with SessionLocal() as session:
        return list(session.scalars(select(LlmUsageEvent).order_by(LlmUsageEvent.model)))


def _add_event(
    organization_id: str | None,
    model: str,
    input_tokens: int | None,
    output_tokens: int | None,
    *,
    age_hours: int = 0,
) -> None:
    with SessionLocal() as session:
        session.add(
            LlmUsageEvent(
                organization_id=organization_id,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                created_at=now_utc() - timedelta(hours=age_hours),
            )
        )
        session.commit()


# ------------------------------------------------------------------ record_call attribution
def test_record_call_attributes_to_the_context_organization():
    token = request_context.current_organization_id.set("org-ctx-123")
    try:
        llm_usage_service.record_call(model="claude-test", input_tokens=10, output_tokens=20)
    finally:
        request_context.current_organization_id.reset(token)

    rows = _rows()
    assert len(rows) == 1
    assert rows[0].organization_id == "org-ctx-123"
    assert rows[0].model == "claude-test"
    assert rows[0].input_tokens == 10
    assert rows[0].output_tokens == 20
    assert rows[0].created_at is not None


def test_record_call_without_request_context_records_untagged():
    """Background paths never guess a tenant: no contextvar -> organization_id is NULL."""
    assert request_context.current_organization_id.get() is None
    llm_usage_service.record_call(model="claude-test", input_tokens=None, output_tokens=None)

    rows = _rows()
    assert len(rows) == 1
    assert rows[0].organization_id is None
    assert rows[0].input_tokens is None
    assert rows[0].output_tokens is None


# ------------------------------------------------------------------ never-raise guarantees
def test_record_call_is_a_noop_when_the_table_is_missing():
    """A deployment whose migration has not run yet must not break live LLM calls."""
    LlmUsageEvent.__table__.drop(bind=engine)
    try:
        llm_usage_service.record_call(model="claude-test", input_tokens=1, output_tokens=2)
    finally:
        LlmUsageEvent.__table__.create(bind=engine)
    assert _rows() == []


def test_record_call_swallows_a_broken_session_factory(monkeypatch):
    def _boom() -> None:
        raise RuntimeError("session factory unavailable")

    monkeypatch.setattr("src.db.session.SessionLocal", _boom)
    llm_usage_service.record_call(model="claude-test", input_tokens=1, output_tokens=2)


# ------------------------------------------------------------------ provider seam
def test_provider_report_usage_seam_lands_a_row():
    """Driving ``llm_provider._report_usage`` directly persists one attributed event."""
    from src.agents.llm_provider import _report_usage

    token = request_context.current_organization_id.set("org-seam")
    try:
        _report_usage("claude-seam-model", 111, 222)
    finally:
        request_context.current_organization_id.reset(token)

    rows = _rows()
    assert len(rows) == 1
    assert rows[0].organization_id == "org-seam"
    assert rows[0].model == "claude-seam-model"
    assert rows[0].input_tokens == 111
    assert rows[0].output_tokens == 222


# ------------------------------------------------------------------ spend_summary rollups
def test_spend_summary_math_and_org_scoping():
    _add_event("org-1", "model-a", 10, 5)
    _add_event("org-1", "model-a", None, None)  # null tokens sum as 0, the call still counts
    _add_event("org-1", "model-b", 7, 3)
    _add_event("org-2", "model-a", 100, 100)
    _add_event(None, "model-a", 1000, 1000)  # untagged background usage

    with SessionLocal() as session:
        org1 = llm_usage_service.spend_summary(session, "org-1")
        org2 = llm_usage_service.spend_summary(session, "org-2")
        global_view = llm_usage_service.spend_summary(session, None)

    assert org1 == {
        "total_calls": 3,
        "input_tokens": 17,
        "output_tokens": 8,
        "by_model": [
            {"model": "model-a", "calls": 2, "input_tokens": 10, "output_tokens": 5},
            {"model": "model-b", "calls": 1, "input_tokens": 7, "output_tokens": 3},
        ],
    }
    # Untagged rows never leak into any tenant's summary...
    assert org2["total_calls"] == 1
    assert org2["input_tokens"] == 100
    # ...but stay visible in the global view so operators can reconcile total spend.
    assert global_view["total_calls"] == 5
    assert global_view["input_tokens"] == 10 + 7 + 100 + 1000
    assert global_view["output_tokens"] == 5 + 3 + 100 + 1000


def test_spend_summary_window_filters_old_rows():
    _add_event("org-w", "model-a", 10, 10)
    _add_event("org-w", "model-a", 40, 40, age_hours=30)  # outside a 24h window

    with SessionLocal() as session:
        windowed = llm_usage_service.spend_summary(session, "org-w", window_hours=24)
        all_time = llm_usage_service.spend_summary(session, "org-w", window_hours=None)

    assert windowed["total_calls"] == 1
    assert windowed["input_tokens"] == 10
    assert all_time["total_calls"] == 2
    assert all_time["input_tokens"] == 50


def test_spend_summary_empty_is_zeroed():
    with SessionLocal() as session:
        summary = llm_usage_service.spend_summary(session, "org-nothing", window_hours=24)
    assert summary == {
        "total_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "by_model": [],
    }


# ------------------------------------------------------------------ quota-usage endpoint
@pytest.fixture()
def _authed(monkeypatch):
    """Production-auth mode with clean limiters, mirroring tests/test_quotas.py."""
    monkeypatch.setattr(settings, "auth_required", True)
    monkeypatch.setattr(settings, "org_request_quota_per_minute", 0)
    monkeypatch.setattr(settings, "org_build_quota_per_hour", 0)
    _org_quota_limiter.clear()
    _auth_rate_limiter.clear()
    yield
    _org_quota_limiter.clear()
    _auth_rate_limiter.clear()


def _register(client, label: str) -> dict:
    _auth_rate_limiter.clear()
    suffix = uuid.uuid4().hex[:10]
    response = client.post(
        "/api/auth/register",
        json={
            "email": f"{label}-{suffix}@example.test",
            "display_name": f"{label.title()} Analyst",
            "password": "correct horse portfolio battery",
            "organization_name": f"{label.title()} Capital {suffix}",
            "organization_slug": f"{label}-capital-{suffix}",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_quota_usage_endpoint_includes_org_scoped_llm_spend(client, _authed):
    owner = _register(client, "llmspend")
    org = owner["principal"]["organization_id"]
    headers = {"Authorization": f"Bearer {owner['access_token']}"}

    _add_event(org, "claude-live", 100, 50)
    _add_event(org, "claude-live", None, None)
    _add_event(org, "claude-live", 40, 40, age_hours=30)  # outside the 24h window
    _add_event("someone-else", "claude-live", 999, 999)
    _add_event(None, "claude-live", 999, 999)  # untagged: global-only, never in an org view

    response = client.get(f"/api/organizations/{org}/quota-usage", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()

    # The pre-existing buckets contract is intact alongside the new sibling.
    assert body["organization_id"] == org
    assert {bucket["name"] for bucket in body["buckets"]} >= {"requests", "builds"}
    assert body["llm_spend"] == {
        "window_hours": 24,
        "total_calls": 2,
        "input_tokens": 100,
        "output_tokens": 50,
        "by_model": [
            {"model": "claude-live", "calls": 2, "input_tokens": 100, "output_tokens": 50},
        ],
    }

    # Cross-tenant reads stay a 404, never an existence (or spend) oracle.
    other = _register(client, "llmspend-other")
    other_headers = {"Authorization": f"Bearer {other['access_token']}"}
    assert (
        client.get(f"/api/organizations/{org}/quota-usage", headers=other_headers).status_code
        == 404
    )
