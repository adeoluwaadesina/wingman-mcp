"""Optional, env-gated, cloud-only observability.

The local product never imports this module. Nothing here ever sends plan or
task content, emails, or tokens. Both integrations are no-ops unless their
env-driven config value is set, and a missing library or bad config is logged
and swallowed so it can never crash server boot.
"""
from __future__ import annotations

import logging

log = logging.getLogger("wingman.cloud")

_posthog = None
_SENSITIVE_KEYS = {"email", "authorization", "token", "access_token", "content", "data"}


def scrub_event(event: dict) -> dict:
    """Sentry ``before_send`` scrubber: strip request bodies, emails, tokens."""
    req = event.get("request")
    if isinstance(req, dict):
        req.pop("data", None)
        req.pop("cookies", None)
    user = event.get("user")
    if isinstance(user, dict):
        user.pop("email", None)
    extra = event.get("extra")
    if isinstance(extra, dict):
        for k in list(extra):
            if k.lower() in _SENSITIVE_KEYS:
                extra.pop(k, None)
    return event


def init(cfg) -> None:
    """Wire Sentry and/or PostHog if configured. Safe to call unconfigured."""
    global _posthog
    if getattr(cfg, "sentry_dsn", None):
        try:
            import sentry_sdk

            sentry_sdk.init(
                dsn=cfg.sentry_dsn,
                before_send=lambda e, h: scrub_event(e),
                send_default_pii=False,
            )
        except Exception as exc:  # missing lib or bad dsn must not crash boot
            log.warning("sentry init skipped: %s", exc)
    if getattr(cfg, "posthog_key", None):
        try:
            import posthog

            posthog.project_api_key = cfg.posthog_key
            _posthog = posthog
        except Exception as exc:
            log.warning("posthog init skipped: %s", exc)


def capture(event_name: str, user_id: str, props: dict | None = None) -> None:
    """Emit a product-analytics event. No-op unless PostHog is configured.

    Callers must pass only event names, user_id, and counts in ``props`` -
    never plan or task content.
    """
    if _posthog is None:
        return
    try:
        _posthog.capture(distinct_id=user_id, event=event_name, properties=props or {})
    except Exception as exc:
        log.warning("posthog capture failed: %s", exc)
