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
    # Intent classification: Claude when credentials exist, else the rules.
    # Either way the model only picks an intent name; replies stay templated.
    intent_llm: bool = _flag("CARELENS_INTENT_LLM", bool(os.environ.get("ANTHROPIC_API_KEY")))
    intent_model: str = os.environ.get("CARELENS_INTENT_MODEL", "claude-opus-5-5")
    # One shared password in front of a public demo link (HTTP Basic). Empty = open.
    demo_password: str = os.environ.get("CARELENS_DEMO_PASSWORD", "")
    web_dist: Path = Path(
        os.environ.get("CARELENS_WEB_DIST", str(REPO_ROOT / "apps" / "web" / "dist"))
    )
    synthetic_dir: Path = REPO_ROOT / "data" / "synthetic"

    @property
    def is_demo(self) -> bool:
        return self.env in {"demo", "test"}


settings = Settings()
