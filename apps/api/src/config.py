"""Application configuration, loaded from environment / .env with safe defaults.

Every default is chosen so the app runs with zero setup: mock LLM mode and a local SQLite file.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- LLM ---------------------------------------------------------------
    # "mock" (default): deterministic seed outputs, no API key required.
    # "live": call an OpenAI-compatible chat completions endpoint.
    llm_mode: str = "mock"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.anthropic.com/v1"
    llm_model: str = "claude-opus-4-8"

    # --- Embeddings (G55) ---------------------------------------------------
    # "feature_hashing" (default): deterministic, dependency-free, keyless.
    # "onnx_local": operator-supplied local ONNX sentence-embedding model; requires the
    # optional extra (pip install .[embeddings]) and EMBEDDINGS_MODEL_PATH pointing at a
    # directory containing model.onnx + tokenizer.json. Unavailability degrades explicitly
    # to feature hashing — never a crash, never silently mixed vector spaces.
    embeddings_backend: str = "feature_hashing"
    embeddings_model_path: str = ""

    # --- Database ----------------------------------------------------------
    database_url: str = "sqlite:///./data/deallens.sqlite3"
    # "migrate" applies Alembic to head, "external" expects deployment orchestration to do so,
    # and "create_all" is reserved for isolated tests.
    schema_management: str = "migrate"

    # --- API ---------------------------------------------------------------
    # Emit structured single-line JSON logs (with request_id) for prod log pipelines.
    # Off by default so local/dev keeps human-readable logs.
    json_logs: bool = False
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    # Auto-seed live-SEC demo workspaces on first startup (needs network). Off by default so
    # startup stays fast/offline-safe; run `python -m src.seed.load_seed` to populate on demand.
    auto_seed: bool = False
    # Optional tenant selector for the CLI/auto seeder when more than one organization exists.
    seed_organization_slug: str = ""
    # Secure by default. Tests or an explicitly isolated demo may opt out with AUTH_REQUIRED=false.
    auth_required: bool = True
    internal_api_token: str = ""
    # The first user may always bootstrap the installation; further self-service signups are opt-in.
    auth_allow_registration: bool = False
    auth_session_hours: int = 12
    auth_max_failed_logins: int = 5
    auth_lockout_minutes: int = 15
    auth_rate_limit_attempts: int = 30
    auth_rate_limit_window_seconds: int = 60
    webhook_encryption_key: str = ""
    webhook_allow_insecure_http: bool = False

    # --- Optional OIDC SSO (G48) --------------------------------------------
    # Password auth stays the default; SSO is entirely opt-in. When OIDC_ENABLED=false the
    # /api/auth/oidc/* endpoints 404. oidc_role_map is a JSON object mapping IdP role claim values
    # to DealLens membership roles (owner/admin/member/viewer); unmapped/missing roles fall back to
    # 'viewer' (least privilege). SSO users are provisioned into OIDC_ORGANIZATION_SLUG.
    oidc_enabled: bool = False
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_redirect_uri: str = ""
    oidc_scopes: str = "openid email profile"
    oidc_role_claim: str = "roles"
    oidc_role_map: str = ""
    oidc_organization_slug: str = ""

    # --- Fine-grained permissions (G49) -------------------------------------
    # Deny-by-default capability matrix layered over the four coarse roles. On by default so the
    # role defaults reproduce the coarse behavior; set false to fall back to coarse role checks
    # only (route-level require_capability dependencies become no-ops).
    permission_matrix_enabled: bool = True

    # --- Public demo posture -------------------------------------------------
    # DEMO_MODE=true enables one-click guest sessions and per-IP throttling of the
    # SEC-bound build endpoints so a hosted demo respects EDGAR fair access.
    demo_mode: bool = False
    demo_builds_per_hour: int = 6
    # Guest-created demo data older than this is purged by `python -m src.workers.demo_cleanup`.
    demo_retention_hours: int = 72
    # On-disk EDGAR response cache TTL (0 = disabled; live research always refetches).
    edgar_cache_ttl_seconds: int = 0

    # --- Per-organization quotas (G39) --------------------------------------
    # In-process sliding-window tenant quotas (generalizes the demo per-IP limiter into policy).
    # Enforced per resolved principal.organization_id, so API-key callers count toward their org.
    # 0 = unlimited. Defaults are deliberately generous so ordinary interactive use and the full
    # test suite never trip them; quota-boundary tests monkeypatch the relevant limit down.
    org_request_quota_per_minute: int = 600
    org_build_quota_per_hour: int = 60
    # G58 — hourly cap on requests that MAY trigger a live external LLM call (analysis build,
    # grounded QA, LLM extraction). Bounds the public demo's API spend; 0 = unlimited. Only
    # meaningful when LLM_MODE=live — mock deployments never call out regardless.
    org_llm_quota_per_hour: int = 120

    # --- Blob storage (G40) --------------------------------------------------
    # Backend for opaque blobs (data-room docs, EDGAR cache): "local" (default, zero setup) or
    # "s3" (S3-compatible; requires an injected client — see storage_service.get_store).
    storage_backend: str = "local"
    # Root directory for the local-disk backend, relative to the API working dir by default.
    storage_root: str = "./data/blobs"
    # S3-compatible settings (only consulted when storage_backend == "s3").
    s3_bucket: str = ""
    s3_prefix: str = ""
    s3_endpoint_url: str = ""
    s3_region: str = ""

    # --- Public data sources (live mode / extensions) ----------------------
    sec_user_agent: str = "DealLens Diligence Lab (portfolio project) contact@example.com"
    fred_api_key: str = ""
    openfigi_api_key: str = ""
    sam_gov_api_key: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_mock(self) -> bool:
        return self.llm_mode.lower() != "live"

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
