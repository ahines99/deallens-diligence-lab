"""DealLens Diligence Lab — FastAPI application entrypoint."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.config import settings
from src.db.session import SessionLocal, engine, init_db
from src.routers import (
    comps,
    evidence,
    feeds,
    filings,
    financials,
    forensics,
    govcon,
    memos,
    questions,
    red_team,
    risks,
    sec,
    signals,
    targets,
    valuation,
    workspaces,
)
from src.services.common import NotFound

logger = logging.getLogger("deallens")
logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if settings.auto_seed:
        from src.seed.load_seed import seed_all_if_empty

        try:
            with SessionLocal() as session:
                created = seed_all_if_empty(session)
                if created:
                    logger.info("Auto-seeded %d demo workspace(s) from live SEC data", len(created))
        except Exception as exc:  # pragma: no cover - network dependent
            logger.warning("Auto-seed skipped: %s", exc)
    yield


app = FastAPI(
    title="DealLens Diligence Lab API",
    version="0.1.0",
    description=(
        "Public-data AI diligence copilot. Runs in mock mode by default (no API key). "
        "Outputs are drafts for human review and are NOT investment advice."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(NotFound)
async def not_found_handler(request: Request, exc: NotFound) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": exc.message})


@app.get("/api/health", tags=["health"])
def health() -> dict:
    return {
        "status": "ok",
        "llm_mode": "mock" if settings.is_mock else "live",
        "database": "sqlite" if settings.is_sqlite else engine.dialect.name,
    }


for module in (
    workspaces, targets, sec, filings, comps, financials, risks, questions,
    memos, red_team, evidence, govcon,
    forensics, valuation, feeds, signals,
):
    app.include_router(module.router)
