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

    # --- Database ----------------------------------------------------------
    database_url: str = "sqlite:///./data/deallens.sqlite3"
    # "migrate" applies Alembic to head, "external" expects deployment orchestration to do so,
    # and "create_all" is reserved for isolated tests.
    schema_management: str = "migrate"

    # --- API ---------------------------------------------------------------
    api_host: str = "0.0.0.0"
    api_port: int = 8000
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

    # --- Public demo posture -------------------------------------------------
    # DEMO_MODE=true enables one-click guest sessions and per-IP throttling of the
    # SEC-bound build endpoints so a hosted demo respects EDGAR fair access.
    demo_mode: bool = False
    demo_builds_per_hour: int = 6
    # Guest-created demo data older than this is purged by `python -m src.workers.demo_cleanup`.
    demo_retention_hours: int = 72
    # On-disk EDGAR response cache TTL (0 = disabled; live research always refetches).
    edgar_cache_ttl_seconds: int = 0

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
