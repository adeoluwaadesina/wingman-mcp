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


def build_from_env(connect: bool = True):
    """Construct the ASGI app from environment config.

    ``connect=False`` skips wiring the DB-pool startup (used by tests that do
    not have a database). The returned app is otherwise fully built.
    """
    cfg = CloudConfig.from_env()
    observability.init(cfg)

    issuer = server_http._idp_issuer(cfg)
    verifier = auth_mod.TokenVerifier(
        issuer=issuer,
        audience=cfg.base_url,
        jwks_uri=f"{issuer}/.well-known/jwks.json",
    )

    on_startup = None
    if connect:
        async def on_startup():
            pool = await store_pg.create_pool(cfg.database_url)
            await store_pg.init_db(pool)
            store_pg.set_pool(pool)

    return server_http.build_app(cfg, verifier, on_startup=on_startup)


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
