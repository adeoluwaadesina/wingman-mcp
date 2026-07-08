"""Environment-driven configuration for Wingman Cloud."""
from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(Exception):
    """Raised when a required environment variable is missing."""


def _require(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise ConfigError(f"missing required environment variable: {name}")
    return val


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


@dataclass(frozen=True)
class CloudConfig:
    database_url: str
    workos_api_key: str
    workos_client_id: str
    base_url: str
    allowed_origins: list[str]
    sentry_dsn: str | None
    posthog_key: str | None
    max_plans_per_user: int
    max_tasks_per_plan: int
    max_batch_size: int
    max_body_bytes: int
    # Per-user requests/minute ceiling. The interactive panel polls (~24/min at
    # 2.5s) on top of the user's actions, so this must sit well above that or
    # normal editing trips a 429. Default 240 (4/s) still stops runaway loops.
    rate_limit_per_min: int = 240

    @classmethod
    def from_env(cls) -> "CloudConfig":
        origins_raw = os.environ.get("ALLOWED_ORIGINS", "")
        origins = [o.strip() for o in origins_raw.split(",") if o.strip()]
        return cls(
            database_url=_require("DATABASE_URL"),
            workos_api_key=_require("WORKOS_API_KEY"),
            workos_client_id=_require("WORKOS_CLIENT_ID"),
            base_url=_require("WINGMAN_BASE_URL").rstrip("/"),
            allowed_origins=origins,
            sentry_dsn=os.environ.get("SENTRY_DSN") or None,
            posthog_key=os.environ.get("POSTHOG_KEY") or None,
            max_plans_per_user=_int_env("MAX_PLANS_PER_USER", 100),
            max_tasks_per_plan=_int_env("MAX_TASKS_PER_PLAN", 500),
            max_batch_size=_int_env("MAX_BATCH_SIZE", 50),
            max_body_bytes=_int_env("MAX_BODY_BYTES", 256 * 1024),
            rate_limit_per_min=_int_env("RATE_LIMIT_PER_MIN", 240),
        )
