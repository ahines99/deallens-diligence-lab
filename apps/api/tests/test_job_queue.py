"""Durable job queue (G31): at-least-once claims, crash recovery, retry/backoff, and the
end-to-end workspace build path through the DB-backed queue.

Runs fully offline: handlers are registered per-test and the build test monkeypatches the
network-bound EDGAR stages (same pattern as test_workspace_build.py).
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from src.db.base import now_utc
from src.models.job import BackgroundJob
from src.services import job_service, workspace_service


FAKE_INFO = {"cik": "0000000000", "ticker": "FAKE", "name": "Fake Example Corp"}


@pytest.fixture()
def db_session(client):
    """A service-level session against the app's test database (client boots the schema)."""
    from src.db.session import SessionLocal

    with SessionLocal() as session:
        yield session


@pytest.fixture()
def offline_build(monkeypatch):
    """Patch the network-bound stages so a ticker build succeeds deterministically."""
    from src.services import analysis_service, sec_ingestion_service

    monkeypatch.setattr(
        workspace_service.edgar_client, "resolve_ticker", lambda ticker: FAKE_INFO
    )

    def fake_ingest(session, workspace_id, ticker, filing_limit=8, progress=None):
        if progress is not None:
            progress("resolving_company")
            progress("fetching_financials")
        return None

    monkeypatch.setattr(sec_ingestion_service, "ingest_company", fake_ingest)
    monkeypatch.setattr(
        analysis_service, "run_full_analysis", lambda session, ws_id, heartbeat=None: None
    )


# ---------------------------------------------------------------- at-least-once claiming


def test_two_workers_claim_exactly_once(client):
    """Two claim attempts race for one job; the atomic UPDATE lets exactly one win."""
    from src.db.session import SessionLocal

    with SessionLocal() as session_a, SessionLocal() as session_b:
        job = job_service.enqueue(session_a, "claim_race", {})
        first = job_service.claim_next(session_a, "worker-a", ["claim_race"])
        second = job_service.claim_next(session_b, "worker-b", ["claim_race"])

        assert first is not None and first.id == job.id
        assert first.status == "running"
        assert first.claimed_by == "worker-a"
        assert first.attempts == 1
        assert first.heartbeat_at is not None
        assert second is None


def test_enqueue_dedupes_active_jobs_for_same_workspace(db_session, monkeypatch):
    monkeypatch.setitem(job_service.JOB_HANDLERS, "dedup_demo", lambda session, job: None)
    first = job_service.enqueue(db_session, "dedup_demo", {"workspace_id": "ws-dedup"})
    duplicate = job_service.enqueue(db_session, "dedup_demo", {"workspace_id": "ws-dedup"})
    assert duplicate.id == first.id

    done = job_service.process_one(db_session, "worker-a", ["dedup_demo"])
    assert done is not None and done.status == "succeeded"
    # Once the job is terminal, a new enqueue creates a fresh row.
    fresh = job_service.enqueue(db_session, "dedup_demo", {"workspace_id": "ws-dedup"})
    assert fresh.id != first.id


# ------------------------------------------------------------------------ crash recovery


def test_stale_claim_is_recovered_and_reprocessed(db_session, monkeypatch):
    """A job claimed by a crashed worker (stale heartbeat) is requeued and completes."""
    handled: list[str] = []
    monkeypatch.setitem(
        job_service.JOB_HANDLERS, "crash_demo", lambda session, job: handled.append(job.id)
    )
    job = job_service.enqueue(db_session, "crash_demo", {})
    claimed = job_service.claim_next(db_session, "worker-crashed", ["crash_demo"])
    assert claimed is not None and claimed.id == job.id

    # A live claim is not recoverable, and running jobs are not claimable by others.
    assert job_service.recover_stale(db_session, older_than_seconds=600) == 0
    assert job_service.claim_next(db_session, "worker-b", ["crash_demo"]) is None

    # Simulate the crash: the worker stopped heartbeating long ago.
    claimed.heartbeat_at = now_utc() - timedelta(hours=1)
    db_session.commit()

    assert job_service.recover_stale(db_session, older_than_seconds=600) == 1
    result = job_service.process_one(db_session, "worker-b", ["crash_demo"])
    assert result is not None and result.id == job.id
    assert result.status == "succeeded"
    assert result.claimed_by == "worker-b"
    assert result.attempts == 2  # the crashed attempt still counted
    assert handled == [job.id]


def test_stale_claim_on_final_attempt_goes_dead(db_session):
    job = job_service.enqueue(db_session, "crash_dead_demo", {}, max_attempts=1)
    claimed = job_service.claim_next(db_session, "worker-crashed", ["crash_dead_demo"])
    assert claimed is not None
    claimed.heartbeat_at = now_utc() - timedelta(hours=1)
    db_session.commit()

    assert job_service.recover_stale(db_session, older_than_seconds=600) == 1
    db_session.refresh(job)
    assert job.status == "dead"
    assert "stale" in (job.last_error or "")


# ------------------------------------------------------------------------- retry/backoff


