"""DealLens Diligence Lab — FastAPI application entrypoint."""
from __future__ import annotations

import hmac
import logging
import re
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select, text

from src.config import settings
from src.db.session import SessionLocal, engine, prepare_schema
from src.models.deal_workflow import Deal
from src.models.workspace import Workspace
from src.routers import (
    activity,
    comps,
    deal_intelligence,
    deal_workflow,
    evidence,
    examples,
    feeds,
    filings,
    financials,
    forensics,
    governance,
    govcon,
    identity,
    integrations,
    memos,
    notifications,
    ownership,
    portfolio,
    questions,
    red_team,
    risks,
    sec,
    signals,
    targets,
    underwriting_data,
    underwriting_model,
    valuation,
    workspaces,
)
from src.services.common import NotFound
from src.services import identity_service
from src.schemas.identity import PrincipalContext

logger = logging.getLogger("deallens")
logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    prepare_schema()
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
        "Private-equity underwriting, diligence, evidence, and IC governance workbench. "
        "Public targets can enrich the workflow with live SEC data. "
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

_WORKSPACE_PATH = re.compile(r"^/api/workspaces/([a-zA-Z0-9_-]+)")
_PUBLIC_PATHS = {
    "/api/health",
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/demo",
    "/docs",
    "/openapi.json",
    "/redoc",
}
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_VIEWER_SELF_SERVICE_PATHS = {"/api/auth/logout", "/api/auth/switch-organization"}
_RATE_LIMITED_AUTH_PATHS = {"/api/auth/login", "/api/auth/register", "/api/auth/demo"}
# SEC-bound endpoints throttled per client IP when DEMO_MODE is on, so a public demo
# box cannot be driven past EDGAR's fair-access guidance by one visitor.
_DEMO_THROTTLED_BUILD_PATHS = re.compile(
    r"^/api/(?:workspaces$|workspaces/[a-zA-Z0-9_-]+/(?:build/retry|refresh)$|sec/ingest$)"
)


class _AuthRateLimiter:
    """Bound the expensive password-hash work performed by public auth endpoints.

    The Compose deployment runs one API process, so an in-process limiter is immediately effective.
    Multi-replica deployments should retain this guard and add a shared edge/Redis limiter.
    """

    def __init__(self) -> None:
        self._attempts: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> int | None:
        now = time.monotonic()
        window = max(settings.auth_rate_limit_window_seconds, 1)
        limit = max(settings.auth_rate_limit_attempts, 1)
        with self._lock:
            attempts = self._attempts[key]
            while attempts and attempts[0] <= now - window:
                attempts.popleft()
            if len(attempts) >= limit:
                return max(1, round(window - (now - attempts[0])))
            attempts.append(now)
            if len(self._attempts) > 10_000:
                for stale_key in [
                    item_key
                    for item_key, values in self._attempts.items()
                    if not values or values[-1] <= now - window
                ]:
                    self._attempts.pop(stale_key, None)
            return None

    def clear(self) -> None:
        with self._lock:
            self._attempts.clear()


_auth_rate_limiter = _AuthRateLimiter()


class _DemoBuildRateLimiter:
    """Per-IP hourly cap on SEC-bound build endpoints, active only in DEMO_MODE."""

    def __init__(self) -> None:
        self._builds: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> int | None:
        now = time.monotonic()
        window = 3600.0
        limit = max(settings.demo_builds_per_hour, 1)
        with self._lock:
            builds = self._builds[key]
            while builds and builds[0] <= now - window:
                builds.popleft()
            if len(builds) >= limit:
                return max(1, round(window - (now - builds[0])))
            builds.append(now)
            return None

    def clear(self) -> None:
        with self._lock:
            self._builds.clear()


_demo_build_rate_limiter = _DemoBuildRateLimiter()


def _service_principal(request: Request, supplied: str) -> PrincipalContext | None:
    """Authenticate the optional trusted-service credential used by automation.

    Human callers use revocable ``dls_`` sessions. This compatibility path only accepts actor
    headers after the caller proves possession of the configured internal service secret.
    """
    if not settings.internal_api_token or not hmac.compare_digest(
        supplied, settings.internal_api_token
    ):
        return None
    actor_id = request.headers.get("X-Actor-ID")
    organization_id = request.headers.get("X-Organization-ID")
    if not actor_id or not organization_id:
        return None
    header_roles = request.headers.get("X-Actor-Roles", "")
    roles = {item.strip() for item in header_roles.split(",") if item.strip()}
    role = "admin" if roles & {"owner", "admin", "organization_admin"} else "member"
    return PrincipalContext(
        user_id=actor_id,
        session_id="trusted-service",
        email=f"{actor_id}@trusted-service.invalid",
        display_name=request.headers.get("X-Actor-Name") or actor_id,
        organization_id=organization_id,
        membership_id="trusted-service",
        role=role,
    )


