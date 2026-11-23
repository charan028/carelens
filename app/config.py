"""Runtime settings, read once from environment variables.

Nothing here is a secret default suitable for production: the demo secret
exists only so a synthetic-data developer environment starts with no setup.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    env: str = os.environ.get("CARELENS_ENV", "demo")
    database_url: str = os.environ.get(
        "CARELENS_DATABASE_URL", f"sqlite:///{REPO_ROOT / 'carelens.db'}"
    )
    secret_key: str = os.environ.get(
        "CARELENS_SECRET_KEY", "synthetic-demo-only-change-me"
    )
    # Public reference APIs (RxNorm, MedlinePlus, DailyMed, openFDA).
    live_reference_apis: bool = _flag("CARELENS_LIVE_REFERENCE_APIS", True)
    reference_timeout_s: float = float(os.environ.get("CARELENS_REFERENCE_TIMEOUT", "4"))
    reference_cache_hours: int = int(os.environ.get("CARELENS_REFERENCE_CACHE_HOURS", "24"))
    # Background reminder loop (off in tests, which drive the engine directly).
    run_engine_loop: bool = _flag("CARELENS_RUN_ENGINE", True)
    engine_interval_s: float = float(os.environ.get("CARELENS_ENGINE_INTERVAL", "15"))
    assistant_rate_limit_per_min: int = int(os.environ.get("CARELENS_RATE_LIMIT", "60"))
    web_dist: Path = Path(
        os.environ.get("CARELENS_WEB_DIST", str(REPO_ROOT / "apps" / "web" / "dist"))
    )
    synthetic_dir: Path = REPO_ROOT / "data" / "synthetic"

    @property
    def is_demo(self) -> bool:
        return self.env in {"demo", "test"}


settings = Settings()
