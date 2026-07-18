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

import contextvars
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

# G61 recovery seam — idempotency for client-supplied request ids. A client that saw an
# ambiguous network failure (its POST may or may not have reached us) retries with the SAME
# id: a still-running duplicate is refused with 409 (never a second run), and an
# already-sealed duplicate replays the sealed record (never a second run either). The registry
# is process-local and SINGLE-PROCESS BY DESIGN, like every in-process limiter in main.py: a
# multi-worker or multi-replica deployment would need a shared claim (e.g. a unique
# client_request_id column on the sealed artifact) for the 409 guarantee to hold across
# processes — a documented limitation, not an accident. The replay lookup is bounded below;
# the console's recovery retries arrive within seconds of their run, never 50 seals later.
_inflight_lock = threading.Lock()
_inflight_request_keys: set[str] = set()
# Memo-section and comparative runs seal into the same workspace agent_run stream, so the
# window is sized generously: evicting a run within the seconds a console retry takes would
# need 200 concurrent seals in one workspace. The scan reads only (id, metadata) rows.
_REPLAY_SCAN_LIMIT = 200


def _request_key(workspace_id: str, client_request_id: str) -> str:
    return f"{workspace_id}:{client_request_id}"


def _claim_request_id(workspace_id: str, client_request_id: str) -> bool:
    key = _request_key(workspace_id, client_request_id)
    with _inflight_lock:
        if key in _inflight_request_keys:
            return False
        _inflight_request_keys.add(key)
        return True


def _release_request_id(workspace_id: str, client_request_id: str) -> None:
    with _inflight_lock:
        _inflight_request_keys.discard(_request_key(workspace_id, client_request_id))


def _find_sealed_run(session, workspace_id: str, client_request_id: str) -> dict | None:
    """The sealed run record previously produced for this request id, if one exists.

    Scans only (id, metadata) of the newest sealed runs — the full transcript loads for the
    single matched row, not for all fifty scanned candidates.
    """
    rows = session.execute(
        select(ArtifactVersion.id, ArtifactVersion.artifact_metadata)
        .where(
            ArtifactVersion.workspace_id == workspace_id,
            ArtifactVersion.artifact_type == _ARTIFACT_TYPE,
        )
        .order_by(ArtifactVersion.version.desc())
        .limit(_REPLAY_SCAN_LIMIT)
    ).all()
    for row_id, metadata in rows:
        if (metadata or {}).get("client_request_id") == client_request_id:
            row = session.get(ArtifactVersion, row_id)
            return {**row.content_json, "artifact_version_id": row.id}
    return None


_DUPLICATE_DETAIL = (
    "duplicate_in_flight: a run with this client_request_id is still executing; recover its "
    "sealed transcript from GET .../agent/runs instead of re-running"
)


def _replay_or_claim(
    session, workspace_id: str, client_request_id: str, objective: str
) -> dict | None:
    """Return the sealed record to REPLAY, or claim the id and return None (caller runs).

    Claim FIRST, then look for a sealed twin: the claim is the mutual exclusion, so once it
    succeeds the original holder either never existed or had already sealed AND committed
    before releasing — the lookup after a successful claim can never miss a finished twin.
    (Looking up before claiming had a race: a retry could read no-seal, then claim just after
    the original sealed and released, and re-run.) A sealed twin releases the claim and is
    replayed; an in-flight twin raises 409. A reused id whose sealed twin ran a DIFFERENT
    objective is a client bug, refused loudly rather than silently answered with the wrong
    run's record.
    """
    if not _claim_request_id(workspace_id, client_request_id):
        raise HTTPException(status_code=409, detail=_DUPLICATE_DETAIL)
    sealed = _find_sealed_run(session, workspace_id, client_request_id)
    if sealed is not None:
        _release_request_id(workspace_id, client_request_id)
        if sealed.get("objective") != objective:
            raise HTTPException(
                status_code=409,
                detail=(
                    "request_id_reuse_mismatch: this client_request_id already ran a "
                    "different objective; use a fresh id per user action"
                ),
            )
        return sealed
    return None


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
    rid = payload.client_request_id
    if rid is not None:
        sealed = _replay_or_claim(session, workspace_id, rid, payload.objective)
        if sealed is not None:
            return AgentRunOut.model_validate(sealed)
    try:
        record = agent_service.run_diligence_agent(
            session,
            workspace_id,
            payload.objective,
            actor_id=_actor_id(request, header_actor_id),
            max_steps=payload.max_steps,
            client_request_id=rid,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        if rid is not None:
            _release_request_id(workspace_id, rid)
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

    ``client_request_id`` (optional) makes the POST idempotent — on this route AND the
    non-streaming twin, which share one registry. While a run with the same id is still
    executing, a duplicate POST is refused with 409 (``duplicate_in_flight``); once it seals, a
    duplicate replays the sealed record (here: as the single ``finished`` frame). A client
    facing an ambiguous network failure retries with the SAME id, so one user action can never
    execute, bill, or seal twice.

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
    rid = payload.client_request_id
    if rid is not None:
        sealed = _replay_or_claim(session, workspace_id, rid, payload.objective)
        if sealed is not None:
            # Idempotent replay: this request id already ran and sealed — stream its record as
            # the single terminal frame instead of executing a second run.
            return StreamingResponse(
                iter([_sse_frame("finished", sealed)]),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
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
                client_request_id=rid,
            )
            events.put(("finished", record))
        except Exception as exc:  # noqa: BLE001 — surfaced to the client as a terminal frame
            events.put(("error", {"detail": str(exc) or "The agent run failed."}))
        finally:
            worker_session.close()
            if rid is not None:
                # The worker owns the claim until the transcript is sealed (it outlives the HTTP
                # response), so a retry during the run 409s and one after it replays the seal.
                _release_request_id(workspace_id, rid)

    # A raw Thread starts with an EMPTY contextvars context (anyio only propagates context into
    # its own threadpool), so the worker's live LLM calls would record their G80 usage rows with
    # organization_id=None — untagged spend the org's quota-usage rollup never sees. Snapshot the
    # request context here (identity middleware already stamped current_organization_id) and run
    # the worker inside the copy. Started here, not in the generator, so the claim above is
    # always released even if the response body is never consumed; a failed thread START (fd or
    # thread exhaustion) releases it too — the worker's finally never runs in that case, and a
    # leaked claim would 409 this request id forever.
    request_ctx = contextvars.copy_context()
    try:
        threading.Thread(
            target=request_ctx.run, args=(_work,), name="agent-run-stream", daemon=True
        ).start()
    except Exception:
        if rid is not None:
            _release_request_id(workspace_id, rid)
        raise

    def _stream():
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
