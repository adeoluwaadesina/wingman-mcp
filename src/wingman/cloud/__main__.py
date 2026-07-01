"""`python -m wingman.cloud` / `wingman-cloud` entry point.

Builds the Starlette ASGI app from environment config and serves it with
uvicorn. The Postgres pool is created inside the app's startup lifespan (so it
binds to uvicorn's running event loop), alongside the MCP session manager.
"""
from __future__ import annotations

import logging
import os

from . import auth as auth_mod
from . import observability, server_http, store_pg
from .config_cloud import CloudConfig

log = logging.getLogger("wingman.cloud")


def _discover(issuer: str) -> tuple[str | None, str | None]:
    """Read (jwks_uri, userinfo_endpoint) from the issuer's OpenID discovery."""
    import httpx

    try:
        resp = httpx.get(f"{issuer}/.well-known/openid-configuration", timeout=10)
        resp.raise_for_status()
        doc = resp.json()
        return doc.get("jwks_uri"), doc.get("userinfo_endpoint")
    except Exception as exc:  # unreachable issuer must not hard-crash boot here
        log.warning("oidc discovery failed for %s: %s", issuer, exc)
        return None, None


def build_from_env(connect: bool = True):
    """Construct the ASGI app from environment config.

    ``connect=False`` skips wiring the DB-pool startup (used by tests that do
    not have a database). The returned app is otherwise fully built.
    """
    cfg = CloudConfig.from_env()
    observability.init(cfg)

    issuer = server_http._idp_issuer(cfg)
    # Resolve the JWKS URL from the issuer's OpenID discovery document rather
    # than hardcoding a path (WorkOS AuthKit serves it at /oauth2/jwks, not the
    # /.well-known/jwks.json some providers use). WORKOS_JWKS_URI overrides;
    # discovery only runs on real startup (connect=True) so tests stay offline.
    jwks_uri = os.environ.get("WORKOS_JWKS_URI")
    userinfo_url = os.environ.get("WORKOS_USERINFO_URL")
    if connect and (not jwks_uri or not userinfo_url):
        d_jwks, d_userinfo = _discover(issuer)
        jwks_uri = jwks_uri or d_jwks
        userinfo_url = userinfo_url or d_userinfo
    if not jwks_uri:
        jwks_uri = f"{issuer}/.well-known/jwks.json"

    # Audience is opt-in: WorkOS AuthKit binds token aud to the OAuth client id
    # (dynamic under DCR), not the resource URL, so leave it unset and validate
    # via issuer + signature + expiry. Set WORKOS_AUDIENCE only if your IdP binds
    # aud to this server's resource URL.
    verifier = auth_mod.TokenVerifier(
        issuer=issuer,
        audience=os.environ.get("WORKOS_AUDIENCE") or None,
        jwks_uri=jwks_uri,
    )

    on_startup = None
    if connect:
        async def on_startup():
            pool = await store_pg.create_pool(cfg.database_url)
            await store_pg.init_db(pool)
            store_pg.set_pool(pool)

    return server_http.build_app(cfg, verifier, on_startup=on_startup, userinfo_url=userinfo_url)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    import uvicorn

    app = build_from_env(connect=True)
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))


if __name__ == "__main__":
    main()
