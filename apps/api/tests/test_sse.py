"""G32 — server-sent events for live workspace build progress.

Covers the streaming contract (a terminal `ready` frame closes the stream), transition dedup at
the generator level, the bounded-duration timeout, and the tenant guard (cross-org → 404).
"""
from __future__ import annotations

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.base import Base
from src.models import Organization, Workspace
from src.services import workspace_service


def _payloads(body: str) -> list[dict]:
    return [
        json.loads(block[len("data: "):])
        for block in body.strip().split("\n\n")
        if block.startswith("data: ")
    ]


def test_build_events_stream_yields_ready_frame(client):
    """Acceptance: the SSE endpoint streams a terminal `ready` frame then closes."""
    from src.db.session import SessionLocal

    with SessionLocal() as session:
        # No organization: with no X-Organization-ID header the tenant guard is skipped.
        ws = Workspace(name="SSE ready", organization_id=None, build_status="ready")
        session.add(ws)
        session.commit()
        workspace_id = ws.id

    with client.stream("GET", f"/api/workspaces/{workspace_id}/build-events") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = "".join(resp.iter_text())

    frames = _payloads(body)
    assert frames, "stream produced no data frames"
    assert frames[-1]["status"] == "ready"
    assert frames[-1]["workspace_id"] == workspace_id


def test_build_events_cross_org_is_404(client):
    """The `/api/workspaces/{id}` tenant guard covers the SSE path: another org gets a 404."""
    from src.db.session import SessionLocal

    with SessionLocal() as session:
        owner = Organization(name="SSE Owner Org", slug="sse-owner-org")
        session.add(owner)
        session.flush()
        ws = Workspace(name="SSE tenant", organization_id=owner.id, build_status="ready")
        session.add(ws)
        session.commit()
        workspace_id = ws.id
        owner_id = owner.id

    resp = client.get(
        f"/api/workspaces/{workspace_id}/build-events",
        headers={"X-Organization-ID": ("b" * 32) if owner_id != "b" * 32 else ("c" * 32)},
    )
    assert resp.status_code == 404


def _memory_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, class_=Session), engine


def test_iter_build_events_emits_each_transition_once_until_ready():
    """Frames are emitted only on (status, step) change; the terminal `ready` frame ends it."""
    factory, engine = _memory_factory()
    with factory() as session:
        ws = Workspace(
            name="Build",
            organization_id="a" * 32,
            build_status="building",
            build_step="ingesting",
            build_ticker="MSFT",
        )
        session.add(ws)
        session.commit()
        workspace_id = ws.id

    steps = {"n": 0}

    def advance(_interval: float) -> None:
        steps["n"] += 1
        with factory() as session:
            ws = session.get(Workspace, workspace_id)
            if steps["n"] == 1:
                ws.build_step = "running_analysis"  # still building, new step
            else:
                ws.build_status = "ready"
                ws.build_step = None
            session.commit()

    frames = list(
        workspace_service.iter_build_events(
            workspace_id, session_factory=factory, poll_interval=0, sleep=advance
        )
    )
    engine.dispose()

    payloads = _payloads("".join(frames))
    assert [p["status"] for p in payloads] == ["building", "building", "ready"]
    assert [p["step"] for p in payloads] == ["ingesting", "running_analysis", None]


def test_iter_build_events_times_out_without_hanging():
    """A build that never terminates yields a final `timeout` frame and returns."""
    factory, engine = _memory_factory()
    with factory() as session:
        ws = Workspace(
            name="Stuck", organization_id="a" * 32, build_status="building", build_step="ingesting"
        )
        session.add(ws)
        session.commit()
        workspace_id = ws.id

    frames = list(
        workspace_service.iter_build_events(
            workspace_id, session_factory=factory, poll_interval=0, max_duration=0.0
        )
    )
    engine.dispose()

    payloads = _payloads("".join(frames))
    assert payloads[0]["status"] == "building"
    assert payloads[-1]["timeout"] is True
