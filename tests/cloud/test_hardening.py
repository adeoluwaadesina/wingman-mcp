import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient
from wingman.cloud import hardening


def test_rate_limiter_allows_then_blocks():
    rl = hardening.RateLimiter(max_per_min=2)
    assert rl.allow("k") is True
    assert rl.allow("k") is True
    assert rl.allow("k") is False
    assert rl.allow("other") is True  # separate bucket


def _app(**mw):
    async def ok(request):
        return PlainTextResponse("ok")
    app = Starlette(routes=[Route("/x", ok)])
    return app


def test_body_limit_413():
    app = _app()
    app.add_middleware(hardening.BodyLimitMiddleware, max_bytes=10)
    client = TestClient(app)
    r = client.post("/x", content=b"x" * 50, headers={"content-length": "50"})
    assert r.status_code == 413


def test_security_headers_present():
    app = _app()
    app.add_middleware(hardening.SecurityHeadersMiddleware)
    client = TestClient(app)
    r = client.get("/x")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "strict-transport-security" in r.headers


def test_rate_limit_429():
    app = _app()
    app.add_middleware(hardening.RateLimitMiddleware, limiter=hardening.RateLimiter(1))
    client = TestClient(app)
    assert client.get("/x").status_code == 200
    assert client.get("/x").status_code == 429