@app.middleware("http")
async def identity_and_tenant_guard(request: Request, call_next):
    """Resolve server-verified identity and enforce workspace tenant boundaries."""
    path = request.url.path
    request.state.principal = None
    if request.method == "POST" and path in _RATE_LIMITED_AUTH_PATHS:
        client_host = request.client.host if request.client else "unknown"
        retry_after = _auth_rate_limiter.check(f"{path}:{client_host}")
        if retry_after is not None:
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many authentication attempts; retry later"},
                headers={"Retry-After": str(retry_after)},
            )
    if (
        settings.demo_mode
        and request.method == "POST"
        and _DEMO_THROTTLED_BUILD_PATHS.match(path)
    ):
        client_host = request.client.host if request.client else "unknown"
        retry_after = _demo_build_rate_limiter.check(client_host)
        if retry_after is not None:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": (
                        "Demo build limit reached for this hour; existing workspaces stay "
                        "fully explorable while the limit resets"
                    )
                },
                headers={"Retry-After": str(retry_after)},
            )
    authorization = request.headers.get("Authorization", "")
    supplied = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""

    if supplied:
        if supplied.startswith("dls_"):
            with SessionLocal() as session:
                try:
                    request.state.principal = identity_service.authenticate_token(session, supplied)
                except identity_service.IdentityError as exc:
                    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})
        else:
            request.state.principal = _service_principal(request, supplied)
            if request.state.principal is None:
                return JSONResponse(status_code=401, content={"detail": "Invalid authentication"})

    is_public = path in _PUBLIC_PATHS or request.method == "OPTIONS"
    if settings.auth_required and not is_public and request.state.principal is None:
        return JSONResponse(status_code=401, content={"detail": "Authenticated actor required"})

    principal = request.state.principal
    if (
        principal is not None
        and principal.role == "viewer"
        and request.method not in _SAFE_METHODS
        and path not in _VIEWER_SELF_SERVICE_PATHS
    ):
        return JSONResponse(
            status_code=403,
            content={"detail": "Viewer memberships are read-only"},
        )

    match = _WORKSPACE_PATH.match(path)
    if match:
        # Legacy development headers remain useful only while authentication is explicitly off.
        organization_id = (
            principal.organization_id
            if principal is not None
            else request.headers.get("X-Organization-ID") if not settings.auth_required else None
        )
        if organization_id:
            workspace_id = match.group(1)
            with SessionLocal() as session:
                workspace_org = session.scalar(
                    select(Workspace.organization_id).where(Workspace.id == workspace_id)
                )
                linked_org = session.scalar(
                    select(Deal.organization_id).where(Deal.workspace_id == workspace_id)
                )
            effective_org = workspace_org or linked_org
            # Authenticated users cannot see unowned legacy workspaces or another tenant's data.
            if (principal is not None and effective_org != organization_id) or (
                principal is None and effective_org and effective_org != organization_id
            ):
                return JSONResponse(status_code=404, content={"detail": "Workspace not found"})
    return await call_next(request)


@app.middleware("http")
async def versioned_api_alias(request: Request, call_next):
    """Expose the stable Wave 3 contract under `/api/v1` while preserving legacy `/api` clients."""
    versioned = request.scope["path"] == "/api/v1" or request.scope["path"].startswith("/api/v1/")
    if versioned:
        suffix = request.scope["path"][len("/api/v1"):]
        rewritten = "/api" + (suffix or "")
        request.scope["path"] = rewritten
        request.scope["raw_path"] = rewritten.encode("utf-8")
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
    )
    if versioned:
        response.headers["X-DealLens-API-Version"] = "1"
    return response


@app.exception_handler(NotFound)
async def not_found_handler(request: Request, exc: NotFound) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": exc.message})


@app.get("/api/health", tags=["health"])
def health() -> dict:
    with SessionLocal() as session:
        session.execute(text("SELECT 1")).scalar_one()
    return {
        "status": "ok",
        "llm_mode": "mock" if settings.is_mock else "live",
        "database": "sqlite" if settings.is_sqlite else engine.dialect.name,
        "database_status": "ready",
        "schema_management": settings.schema_management,
        "demo_mode": settings.demo_mode,
    }


for module in (
    activity, workspaces, targets, sec, filings, comps, financials, risks, questions,
    memos, red_team, evidence, examples, governance, govcon, portfolio, notifications,
    forensics, valuation, feeds, signals, ownership,
    underwriting_data, underwriting_model, deal_workflow, deal_intelligence,
    integrations, identity,
):
    app.include_router(module.router)
