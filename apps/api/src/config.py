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

    # --- API ---------------------------------------------------------------
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    # Auto-seed live-SEC demo workspaces on first startup (needs network). Off by default so
    # startup stays fast/offline-safe; run `python -m src.seed.load_seed` to populate on demand.
    auto_seed: bool = False

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
