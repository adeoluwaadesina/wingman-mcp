"""Tests for the optional, env-gated cloud observability wiring."""
from wingman.cloud import observability


def test_scrub_removes_pii():
    event = {
        "request": {"data": "secret task text"},
        "user": {"email": "a@x.com", "id": "u1"},
        "extra": {"authorization": "Bearer abc"},
    }
    out = observability.scrub_event(event)
    assert "data" not in out.get("request", {})
    assert "email" not in out.get("user", {})
    assert out["user"]["id"] == "u1"
    assert "authorization" not in out.get("extra", {})


def test_init_noop_without_config():
    class Cfg:
        sentry_dsn = None
        posthog_key = None

    observability.init(Cfg())  # must not raise


def test_capture_noop_without_key():
    # No PostHog configured -> silently no-op, must not raise.
    observability.capture("plan_created", "u1", {"count": 1})
