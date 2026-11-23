"""RADS settings: environment (.env) + config/defaults.yaml.

Relative paths in .env are resolved against the repository root, so scripts behave the
same whatever the current working directory is.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"
DEFAULTS_FILE = ROOT / "config" / "defaults.yaml"
SCHEMA_FILE = ROOT / "shared" / "schema.sql"

# Real environment variables win over .env.
load_dotenv(ENV_FILE, override=False)


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name, default)
    # Tolerate "value   # comment" even if a dotenv version keeps the comment.
    if " #" in value:
        value = value.split(" #", 1)[0]
    return value.strip()


def _path(name: str, default: str) -> Path:
    p = Path(_env(name, default))
    return p if p.is_absolute() else (ROOT / p).resolve()


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    db_path: Path
    api_url: str
    worker_url: str
    internal_token: str
    jwt_secret: str
    model_path: Path
    imgsz: int
    device: str
    whatsapp_token: str
    whatsapp_phone_id: str
    telegram_bot_token: str

    @property
    def evidence_dir(self) -> Path:
        return self.data_dir / "evidence"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @staticmethod
    def camera_rtsp(env_name: str) -> str:
        """RTSP URL from the named env var (e.g. CAM1_RTSP); '' if unset."""
        return _env(env_name)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        data_dir=_path("RADS_DATA_DIR", "./rads_data"),
        db_path=_path("RADS_DB_PATH", "./rads_data/rads.db"),
        api_url=_env("RADS_API_URL", "http://127.0.0.1:8000").rstrip("/"),
        worker_url=_env("RADS_WORKER_URL", "http://127.0.0.1:8001").rstrip("/"),
        internal_token=_env("RADS_INTERNAL_TOKEN"),
        jwt_secret=_env("RADS_JWT_SECRET"),
        model_path=_path("RADS_MODEL_PATH", "./models/v2_960_best.pt"),
        imgsz=int(_env("RADS_IMGSZ", "960") or 960),
        device=_env("RADS_DEVICE", "auto") or "auto",
        whatsapp_token=_env("WHATSAPP_TOKEN"),
        whatsapp_phone_id=_env("WHATSAPP_PHONE_ID"),
        telegram_bot_token=_env("TELEGRAM_BOT_TOKEN"),
    )


@lru_cache(maxsize=1)
def load_defaults() -> dict[str, Any]:
    """Parsed config/defaults.yaml."""
    with DEFAULTS_FILE.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
