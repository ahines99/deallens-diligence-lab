"""Durable DB-backed job queue: enqueue, atomic claim, heartbeat, retry, crash recovery.

Generalizes the webhook-outbox pattern (``webhook_service``): workers claim work with an
atomic ``UPDATE ... WHERE status IN (...)`` rowcount check so each attempt runs on exactly
one worker, failed attempts requeue with exponential backoff until attempts are exhausted,
and ``recover_stale`` requeues ``running`` jobs whose heartbeat stopped (crashed worker).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from src.db.base import new_uuid, now_utc
from src.models.job import BackgroundJob

logger = logging.getLogger("deallens.jobs")

_RETRY_BASE_SECONDS = 30
_RETRY_MAX_SECONDS = 3_600
_STALE_HEARTBEAT_SECONDS = 300
_ACTIVE_STATUSES = ("queued", "running", "failed")

JobHandler = Callable[[Session, BackgroundJob], None]


def _handle_workspace_build(session: Session, job: BackgroundJob) -> None:
    from src.db.session import SessionLocal
    from src.services import workspace_service

    workspace_id = (job.payload or {}).get("workspace_id")
    if not workspace_id:
        raise ValueError("workspace_build job payload requires 'workspace_id'")

    def _beat() -> None:
        # Liveness pings run on their OWN session: `heartbeat` commits, and committing the
        # shared build session mid-stage would flush the projection deletes and break
        # run_full_analysis's atomic-projection contract (a later stage failure could then
        # leave the workspace with no risks/memo/plan). Best-effort: a skipped beat (e.g.
        # SQLite write-lock contention with the build transaction) only risks a benign
        # stale-recovery re-run, never a poisoned build.
        try:
            with SessionLocal() as beat_session:
                heartbeat(beat_session, job.id, job.claimed_by or "")
        except OperationalError:
            logger.debug("Heartbeat skipped for job %s (transient DB contention)", job.id)

    # Two failure domains are deliberately separated. Build-level failures (bad ticker, EDGAR
    # down) are caught inside run_build and land on Workspace.build_status="failed", which the
    # user retries via POST /build/retry — the JOB legitimately "succeeded" because it ran the
    # build to completion. Infrastructure failures (a raised exception: DB lock, process crash)
    # propagate out of the handler and drive the job's retry/backoff and stale recovery.
    workspace_service.run_build(session, workspace_id, heartbeat=_beat)


def _reconcile_workspace_build(session: Session, workspace_id: str, reason: str) -> None:
    """Unstick a workspace whose build job died before ``run_build`` could set a terminal state.

    Without this, an infrastructure crash (process kill, DB lock) between claim and
    ``run_build``'s own try/except leaves ``build_status='building'`` forever and
    ``/build/retry`` 409s — the workspace becomes unrecoverable via the API.
    """
    from src.models.workspace import Workspace

    ws = session.get(Workspace, workspace_id)
    if ws is not None and ws.build_status == "building":
        ws.build_status = "failed"
        ws.build_error = reason
        ws.build_step = None


JOB_HANDLERS: dict[str, JobHandler] = {
    "workspace_build": _handle_workspace_build,
}


def enqueue(
    session: Session,
    job_type: str,
    payload: dict,
    *,
    max_attempts: int = 3,
) -> BackgroundJob:
    """Insert a queued job, deduplicating against active jobs for the same workspace.

    If an active (queued/running/failed) job of the same type already targets the same
    ``workspace_id``, that job is returned instead of enqueuing a duplicate.
    """
    workspace_id = payload.get("workspace_id")
    if workspace_id:
        active = session.scalars(
            select(BackgroundJob).where(
                BackgroundJob.job_type == job_type,
                BackgroundJob.status.in_(_ACTIVE_STATUSES),
            )
        )
        for job in active:
            if (job.payload or {}).get("workspace_id") == workspace_id:
                return job
    job = BackgroundJob(
        job_type=job_type,
        payload=payload,
        status="queued",
        max_attempts=max_attempts,
        next_attempt_at=now_utc(),
    )
    session.add(job)
    session.commit()
    return job


def _claim_job(session: Session, job_id: str, worker_id: str) -> BackgroundJob | None:
    """Atomically transition one specific job to ``running`` for this worker.

    The rowcount check is the at-least-once guarantee: when two workers race for the same
    row, exactly one UPDATE matches and the loser sees rowcount 0.
    """
    now = now_utc()
    claimed = session.execute(
        update(BackgroundJob)
        .where(
            BackgroundJob.id == job_id,
            BackgroundJob.status.in_(("queued", "failed")),
            BackgroundJob.attempts < BackgroundJob.max_attempts,
            or_(BackgroundJob.next_attempt_at.is_(None), BackgroundJob.next_attempt_at <= now),
        )
        .values(
            status="running",
            attempts=BackgroundJob.attempts + 1,
            claimed_by=worker_id,
            heartbeat_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        session.rollback()
        return None
    session.commit()
    job = session.get(BackgroundJob, job_id)
    if job is not None:
        # The raw UPDATE bypassed the identity map; reload the claimed state.
        session.refresh(job)
    return job


def claim_next(
    session: Session,
    worker_id: str,
    job_types: list[str] | None = None,
) -> BackgroundJob | None:
    """Claim the oldest due job (queued, or failed and past its backoff), if any."""
    now = now_utc()
    clauses = [
        BackgroundJob.status.in_(("queued", "failed")),
        BackgroundJob.attempts < BackgroundJob.max_attempts,
        or_(BackgroundJob.next_attempt_at.is_(None), BackgroundJob.next_attempt_at <= now),
    ]
    if job_types:
        clauses.append(BackgroundJob.job_type.in_(job_types))
    candidate_ids = list(
        session.scalars(
            select(BackgroundJob.id).where(*clauses).order_by(BackgroundJob.created_at)
        )
    )
    for job_id in candidate_ids:
        job = _claim_job(session, job_id, worker_id)
        if job is not None:
            return job
    return None


def heartbeat(session: Session, job_id: str, worker_id: str) -> bool:
    """Refresh the liveness timestamp; returns False if this worker no longer owns the job.

    Commits ``session`` — never pass a session holding uncommitted handler work; use a
    dedicated short-lived session when beating from inside a job handler.
    """
    result = session.execute(
        update(BackgroundJob)
        .where(
            BackgroundJob.id == job_id,
            BackgroundJob.claimed_by == worker_id,
            BackgroundJob.status == "running",
        )
        .values(heartbeat_at=now_utc())
        .execution_options(synchronize_session=False)
    )
    session.commit()
    return result.rowcount == 1


def _owns(job: BackgroundJob, worker_id: str | None) -> bool:
    """A worker may only finalize a job it still holds — a stale-recovered requeue that got
    reclaimed by another worker must not be overwritten by the superseded one."""
    return worker_id is None or (job.status == "running" and job.claimed_by == worker_id)


def complete(session: Session, job: BackgroundJob, worker_id: str | None = None) -> BackgroundJob:
    if not _owns(job, worker_id):
        session.rollback()
        return job
    job.status = "succeeded"
    job.next_attempt_at = None
    job.last_error = None
    session.commit()
    return job


def fail(
    session: Session, job: BackgroundJob, error: str, worker_id: str | None = None
) -> BackgroundJob:
    """Requeue with exponential backoff, or mark dead once attempts are exhausted."""
    if not _owns(job, worker_id):
        session.rollback()
        return job
    job.last_error = (error or "unknown error")[:2_000]
    if job.attempts >= job.max_attempts:
        job.status = "dead"
        job.next_attempt_at = None
        # A dead build job must not strand its workspace in "building".
        if job.job_type == "workspace_build":
            workspace_id = (job.payload or {}).get("workspace_id")
            if workspace_id:
                _reconcile_workspace_build(
                    session, workspace_id, f"build job exhausted retries: {job.last_error}"
                )
    else:
        job.status = "failed"
        delay = min(
            _RETRY_BASE_SECONDS * (2 ** max(job.attempts - 1, 0)),
            _RETRY_MAX_SECONDS,
        )
        job.next_attempt_at = now_utc() + timedelta(seconds=delay)
        job.claimed_by = None
    session.commit()
    return job


def recover_stale(
    session: Session, older_than_seconds: int = _STALE_HEARTBEAT_SECONDS
) -> int:
    """Requeue ``running`` jobs whose worker stopped heartbeating (crash recovery).

    Jobs that crashed on their final permitted attempt go straight to ``dead`` rather
    than re-running forever.
    """
    cutoff = now_utc() - timedelta(seconds=older_than_seconds)
    stale = [
        BackgroundJob.status == "running",
        or_(BackgroundJob.heartbeat_at.is_(None), BackgroundJob.heartbeat_at < cutoff),
    ]
    # Capture workspace-build jobs about to die so their workspaces can be unstuck below.
    dying_builds = list(
        session.scalars(
            select(BackgroundJob).where(
                *stale,
                BackgroundJob.attempts >= BackgroundJob.max_attempts,
                BackgroundJob.job_type == "workspace_build",
            )
        )
    )
    dead = session.execute(
        update(BackgroundJob)
        .where(*stale, BackgroundJob.attempts >= BackgroundJob.max_attempts)
        .values(
            status="dead",
            next_attempt_at=None,
            last_error="Worker heartbeat went stale on the final attempt",
        )
        .execution_options(synchronize_session=False)
    )
    for job in dying_builds:
        workspace_id = (job.payload or {}).get("workspace_id")
        if workspace_id:
            _reconcile_workspace_build(
                session, workspace_id, "build worker crashed on the final attempt"
            )
    requeued = session.execute(
        update(BackgroundJob)
        .where(*stale, BackgroundJob.attempts < BackgroundJob.max_attempts)
        .values(
            status="queued",
            next_attempt_at=now_utc(),
            claimed_by=None,
            last_error="Recovered a stale in-progress job",
        )
        .execution_options(synchronize_session=False)
    )
    recovered = int(dead.rowcount or 0) + int(requeued.rowcount or 0)
    if recovered:
        session.commit()
        logger.warning("Recovered %d stale background job(s)", recovered)
    else:
        session.rollback()
    return recovered


def _execute(session: Session, job: BackgroundJob, worker_id: str) -> BackgroundJob:
    """Run the claimed job's handler and record the outcome."""
    job_id = job.id
    handler = JOB_HANDLERS.get(job.job_type)
    if handler is None:
        job.status = "dead"
        job.next_attempt_at = None
        job.last_error = f"No handler registered for job type '{job.job_type}'"
        if job.job_type == "workspace_build":
            workspace_id = (job.payload or {}).get("workspace_id")
            if workspace_id:
                _reconcile_workspace_build(session, workspace_id, "no handler for build job")
        session.commit()
        return job
    try:
        handler(session, job)
    except Exception as exc:  # noqa: BLE001 — every failure must land on the job row
        logger.exception("Background job %s (%s) attempt %d failed", job_id, job.job_type, job.attempts)
        session.rollback()
        job = session.get(BackgroundJob, job_id)
        return fail(session, job, str(exc) or exc.__class__.__name__, worker_id)
    return complete(session, job, worker_id)


def process_one(
    session: Session,
    worker_id: str,
    job_types: list[str] | None = None,
) -> BackgroundJob | None:
    """Claim and execute one due job; returns None when nothing was claimable."""
    job = claim_next(session, worker_id, job_types)
    if job is None:
        return None
    return _execute(session, job, worker_id)


def drain_job(session: Session, job_id: str, worker_id: str | None = None) -> BackgroundJob | None:
    """Claim and run one specific queued job through the same at-least-once path.

    Used by the API's in-process fallback: single-process deployments (and tests) finish
    builds without a separate worker, while a concurrent worker that already claimed the
    job simply wins the race and this becomes a no-op.
    """
    worker_id = worker_id or f"inline-{new_uuid()[:8]}"
    job = _claim_job(session, job_id, worker_id)
    if job is None:
        return None
    return _execute(session, job, worker_id)


def drain_job_in_new_session(job_id: str) -> None:
    """BackgroundTasks entry point: drains with a session independent of the request's."""
    from src.db.session import SessionLocal

    with SessionLocal() as session:
        drain_job(session, job_id)
