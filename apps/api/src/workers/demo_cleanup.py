"""Retention cleanup for the public demo sandbox.

Run once from a scheduler (``python -m src.workers.demo_cleanup --once``) or continuously.
Only demo-sandbox data and expired sessions are touched; real organizations are never scanned.
"""
from __future__ import annotations

import argparse
import logging
import time

from src.db.session import SessionLocal, prepare_schema
from src.services.demo_service import purge_expired_demo_data

logger = logging.getLogger("deallens.demo_cleanup")

DEFAULT_INTERVAL_SECONDS = 3600.0


def run_once() -> dict:
    with SessionLocal() as session:
        return purge_expired_demo_data(session)


def main() -> None:
    parser = argparse.ArgumentParser(description="Purge expired DealLens demo data")
    parser.add_argument("--once", action="store_true", help="Run one purge and exit")
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL_SECONDS,
        help="Seconds between purges when running continuously",
    )
    args = parser.parse_args()
    if args.interval < 60:
        parser.error("--interval must be at least 60 seconds")

    logging.basicConfig(level=logging.INFO)
    prepare_schema()
    while True:
        try:
            counts = run_once()
            if any(counts.values()):
                logger.info("Purged demo data: %s", counts)
        except Exception:
            logger.exception("Demo cleanup failed")
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
