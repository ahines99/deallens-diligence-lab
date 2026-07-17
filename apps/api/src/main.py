"""DealLens Diligence Lab — FastAPI application entrypoint."""
from __future__ import annotations

import hmac
import logging
import re
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select, text

from src.config import settings
from src.observability import (
    CONTENT_TYPE_LATEST,
    METRICS,
    PathTemplateMatcher,
    configure_logging,
    new_request_id,
    reset_request_id,
    set_request_id,
)
from src.db.session import SessionLocal, engine, prepare_schema
from src.models.deal_workflow import Deal
from src.models.workspace import Workspace
from src.routers import (
    activity,
    agent,
    api_keys,
    collaboration,
    comments,
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
    memo_redline,
    memos,
    model_ops,
    notifications,
    ownership,
    portfolio,
    questions,
    quotas,
    red_team,
    risks,
    search,
    sec,
    share_links,
    signals,
    targets,
    underwriting_data,
    underwriting_model,
    valuation,
    watchlist,
    workspace_bundle,
    workspaces,
)
from src.services.common import NotFound
from src.services import api_key_service, identity_service
from src.schemas.identity import PrincipalContext

logger = logging.getLogger("deallens")
logging.basicConfig(level=logging.INFO)
configure_logging(settings.json_logs)


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
# NOTE: CORSMiddleware is registered at the BOTTOM of this module so it wraps every other
# middleware — early returns from the auth/tenant/quota guards must still carry CORS headers.

_WORKSPACE_PATH = re.compile(r"^/api/workspaces/([a-zA-Z0-9_-]+)")
_PUBLIC_PATHS = {
    "/api/health",
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/demo",
    # OIDC SSO (G48): the login redirect and IdP callback are pre-session (the callback IS the
    # authentication). They 404 on their own when OIDC_ENABLED=false.
    "/api/auth/oidc/login",
    "/api/auth/oidc/callback",
    "/docs",
    "/openapi.json",
    "/redoc",
    # Observability scrape target: unauthenticated so Prometheus can poll it.
    "/metrics",
}
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_VIEWER_SELF_SERVICE_PATHS = {"/api/auth/logout", "/api/auth/switch-organization"}
_RATE_LIMITED_AUTH_PATHS = {"/api/auth/login", "/api/auth/register", "/api/auth/demo"}
# The OIDC endpoints are GETs but still unauthenticated auth surface: /login mints server-side
# state entries and /callback performs an outbound token exchange — both must be throttled.
_RATE_LIMITED_AUTH_GET_PATHS = {"/api/auth/oidc/login", "/api/auth/oidc/callback"}
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
            if len(self._builds) > 10_000:
                # Same stale-key eviction as _AuthRateLimiter: per-key deques are pruned above,
                # but abandoned keys would otherwise accumulate for the life of the process.
                for stale_key in [
                    item_key
                    for item_key, values in self._builds.items()
                    if not values or values[-1] <= now - window
                ]:
                    self._builds.pop(stale_key, None)
            return None

    def clear(self) -> None:
        with self._lock:
            self._builds.clear()


_demo_build_rate_limiter = _DemoBuildRateLimiter()


class _OrgQuotaLimiter:
    """Per-organization sliding-window quota buckets (G39; generalizes _DemoBuildRateLimiter).

    Keyed by ``(organization_id, bucket)`` so each tenant's usage is isolated. ``check`` records a
    hit and returns ``None`` when under the limit or the Retry-After seconds when over. A ``limit``
    of ``0`` (or less) means *unlimited*: nothing is recorded and nothing is ever throttled. As with
    the other in-process limiters, the Compose deployment runs a single API process so this is
    immediately effective; multi-replica deployments should add a shared edge/Redis limiter.
    """

    def __init__(self) -> None:
        self._events: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(
        self, organization_id: str, bucket: str, *, limit: int, window_seconds: float
    ) -> int | None:
        if limit <= 0:
            return None
        now = time.monotonic()
        window = max(window_seconds, 1.0)
        with self._lock:
            events = self._events[(organization_id, bucket)]
            while events and events[0] <= now - window:
                events.popleft()
            if len(events) >= limit:
                return max(1, round(window - (now - events[0])))
            events.append(now)
            if len(self._events) > 10_000:
                for stale_key in [
                    key
                    for key, values in self._events.items()
                    if not values or values[-1] <= now - max(window, 3600.0)
                ]:
                    self._events.pop(stale_key, None)
            return None

    def usage(self, organization_id: str, bucket: str, window_seconds: float) -> int:
        """Current in-window count without recording a hit (for the read endpoint)."""
        now = time.monotonic()
        window = max(window_seconds, 1.0)
        with self._lock:
            events = self._events.get((organization_id, bucket))
            if not events:
                return 0
            while events and events[0] <= now - window:
                events.popleft()
            return len(events)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


