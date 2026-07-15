"""Scheduled watchlist refresh worker (G19).

Run once from a scheduler (``python -m src.workers.watchlist_refresh --once``) or continuously.
Each pass reads EDGAR submissions for every active watchlist entry across all organizations,
detects new filings since the per-entry dedup cursor, and emits outbox events (notifications +
webhooks) for each one.
"""
from __future__ import annotations

import argparse
import logging
import time

from src.db.session import SessionLocal, prepare_schema
from src.services.watchlist_service import refresh_all

logger = logging.getLogger("deallens.watchlist_refresh")

DEFAULT_INTERVAL_SECONDS = 900.0


def run_once() -> dict:
    with SessionLocal() as session:
        return refresh_all(session)


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh DealLens watchlists for new SEC filings")
    parser.add_argument("--once", action="store_true", help="Run one refresh and exit")
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL_SECONDS,
        help="Seconds between refreshes when running continuously",
    )
    args = parser.parse_args()
    if args.interval < 60:
        parser.error("--interval must be at least 60 seconds")

    logging.basicConfig(level=logging.INFO)
    prepare_schema()
    while True:
        try:
            totals = run_once()
            if totals.get("events_emitted"):
                logger.info("Watchlist refresh emitted %d filing event(s)", totals["events_emitted"])
        except Exception:
            logger.exception("Watchlist refresh failed")
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
