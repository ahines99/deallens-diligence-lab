"""Database-backed outbound webhook worker.

Run continuously with ``python -m src.workers.webhooks`` or use ``--once`` from a scheduler.
"""
from __future__ import annotations

import argparse
import logging
import time

from src.db.session import SessionLocal, prepare_schema
from src.services.webhook_service import process_pending

logger = logging.getLogger("deallens.webhooks")


def run_batch(limit: int) -> int:
    with SessionLocal() as session:
        deliveries = process_pending(session, limit=limit)
        return len(deliveries)


def main() -> None:
    parser = argparse.ArgumentParser(description="Deliver queued DealLens webhooks")
    parser.add_argument("--once", action="store_true", help="Process one batch and exit")
    parser.add_argument("--limit", type=int, default=100, help="Maximum deliveries per batch")
    parser.add_argument("--interval", type=float, default=5.0, help="Idle polling interval in seconds")
    args = parser.parse_args()
    if not 1 <= args.limit <= 1_000:
        parser.error("--limit must be between 1 and 1000")
    if args.interval < 0.25:
        parser.error("--interval must be at least 0.25 seconds")

    logging.basicConfig(level=logging.INFO)
    prepare_schema()
    while True:
        try:
            processed = run_batch(args.limit)
            if processed:
                logger.info("Processed %d webhook delivery(s)", processed)
        except Exception:
            logger.exception("Webhook batch failed")
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
