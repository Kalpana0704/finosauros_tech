from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = "https://api.openai.com/v1"

    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-3-5-haiku-latest"

    poll_seconds: int = 45
    lookback_days: int = 120
    host: str = "0.0.0.0"
    port: int = 8000
    data_dir: Path = Field(default=ROOT / "data")
    watchlist: Path = Field(default=ROOT / "watchlist.yaml")
    sample_dir: Path = Field(default=ROOT / "sample_outputs")
    min_request_interval: float = 1.4
    request_timeout: float = 40.0


def load_watchlist(path: Path) -> list[dict[str, Any]]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    companies = raw.get("companies") or []
    if not companies:
        raise ValueError(f"No companies in {path}")
    return companies


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
(settings.data_dir / "pdfs").mkdir(parents=True, exist_ok=True)
(settings.data_dir / "transcripts").mkdir(parents=True, exist_ok=True)
settings.sample_dir.mkdir(parents=True, exist_ok=True)
