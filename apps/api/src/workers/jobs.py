"""Database-backed background job worker (workspace builds and future job types).

Run continuously with ``python -m src.workers.jobs`` or use ``--once`` from a scheduler.
Each batch first requeues stale claims left by crashed workers, then claims and executes
due jobs one at a time through the atomic at-least-once path in ``job_service``.
"""
from __future__ import annotations

import argparse
import logging
import os
import socket
import time

from src.config import settings
from src.db.base import new_uuid
from src.db.session import SessionLocal, prepare_schema
from src.observability import configure_logging, reset_request_id, set_request_id
from src.services.job_service import process_one, recover_stale

logger = logging.getLogger("deallens.jobs")

WORKER_ID = f"{socket.gethostname()}-{os.getpid()}-{new_uuid()[:8]}"


def run_batch(limit: int, stale_seconds: int) -> int:
    with SessionLocal() as session:
        recover_stale(session, older_than_seconds=stale_seconds)
        processed = 0
        for _ in range(limit):
            # Bind a per-job correlation id so structured worker logs are traceable end-to-end.
            token = set_request_id(f"job-{new_uuid()[:12]}")
            try:
                job = process_one(session, WORKER_ID)
                if job is None:
                    break
                processed += 1
                logger.info(
                    "Job %s (%s) attempt %d -> %s",
                    job.id, job.job_type, job.attempts, job.status,
                )
            finally:
                reset_request_id(token)
        return processed


def main() -> None:
    parser = argparse.ArgumentParser(description="Run queued DealLens background jobs")
    parser.add_argument("--once", action="store_true", help="Process one batch and exit")
    parser.add_argument("--limit", type=int, default=20, help="Maximum jobs per batch")
    parser.add_argument("--interval", type=float, default=5.0, help="Idle polling interval in seconds")
    parser.add_argument(
        "--stale-seconds",
        type=int,
        default=300,
        help="Requeue running jobs whose heartbeat is older than this",
    )
    args = parser.parse_args()
    if not 1 <= args.limit <= 1_000:
        parser.error("--limit must be between 1 and 1000")
    if args.interval < 0.25:
        parser.error("--interval must be at least 0.25 seconds")
    if args.stale_seconds < 30:
        parser.error("--stale-seconds must be at least 30")

    logging.basicConfig(level=logging.INFO)
    configure_logging(settings.json_logs)
    prepare_schema()
    while True:
        try:
            processed = run_batch(args.limit, args.stale_seconds)
            if processed:
                logger.info("Processed %d background job(s)", processed)
        except Exception:
            logger.exception("Job batch failed")
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
