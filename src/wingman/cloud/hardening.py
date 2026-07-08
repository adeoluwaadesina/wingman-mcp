"""Transport hardening: body size cap, rate limiting, CORS, security headers."""
from __future__ import annotations

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import identity

log = logging.getLogger("wingman.cloud")


class RateLimiter:
    """Fixed-window token bucket, per key, in-memory (single instance)."""

    def __init__(self, max_per_min: int):
        self._max = max_per_min
        self._buckets: dict[str, tuple[float, int]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        window_start, count = self._buckets.get(key, (now, 0))
        if now - window_start >= 60.0:
            window_start, count = now, 0
        if count >= self._max:
            self._buckets[key] = (window_start, count)
            return False
        self._buckets[key] = (window_start, count + 1)
        return True


class BodyLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_bytes: int):
        super().__init__(app)
        self._max = max_bytes

    async def dispatch(self, request: Request, call_next):
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > self._max:
            return JSONResponse({"error": "payload_too_large"}, status_code=413)
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return resp


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, limiter: RateLimiter):
        super().__init__(app)
        self._limiter = limiter

    async def dispatch(self, request: Request, call_next):
        try:
            key = identity.current_user_id()
        except identity.Unauthenticated:
            key = request.client.host if request.client else "anon"
        if not self._limiter.allow(key):
            log.warning("rate limit hit key=%s path=%s", key, request.url.path)
            return JSONResponse({"error": "rate_limited"}, status_code=429)
        return await call_next(request)


# ---------------------------------------------------------------------------
# Middleware ordering helpers
#
# In Starlette, middleware added LATER wraps OUTERMOST (runs first inbound).
# Desired inbound chain:
#   CORS -> body-limit -> auth (sets identity) -> rate-limit (reads identity)
#     -> security-headers -> app
#
# To achieve this, middlewares must be added in reverse (innermost first):
#   1. SecurityHeadersMiddleware  (innermost, added 1st via apply_inner)
#   2. RateLimitMiddleware        (added 2nd via apply_inner)
#   3. AuthMiddleware             (added 3rd by build_app, wraps the above)
#   4. BodyLimitMiddleware        (added 4th via apply_outer)
#   5. CORSMiddleware             (outermost, added 5th via apply_outer)
#
# build_app calls: apply_inner -> add AuthMiddleware -> apply_outer
# ---------------------------------------------------------------------------


def apply_inner(app, cfg) -> None:
    """Add inner hardening middlewares (security headers, rate limit).
    Must be called BEFORE AuthMiddleware is added so auth runs outside these."""
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RateLimitMiddleware, limiter=RateLimiter(cfg.rate_limit_per_min))


def apply_outer(app, cfg) -> None:
    """Add outer hardening middlewares (body limit, CORS).
    Must be called AFTER AuthMiddleware is added so auth runs inside these.
    CORS is added last so it is the true outermost layer (handles preflight
    before auth can reject unauthenticated OPTIONS requests)."""
    app.add_middleware(BodyLimitMiddleware, max_bytes=cfg.max_body_bytes)
    if cfg.allowed_origins:
        # NEVER pass "*"; empty list means no CORS middleware at all.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cfg.allowed_origins,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["authorization", "content-type"],
        )


def apply(app, cfg) -> None:
    """Apply all hardening middlewares in correct internal order.

    Adds: SecurityHeaders, RateLimit, BodyLimit, and (if configured) CORS.
    CORS allow-list comes from cfg.allowed_origins; wildcard is never used.

    NOTE: In build_app, use apply_inner + AuthMiddleware + apply_outer so that
    auth (which sets identity) sits between rate-limit (reads identity) and
    body-limit, giving the correct inbound chain:
    CORS -> body-limit -> auth -> rate-limit -> security-headers -> app.
    """
    apply_inner(app, cfg)
    apply_outer(app, cfg)