def test_failed_attempt_backs_off_before_retry(db_session, monkeypatch):
    def boom(session, job):
        raise RuntimeError("transient outage")

    monkeypatch.setitem(job_service.JOB_HANDLERS, "backoff_demo", boom)
    job_service.enqueue(db_session, "backoff_demo", {})
    failed = job_service.process_one(db_session, "worker-a", ["backoff_demo"])
    assert failed is not None
    assert failed.status == "failed"
    assert "transient outage" in failed.last_error
    assert failed.next_attempt_at > now_utc()  # backoff scheduled in the future
    # Not due yet, so it cannot be claimed again immediately.
    assert job_service.claim_next(db_session, "worker-a", ["backoff_demo"]) is None


def test_transient_failures_retry_until_success(db_session, monkeypatch):
    monkeypatch.setattr(job_service, "_RETRY_BASE_SECONDS", 0)
    calls = {"count": 0}

    def flaky(session, job):
        calls["count"] += 1
        if calls["count"] < 3:
            raise RuntimeError(f"flake #{calls['count']}")

    monkeypatch.setitem(job_service.JOB_HANDLERS, "flaky_demo", flaky)
    job_service.enqueue(db_session, "flaky_demo", {})

    # process_one returns the same identity-mapped row; snapshot the status per attempt.
    statuses = []
    final = None
    for _ in range(3):
        final = job_service.process_one(db_session, "worker-a", ["flaky_demo"])
        statuses.append(final.status)
    assert statuses == ["failed", "failed", "succeeded"]
    assert final.attempts == 3
    assert final.last_error is None
    assert calls["count"] == 3


def test_exhausted_attempts_mark_job_dead_with_last_error(db_session, monkeypatch):
    monkeypatch.setattr(job_service, "_RETRY_BASE_SECONDS", 0)

    def always_fails(session, job):
        raise RuntimeError("permanently broken")

    monkeypatch.setitem(job_service.JOB_HANDLERS, "doomed_demo", always_fails)
    job_service.enqueue(db_session, "doomed_demo", {}, max_attempts=3)

    statuses = []
    dead = None
    for _ in range(3):
        dead = job_service.process_one(db_session, "worker-a", ["doomed_demo"])
        statuses.append(dead.status)
    assert statuses == ["failed", "failed", "dead"]
    assert dead.attempts == 3
    assert "permanently broken" in dead.last_error
    # Dead jobs are terminal: nothing further is claimable.
    assert job_service.process_one(db_session, "worker-a", ["doomed_demo"]) is None


def test_unknown_job_type_goes_dead(db_session):
    job_service.enqueue(db_session, "no_such_handler", {})
    result = job_service.process_one(db_session, "worker-a", ["no_such_handler"])
    assert result is not None
    assert result.status == "dead"
    assert "No handler registered" in result.last_error


# ------------------------------------------------------------- end-to-end workspace build


def test_workspace_build_completes_through_job_queue(client, offline_build):
    resp = client.post("/api/workspaces", json={"ticker": "FAKE", "deal_type": "public_equity"})
    assert resp.status_code == 201, resp.text
    workspace_id = resp.json()["id"]

    # TestClient runs the drain background task before returning, so the build is done.
    status = client.get(f"/api/workspaces/{workspace_id}/build-status").json()
    assert status == {
        "workspace_id": workspace_id,
        "status": "ready",
        "step": None,
        "error": None,
        "ticker": "FAKE",
    }

    from src.db.session import SessionLocal

    with SessionLocal() as session:
        jobs = [
            job
            for job in session.scalars(
                select(BackgroundJob).where(BackgroundJob.job_type == "workspace_build")
            )
            if (job.payload or {}).get("workspace_id") == workspace_id
        ]
    assert len(jobs) == 1
    job = jobs[0]
    assert job.status == "succeeded"
    assert job.attempts == 1
    assert job.claimed_by is not None
    assert job.heartbeat_at is not None  # progress callbacks heartbeat during the build
    assert job.last_error is None


def test_heartbeat_never_commits_shared_build_session_work(db_session, offline_build, monkeypatch):
    """Audit H4: a liveness ping mid-build must not make half-done analysis work durable.

    The handler's heartbeat previously committed the SHARED build session, so e.g. the
    projection deletes at the start of run_full_analysis became permanent before the LLM
    stages ran — a later stage failure then left a workspace with no risks/memo/plan.
    The beat now runs on its own session: uncommitted work stays invisible to other
    sessions and rolls back cleanly when a later stage fails.
    """
    from src.db.session import SessionLocal
    from src.models.workspace import Workspace

    ws = workspace_service.create_workspace(
        db_session,
        workspace_service.WorkspaceCreate(ticker="FAKE", deal_type="public_equity"),
        defer_build=True,
    )
    job = job_service.enqueue(db_session, "workspace_build", {"workspace_id": ws.id})
    observed: dict = {}

    def build_fails_after_beat(session, workspace_id, heartbeat=None):
        # Stand-in for the projection-replacement stage: uncommitted work on the shared
        # session, a liveness ping, then a failing later stage.
        session.get(Workspace, workspace_id).build_error = "uncommitted-probe"
        heartbeat()
        with SessionLocal() as other:
            observed["mutation_after_beat"] = other.get(Workspace, workspace_id).build_error
            observed["beat_landed"] = other.get(BackgroundJob, job.id).heartbeat_at is not None
        raise RuntimeError("stage failed after heartbeat")

    monkeypatch.setattr(workspace_service, "run_build", build_fails_after_beat)
    job_service.drain_job(db_session, job.id)

    assert observed["mutation_after_beat"] is None, "heartbeat committed the shared session"
    assert observed["beat_landed"]
    db_session.expire_all()
    # The failed attempt rolled back completely and the job is queued for retry.
    assert db_session.get(Workspace, ws.id).build_error is None
    assert db_session.get(BackgroundJob, job.id).status == "failed"


