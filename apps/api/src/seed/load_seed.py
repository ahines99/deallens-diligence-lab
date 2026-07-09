"""Seed demo workspaces from REAL SEC data (multi-sector), so the app opens fully populated.

Run directly:  python -m src.seed.load_seed
Requires network access to SEC EDGAR. Each demo ingests a company + peers and runs the full analysis.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.session import SessionLocal, init_db
from src.models import Workspace
from src.schemas.workspace import WorkspaceCreate
from src.services import analysis_service, financial_benchmark_service, workspace_service

logger = logging.getLogger("deallens.seed")

# (target ticker, deal_type, peer tickers) — a multi-sector demo set.
DEMOS = [
    ("MSFT", "public_equity", ["GOOGL", "ORCL", "CRM"]),
    ("CRWD", "software_platform", ["PANW", "ZS", "S"]),
]


def seed_demo(session: Session, ticker: str, deal_type: str, peers: list[str]) -> Workspace:
    ws = workspace_service.create_workspace(
        session, WorkspaceCreate(ticker=ticker, deal_type=deal_type)
    )
    if peers:
        financial_benchmark_service.add_comps_by_ticker(session, ws.id, peers)
        session.commit()
        analysis_service.run_full_analysis(session, ws.id)
    session.refresh(ws)
    return ws


def seed_all_if_empty(session: Session) -> list[str]:
    if session.scalar(select(Workspace)) is not None:
        return []
    created: list[str] = []
    for ticker, deal_type, peers in DEMOS:
        try:
            ws = seed_demo(session, ticker, deal_type, peers)
            created.append(ws.id)
            logger.info("Seeded demo workspace for %s: %s", ticker, ws.id)
        except Exception as exc:  # pragma: no cover - network dependent
            logger.warning("Failed to seed %s: %s", ticker, exc)
    return created


def main() -> None:
    init_db()
    with SessionLocal() as session:
        existing = session.scalar(select(Workspace))
        if existing is not None:
            print(f"Workspaces already present (e.g. {existing.id}); nothing to seed.")
            return
        ids = seed_all_if_empty(session)
        if ids:
            print(f"Seeded {len(ids)} demo workspace(s) from live SEC data: {', '.join(ids)}")
        else:
            print("No workspaces seeded (network unavailable?).")


if __name__ == "__main__":
    main()
