"""G57/G61 — the diligence agent endpoints.

One POST runs a budget-capped, consent-gated tool loop over the workspace's governed read-only
tools and returns the full sealed run record (see ``agent_service`` for the boundaries). G61 adds
a streaming twin of that POST (server-sent events, one frame per loop event) plus a rehydration
listing of sealed transcripts, so the console can render the timeline live and recover a dropped
connection from the sealed artifact — the artifact stays the source of truth, streams are never
resumed. Both run paths are in the LLM-capable quota set (G58), so a live deployment's spend
stays bounded.
"""
from __future__ import annotations

import json
import queue
import threading
from typing import Annotated, Callable

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from src.db.session import SessionLocal
from src.models.underwriting_data import ArtifactVersion
from src.routers.deps import SessionDep
from src.schemas.agent import AgentRunOut, AgentRunRequest
from src.services import agent_service
from src.services.common import get_workspace_or_404

router = APIRouter(prefix="/api", tags=["agent"])

# G61 test seam: tests monkeypatch this with a factory returning a scripted provider so the
# streaming route can be driven end to end with zero network. Production leaves it ``None`` and
# the service constructs its default ``LiveProvider``. Read at run time inside the worker thread,
# so a monkeypatched value is always honored.
_provider_factory_override: Callable[[], object] | None = None

# How long the stream waits for the NEXT loop event before giving up with a terminal ``error``
# frame. The worker keeps running to completion regardless (the transcript still seals); the
# client recovers by reloading the newest sealed run from ``GET .../agent/runs``.
STREAM_EVENT_TIMEOUT_SECONDS = 300.0

_ARTIFACT_TYPE = "agent_run"  # mirrors agent_service's sealed-transcript artifact type


def _actor_id(request: Request, header_actor_id: str | None) -> str | None:
    principal = getattr(request.state, "principal", None)
    return principal.user_id if principal is not None else header_actor_id


def _sse_frame(event_type: str, payload: dict) -> str:
    """One SSE frame, mirroring the G32 build-events format plus a named ``event:`` line."""
    return f"event: {event_type}\ndata: {json.dumps(payload, default=str)}\n\n"


@router.post("/workspaces/{workspace_id}/agent/run", response_model=AgentRunOut)
def run_agent(
    workspace_id: str,
    payload: AgentRunRequest,
    session: SessionDep,
    request: Request,
    header_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
) -> AgentRunOut:
    try:
        record = agent_service.run_diligence_agent(
            session,
            workspace_id,
            payload.objective,
            actor_id=_actor_id(request, header_actor_id),
            max_steps=payload.max_steps,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return AgentRunOut.model_validate(record)


@router.post("/workspaces/{workspace_id}/agent/run-stream")
def run_agent_stream(
    workspace_id: str,
    payload: AgentRunRequest,
    session: SessionDep,
    request: Request,
    header_actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
) -> StreamingResponse:
    """G61 — the same run as ``POST .../agent/run``, streamed as server-sent events.

    Frame contract (``event: <type>`` + one JSON ``data:`` line), in emission order:

    * ``started`` — ``{"workspace_id", "objective"}`` once the tool loop begins;
    * ``tool_step`` — ``{"step": <AgentStepOut>, "index": <int>}`` per executed tool call;
    * ``finished`` — terminal; the FULL sealed run record, identical to the non-streaming
      response body. Gated runs (mock mode, missing consent, no API key) stream exactly this one
      frame carrying the honest ``not_run`` record;
    * ``error`` — terminal; ``{"detail": <str>}`` if the worker raised or the loop stalled past
      ``STREAM_EVENT_TIMEOUT_SECONDS``.

    A dropped connection is NOT resumable by design: the sealed ``agent_run`` artifact is the
    replay source, and clients recover by reloading ``GET .../agent/runs`` (newest first) instead
    of re-running the agent.

    Session discipline: the run executes on a worker thread that opens its OWN ``SessionLocal()``
    session (opened in the thread, closed in its ``finally``) — the request-scoped session is not
    thread-safe and is used only for the pre-stream workspace 404 check.

    NOTE (G58, for the ``src/main.py`` owner): this path must join ``agent/run`` in
    ``_LLM_CAPABLE_PATHS`` (e.g. ``agent/run(?:-stream)?``) so the org LLM quota covers it.
    """
    # 404 (and the tenant guard's cross-org 404) surface before the stream opens; exceptions
    # raised after streaming starts can no longer become HTTP status codes.
    get_workspace_or_404(session, workspace_id)
    actor_id = _actor_id(request, header_actor_id)
    events: queue.Queue[tuple[str, dict]] = queue.Queue()

    def _forward(event: dict) -> None:
        kind = event.get("type", "")
        if kind == "finished":
            # Dropped: the route's own terminal frame carries the FULL sealed record instead of
            # the service's summary, so the stream ends with sealed-artifact parity.
            return
        events.put((kind, {k: v for k, v in event.items() if k != "type"}))

    def _work() -> None:
        worker_session = SessionLocal()  # thread-private; the request session stays untouched
        try:
            record = agent_service.run_diligence_agent(
                worker_session,
                workspace_id,
                payload.objective,
                actor_id=actor_id,
                max_steps=payload.max_steps,
                provider_factory=_provider_factory_override,
                on_event=_forward,
            )
            events.put(("finished", record))
        except Exception as exc:  # noqa: BLE001 — surfaced to the client as a terminal frame
            events.put(("error", {"detail": str(exc) or "The agent run failed."}))
        finally:
            worker_session.close()

    def _stream():
        threading.Thread(target=_work, name="agent-run-stream", daemon=True).start()
        while True:
            try:
                kind, body = events.get(timeout=STREAM_EVENT_TIMEOUT_SECONDS)
            except queue.Empty:
                yield _sse_frame("error", {"detail": "stream_timeout"})
                return
            yield _sse_frame(kind, body)
            if kind in ("finished", "error"):
                return

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/workspaces/{workspace_id}/agent/runs", response_model=list[AgentRunOut])
def list_agent_runs(
    workspace_id: str,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> list[AgentRunOut]:
    """G61 rehydration — the newest sealed ``agent_run`` transcripts, newest first.

    The sealed transcript IS the replay source: a client whose SSE connection dropped mid-run
    reloads the newest sealed record here rather than resuming the stream. ``artifact_version_id``
    is stamped from the row id, so every listed record matches its sealing artifact exactly.
    """
    get_workspace_or_404(session, workspace_id)
    rows = session.scalars(
        select(ArtifactVersion)
        .where(
            ArtifactVersion.workspace_id == workspace_id,
            ArtifactVersion.artifact_type == _ARTIFACT_TYPE,
        )
        .order_by(ArtifactVersion.version.desc())
        .limit(limit)
    )
    return [
        AgentRunOut.model_validate({**row.content_json, "artifact_version_id": row.id})
        for row in rows
    ]