def test_worker_batch_processes_queued_jobs(db_session, monkeypatch):
    """The worker entry point recovers stale claims, then drains due jobs."""
    from src.workers import jobs as jobs_worker

    handled: list[str] = []
    monkeypatch.setitem(
        job_service.JOB_HANDLERS, "worker_demo", lambda session, job: handled.append(job.id)
    )
    job = job_service.enqueue(db_session, "worker_demo", {})

    processed = jobs_worker.run_batch(limit=10, stale_seconds=300)
    assert processed >= 1
    db_session.refresh(job)
    assert job.status == "succeeded"
    assert handled == [job.id]


def test_dead_build_job_unsticks_the_workspace(db_session, offline_build, monkeypatch):
    """H4 regression: an infra failure that escapes run_build and exhausts retries must not
    strand the workspace in 'building' forever — it should become 'failed' so retry works."""
    ws = workspace_service.create_workspace(
        db_session,
        workspace_service.WorkspaceCreate(ticker="FAKE", deal_type="public_equity"),
        defer_build=True,
    )
    monkeypatch.setattr(job_service, "_RETRY_BASE_SECONDS", 0)  # no backoff between attempts

    # run_build itself raising simulates a crash-equivalent (e.g. a DB error before its own
    # try/except) — the exception propagates out of the handler and drives job retry.
    def crash(session, workspace_id, heartbeat=None):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(workspace_service, "run_build", crash)
    job = job_service.enqueue(db_session, "workspace_build", {"workspace_id": ws.id})
    for _ in range(job.max_attempts):
        job_service.drain_job(db_session, job.id)

    db_session.expire_all()
    assert db_session.get(BackgroundJob, job.id).status == "dead"
    status = workspace_service.get_build_status(db_session, ws.id)
    assert status["status"] == "failed"  # not stuck 'building'
    assert "exhausted" in (status["error"] or "").lower()
    # And the workspace is now retryable rather than 409-locked.
    workspace_service.retry_build(db_session, ws.id)


def test_stale_recovery_of_dead_build_reconciles_workspace(db_session, offline_build):
    """H4: a build worker killed on its final attempt (no exception, just gone) leaves the
    job 'running'; recover_stale marks it dead AND unsticks the workspace."""
    ws = workspace_service.create_workspace(
        db_session,
        workspace_service.WorkspaceCreate(ticker="FAKE", deal_type="public_equity"),
        defer_build=True,
    )
    job = job_service.enqueue(db_session, "workspace_build", {"workspace_id": ws.id})
    # Simulate a worker that claimed the final attempt then vanished with a stale heartbeat.
    job.status = "running"
    job.attempts = job.max_attempts
    job.claimed_by = "ghost-worker"
    job.heartbeat_at = now_utc() - timedelta(hours=1)
    db_session.commit()

    recovered = job_service.recover_stale(db_session, older_than_seconds=1)
    assert recovered == 1
    db_session.expire_all()  # the bulk UPDATE used synchronize_session=False
    assert db_session.get(BackgroundJob, job.id).status == "dead"
    assert workspace_service.get_build_status(db_session, ws.id)["status"] == "failed"


def test_superseded_worker_cannot_complete_a_reclaimed_job(db_session):
    """H5: after a stale job is requeued and reclaimed, the original worker's late compl()
    must not overwrite the new owner's run."""
    def noop(session, job):
        return None

    job_service.JOB_HANDLERS["ownership_demo"] = noop
    try:
        job = job_service.enqueue(db_session, "ownership_demo", {})
        claimed = job_service._claim_job(db_session, job.id, "worker-A")
        assert claimed is not None
        # Worker B reclaims the same row (simulating post-recovery ownership change).
        claimed.claimed_by = "worker-B"
        db_session.commit()
        # Worker A's late completion is rejected; the row still belongs to B and is running.
        job_service.complete(db_session, claimed, worker_id="worker-A")
        current = db_session.get(BackgroundJob, job.id)
        assert current.status == "running"
        assert current.claimed_by == "worker-B"
    finally:
        job_service.JOB_HANDLERS.pop("ownership_demo", None)
