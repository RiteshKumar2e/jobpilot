"""Central configuration, loaded from environment (.env) via pydantic-settings.

Nothing secret is hardcoded. Missing optional values degrade gracefully:
no LLM key -> rule-based tailoring; no Google creds -> Sheets disabled; etc.
"""
from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import List

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_DIR = Path(__file__).resolve().parent.parent          # .../backend
PROJECT_DIR = BACKEND_DIR.parent                              # .../jobpilot


class ApprovalMode(str, Enum):
    SAFE = "SAFE"                      # Mode A: draft + human approval only
    RESTRICTED_AUTO = "RESTRICTED_AUTO"  # Mode B: auto-submit for allowlisted sources


class LLMProvider(str, Enum):
    NONE = "none"
    GROQ = "groq"        # free tier
    GEMINI = "gemini"    # free tier


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="JOBPILOT_",
        env_file=(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # App
    env: str = "dev"
    host: str = "127.0.0.1"
    port: int = 8077
    api_key: str = "change-me"
    cors_origins: str = "http://localhost:5177,http://127.0.0.1:5177"

    # DB
    db_url: str = f"sqlite:///{(PROJECT_DIR / 'data' / 'jobpilot.db')}"

    # Paths
    profile_path: Path = PROJECT_DIR / "data" / "profile" / "candidate_profile.yaml"
    resumes_dir: Path = PROJECT_DIR / "data" / "resumes"
    tailored_dir: Path = PROJECT_DIR / "data" / "tailored"
    log_dir: Path = PROJECT_DIR / "data" / "logs"

    # Approval / safety
    approval_mode: ApprovalMode = ApprovalMode.SAFE
    auto_apply_allowlist: str = ""
    daily_application_limit: int = 15
    daily_autosubmit_limit: int = 0
    # Mode B only auto-submits jobs at or above this match score.
    min_autosubmit_score: float = 55.0

    # Email notifications (Gmail: smtp_user = your address, smtp_password = an
    # App Password from myaccount.google.com/apppasswords — not your login password)
    notify_email: str = ""
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""

    # Discovery
    discovery_cron: str = "0 */6 * * *"
    # Run a full cycle as soon as the backend starts (not only on the cron).
    run_on_startup: bool = True
    http_timeout: int = 20
    max_concurrency: int = 4
    # Multi-source discovery
    enabled_providers: str = "all"          # "all" or e.g. "greenhouse,lever,ashby,remotive"
    discovery_concurrency: int = 6          # sources fetched in parallel
    provider_timeout: int = 20              # seconds per HTTP request
    max_pages_per_provider: int = 5         # pagination cap per source per cycle
    max_details_per_target: int = 40        # job-detail requests per board per cycle
    max_jobs_per_cycle: int = 10000         # hard cap on listings ingested per cycle (spread across sources)
    breaker_threshold: int = 3              # consecutive failures before a source cools down
    auto_watch_discovered: bool = True      # poll boards found inside other listings
    max_new_companies_per_cycle: int = 15

    # LLM
    llm_provider: LLMProvider = LLMProvider.NONE
    groq_model: str = "llama-3.3-70b-versatile"
    gemini_model: str = "gemini-2.5-flash"

    # Google
    google_credentials: Path = PROJECT_DIR / "data" / "google_credentials.json"
    google_token: Path = PROJECT_DIR / "data" / "google_token.json"
    sheet_id: str = ""

    # Browser
    browser_profile_dir: Path = PROJECT_DIR / "data" / "browser_profile"
    browser_headless: bool = False

    # ---- derived helpers -------------------------------------------------
    @property
    def cors_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def enabled_provider_set(self) -> set:
        from .providers import PROVIDERS
        v = self.enabled_providers.strip().lower()
        if v in ("", "all", "*"):
            return set(PROVIDERS)
        return {p.strip() for p in v.split(",") if p.strip() in PROVIDERS}

    @property
    def auto_apply_sources(self) -> List[str]:
        return [s.strip().lower() for s in self.auto_apply_allowlist.split(",") if s.strip()]

    # Secrets read directly from os.environ (not prefixed) so provider SDKs find them too
    @property
    def groq_api_key(self) -> str:
        return os.environ.get("GROQ_API_KEY", "")

    @property
    def gemini_api_key(self) -> str:
        return os.environ.get("GEMINI_API_KEY", "")

    @model_validator(mode="after")
    def _anchor_relative_paths(self) -> "Settings":
        """Relative paths in .env (e.g. ../data/...) are relative to backend/,
        not to whatever directory uvicorn happened to be started from."""
        for name in ("profile_path", "resumes_dir", "tailored_dir", "log_dir",
                     "google_credentials", "google_token", "browser_profile_dir"):
            p = Path(getattr(self, name))
            if not p.is_absolute():
                setattr(self, name, (BACKEND_DIR / p).resolve())
        prefix = "sqlite:///"
        if self.db_url.startswith(prefix):
            db_path = self.db_url[len(prefix):]
            if db_path and db_path != ":memory:" and not Path(db_path).is_absolute():
                self.db_url = prefix + (BACKEND_DIR / db_path).resolve().as_posix()
        return self

    def ensure_dirs(self) -> None:
        for p in (self.resumes_dir, self.tailored_dir, self.log_dir):
            Path(p).mkdir(parents=True, exist_ok=True)
        prefix = "sqlite:///"
        if self.db_url.startswith(prefix) and self.db_url[len(prefix):] != ":memory:":
            Path(self.db_url[len(prefix):]).parent.mkdir(parents=True, exist_ok=True)


_settings: Settings | None = None


_SECRET_ENV_KEYS = ("GROQ_API_KEY", "GEMINI_API_KEY", "ADZUNA_APP_ID", "ADZUNA_APP_KEY")


def _export_secret_keys(env_file: Path) -> None:
    """pydantic-settings only reads JOBPILOT_* fields from .env; it doesn't put
    the unprefixed provider keys into os.environ. Do that here (a real env var
    still wins)."""
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        key, sep, val = line.strip().partition("=")
        key = key.strip()
        if sep and key in _SECRET_ENV_KEYS and not os.environ.get(key):
            val = val.split(" #", 1)[0].strip().strip('"').strip("'")
            if val:
                os.environ[key] = val


def get_settings() -> Settings:
    """Singleton accessor so the whole app shares one config."""
    global _settings
    if _settings is None:
        _export_secret_keys(BACKEND_DIR / ".env")
        _settings = Settings()
        _settings.ensure_dirs()
    return _settings