_org_quota_limiter = _OrgQuotaLimiter()

# Bucket -> window (seconds). Limits are read from settings at request time so a monkeypatched
# limit takes effect without rebuilding anything (mirrors how the demo limiter reads settings).
_ORG_QUOTA_WINDOWS: dict[str, float] = {"requests": 60.0, "builds": 3600.0, "llm": 3600.0}

# G58 — routes that MAY trigger a live external LLM call (analysis build with polish/extraction,
# grounded QA passes, deal-room extraction). Counted only when LLM_MODE=live: a mock deployment
# never calls out, so metering it would throttle free deterministic work for nothing.
_LLM_CAPABLE_PATHS = re.compile(
    r"^/api/(?:"
    r"workspaces/[a-zA-Z0-9_-]+/(?:risks/generate|qa|cross-corpus-qa|agent/run)"
    r"|deals/[a-zA-Z0-9_-]+/intelligence/extractions"
    r")$"
)


def _org_quota_limit(bucket: str) -> int:
    if bucket == "requests":
        return settings.org_request_quota_per_minute
    if bucket == "builds":
        return settings.org_build_quota_per_hour
    if bucket == "llm":
        return settings.org_llm_quota_per_hour
    return 0


def org_quota_usage(organization_id: str) -> list[dict]:
    """Report current per-bucket usage for an organization (backs the quota-usage endpoint)."""
    report: list[dict] = []
    for name, window in _ORG_QUOTA_WINDOWS.items():
        limit = _org_quota_limit(name)
        used = _org_quota_limiter.usage(organization_id, name, window)
        report.append(
            {
                "name": name,
                "used": used,
                "limit": limit,
                "window_seconds": int(window),
                "remaining": None if limit <= 0 else max(0, limit - used),
            }
        )
    return report


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
async def organization_quota_guard(request: Request, call_next):
    """Enforce per-organization quotas (G39) once a principal is resolved.

    Registered before ``identity_and_tenant_guard`` so it composes as the INNER layer: the identity
    guard resolves ``request.state.principal`` (session, ``dlk_`` API key, or trusted service) and
    handles auth/tenant early-returns first, then delegates here. Unauthenticated and pre-flight
    traffic (``principal is None``) is never counted. Two buckets: a per-minute "requests" quota on
    every authenticated call, and a per-hour "builds" quota layered over the SEC-bound build paths
    (the demo per-IP throttle in the identity guard still applies on top in DEMO_MODE). API-key
    principals count toward their organization's quota like any other member.
    """
    principal = getattr(request.state, "principal", None)
    if principal is None:
        return await call_next(request)
    organization_id = principal.organization_id
    retry_after = _org_quota_limiter.check(
        organization_id,
        "requests",
        limit=settings.org_request_quota_per_minute,
        window_seconds=_ORG_QUOTA_WINDOWS["requests"],
    )
    if retry_after is not None:
        return JSONResponse(
            status_code=429,
            content={"detail": "Organization request quota exceeded; retry later"},
            headers={"Retry-After": str(retry_after)},
        )
    if request.method == "POST" and _DEMO_THROTTLED_BUILD_PATHS.match(request.url.path):
        retry_after = _org_quota_limiter.check(
            organization_id,
            "builds",
            limit=settings.org_build_quota_per_hour,
            window_seconds=_ORG_QUOTA_WINDOWS["builds"],
        )
        if retry_after is not None:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Organization build quota exceeded for this hour; retry later"
                },
                headers={"Retry-After": str(retry_after)},
            )
    if (
        not settings.is_mock
        and request.method == "POST"
        and _LLM_CAPABLE_PATHS.match(request.url.path)
    ):
        retry_after = _org_quota_limiter.check(
            organization_id,
            "llm",
            limit=settings.org_llm_quota_per_hour,
            window_seconds=_ORG_QUOTA_WINDOWS["llm"],
        )
        if retry_after is not None:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": (
                        "Organization LLM quota exceeded for this hour; deterministic "
                        "endpoints remain available"
                    )
                },
                headers={"Retry-After": str(retry_after)},
            )
    return await call_next(request)


