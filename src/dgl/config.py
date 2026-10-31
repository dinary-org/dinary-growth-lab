"""Runtime configuration loaded from environment variables (and an optional .env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # dotenv is optional at runtime
    pass

ROOT = Path(__file__).resolve().parents[2]


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    youtube_api_key: str = field(default_factory=lambda: _env("YOUTUBE_API_KEY"))
    youtube_daily_quota: int = field(default_factory=lambda: int(_env("YOUTUBE_DAILY_QUOTA", "8000")))
    region: str = field(default_factory=lambda: _env("DGL_REGION", "KR"))
    language: str = field(default_factory=lambda: _env("DGL_LANGUAGE", "ko"))

    apify_token: str = field(default_factory=lambda: _env("APIFY_TOKEN"))
    apify_max_usd: float = field(default_factory=lambda: float(_env("APIFY_MAX_USD_PER_RUN", "2.0")))
    apify_reel_actor: str = field(default_factory=lambda: _env("APIFY_REEL_ACTOR", "apify~instagram-reel-scraper"))
    apify_hashtag_actor: str = field(
        default_factory=lambda: _env("APIFY_HASHTAG_ACTOR", "apify~instagram-hashtag-scraper")
    )

    supabase_url: str = field(default_factory=lambda: _env("SUPABASE_URL"))
    supabase_key: str = field(default_factory=lambda: _env("SUPABASE_SERVICE_ROLE_KEY"))
    supabase_schema: str = field(default_factory=lambda: _env("SUPABASE_SCHEMA", "growth_lab"))

    r2_account_id: str = field(default_factory=lambda: _env("R2_ACCOUNT_ID"))
    r2_access_key_id: str = field(default_factory=lambda: _env("R2_ACCESS_KEY_ID"))
    r2_secret_access_key: str = field(default_factory=lambda: _env("R2_SECRET_ACCESS_KEY"))
    r2_bucket: str = field(default_factory=lambda: _env("R2_BUCKET", "dinary-growth-lab"))

    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    claude_model: str = field(default_factory=lambda: _env("DGL_CLAUDE_MODEL", "claude-opus-5-5"))
    runway_secret: str = field(default_factory=lambda: _env("RUNWAYML_API_SECRET"))

    data_dir: Path = field(default_factory=lambda: ROOT / _env("DGL_DATA_DIR", "data"))
    work_dir: Path = field(default_factory=lambda: ROOT / _env("DGL_WORK_DIR", "work"))
    remotion_browser: str = field(default_factory=lambda: _env("REMOTION_BROWSER_EXECUTABLE"))


def settings() -> Settings:
    s = Settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    s.work_dir.mkdir(parents=True, exist_ok=True)
    return s
