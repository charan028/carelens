"""Runtime settings, read once from environment variables.

Nothing here is a secret default suitable for production: the demo secret
exists only so a synthetic-data developer environment starts with no setup.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_SECRET = "synthetic-demo-only-change-me"


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _database_url() -> str:
    url = os.environ.get("CARELENS_DATABASE_URL", f"sqlite:///{REPO_ROOT / 'carelens.db'}")
    # Hosts hand out postgres:// or postgresql:// URLs; we use the psycopg 3 driver.
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


@dataclass
class Settings:
    env: str = os.environ.get("CARELENS_ENV", "demo")
    database_url: str = _database_url()
    secret_key: str = os.environ.get("CARELENS_SECRET_KEY", DEMO_SECRET)
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

    # --- hosted demo -------------------------------------------------------
    # With sandboxes on, every tester gets a private copy of the synthetic
    # records and their own simulated clock. Required for any public URL.
    sandboxes: bool = _flag("CARELENS_SANDBOXES", False)
    access_code: str = os.environ.get("CARELENS_ACCESS_CODE", "")
    sandbox_max: int = int(os.environ.get("CARELENS_SANDBOX_MAX", "200"))
    sandbox_idle_days: int = int(os.environ.get("CARELENS_SANDBOX_IDLE_DAYS", "7"))
    sandbox_dir: Path = Path(os.environ.get("CARELENS_SANDBOX_DIR", str(REPO_ROOT / ".sandboxes")))

    @property
    def is_demo(self) -> bool:
        return self.env in {"demo", "test"}

    def hosted_problems(self) -> list[str]:
        """Reasons this configuration must not be exposed on a public URL."""
        if not self.sandboxes:
            return []
        problems = []
        if self.secret_key == DEMO_SECRET or len(self.secret_key) < 24:
            problems.append("CARELENS_SECRET_KEY must be a private random value of 24+ characters")
        if len(self.access_code) < 6:
            problems.append("CARELENS_ACCESS_CODE must be set (6+ characters)")
        return problems


settings = Settings()