@app.middleware("http")
async def identity_and_tenant_guard(request: Request, call_next):
    """Resolve server-verified identity and enforce workspace tenant boundaries."""
    path = request.url.path
    request.state.principal = None
    if (request.method == "POST" and path in _RATE_LIMITED_AUTH_PATHS) or (
        request.method == "GET" and path in _RATE_LIMITED_AUTH_GET_PATHS
    ):
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
        elif supplied.startswith("dlk_"):
            # Scoped programmatic key (G38): resolves to a member principal carrying the key's
            # granted scopes; the deny-by-default scope gate and tenant guard below apply.
            with SessionLocal() as session:
                try:
                    request.state.principal = api_key_service.authenticate_api_key(session, supplied)
                except identity_service.IdentityError as exc:
                    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})
        else:
            request.state.principal = _service_principal(request, supplied)
            if request.state.principal is None:
                return JSONResponse(status_code=401, content={"detail": "Invalid authentication"})

    # A read-only share link (G44) authorizes itself via its opaque token in the path, so the
    # public snapshot endpoint must bypass the session-required guard.
    is_public = (
        path in _PUBLIC_PATHS
        or path.startswith("/api/shared/")
        or request.method == "OPTIONS"
    )
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

    # API-key principals are bounded by the scope catalog on EVERY route (deny-by-default),
    # not just the handful carrying an explicit require_scope dependency. Scope resolution
    # depends only on (method, path) — never on resource existence — so the 403 is not an
    # existence oracle, and the tenant guard's cross-org 404 below still applies on top.
    if principal is not None and principal.is_api_key and not is_public:
        required = api_key_service.api_key_scope_for(request.method, path)
        if required is None:
            return JSONResponse(
                status_code=403,
                content={"detail": "API keys cannot access this endpoint; use a user session"},
            )
        if not principal.has_scope(required):
            return JSONResponse(
                status_code=403,
                content={"detail": f"API key is missing the required scope: {required}"},
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


# Starlette applies the LAST-registered middleware as the OUTERMOST layer, so metrics is
# declared first, request-id second, and CORS last (at module bottom). Result (outermost ->
# inner):
#   CORS -> request_id -> metrics -> versioned_api_alias -> identity_and_tenant_guard
#        -> organization_quota_guard -> routes
# CORS is outermost so every response — including early JSONResponses from the auth/tenant/
# quota guards (401/403/404/429) — carries CORS headers and stays readable by the web app.
# request_id wraps everything else so the correlation id is bound before anything logs.
@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    """Record request count + latency per (method, registered-route-template, status)."""
    if request.url.path == "/metrics":
        return await call_next(request)
    start = time.perf_counter()
    # Resolve against the registered route table; anything else (bot scans, typos) collapses
    # into one "/unmatched" series so a public scanner cannot mint unbounded label values.
    template = ROUTE_TEMPLATES.resolve(request.url.path)
    try:
        response = await call_next(request)
    except Exception:
        METRICS.observe(request.method, template, 500, time.perf_counter() - start)
        raise
    METRICS.observe(request.method, template, response.status_code, time.perf_counter() - start)
    return response


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Honor an inbound X-Request-ID (else generate one), echo it, and bind it for logs."""
    inbound = request.headers.get("X-Request-ID", "").strip()
    request_id = inbound or new_request_id()
    request.state.request_id = request_id
    token = set_request_id(request_id)
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        reset_request_id(token)


@app.exception_handler(NotFound)
async def not_found_handler(request: Request, exc: NotFound) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": exc.message})


@app.get("/metrics", tags=["observability"], include_in_schema=False)
def metrics() -> Response:
    """Prometheus text exposition (version 0.0.4). Public/unauthenticated scrape target."""
    return Response(content=METRICS.render(), media_type=CONTENT_TYPE_LATEST)


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


_ROUTER_MODULES = (
    activity, agent, workspaces, targets, sec, filings, comps, financials, risks, questions,
    memos, red_team, evidence, examples, governance, govcon, portfolio, notifications,
    forensics, valuation, feeds, signals, ownership, search,
    underwriting_data, underwriting_model, deal_workflow, deal_intelligence,
    integrations, identity, api_keys, model_ops, quotas, watchlist, workspace_bundle,
    collaboration, memo_redline, comments, share_links,
)
for module in _ROUTER_MODULES:
    app.include_router(module.router)

# The metrics label vocabulary: every registered route (params collapsed to {id}) plus the
# app-level endpoints. Requests that match nothing become the single "/unmatched" series.
_PARAM_SEGMENT = re.compile(r"\{[^}]+\}")
ROUTE_TEMPLATES = PathTemplateMatcher(
    [
        _PARAM_SEGMENT.sub("{id}", route.path)
        for module in _ROUTER_MODULES
        for route in module.router.routes
    ]
    + ["/metrics", "/api/health", "/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"]
)

# Registered LAST so it is the OUTERMOST layer: guard early-returns (401/403/404/429) must
# carry CORS headers, or the web app cannot even read the status of a rejected request.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
