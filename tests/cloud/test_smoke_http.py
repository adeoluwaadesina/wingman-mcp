"""End-to-end HTTP smoke test.

Runs the full cloud app (real Neon pool + MCP streamable-http + auth/hardening
middleware) on a background uvicorn server, then drives it with the real MCP
client over HTTP. Proves three things the unit tests cannot on their own:

1. The app serves MCP over HTTP with its lifespan wired (session manager + pool).
2. Identity propagates from the bearer token through the middleware into the
   async tools, so writes/reads are scoped to the authenticated user.
3. Cross-user isolation holds over the wire: user B cannot read user A's plan.
"""
from __future__ import annotations

import contextlib
import os
import socket
import threading
import time

import pytest

from mcp import ClientSession
# The headers-accepting client entry point (the renamed streamable_http_client
# has a different signature); silence its one deprecation notice below.
from mcp.client.streamable_http import streamablehttp_client

from wingman.cloud import auth, server_http, store_pg
from wingman.cloud.config_cloud import CloudConfig
from wingman.cloud.server_http import LLM_TOOL_NAMES

DSN = os.environ.get("WINGMAN_TEST_DSN")
pytestmark = [
    pytest.mark.skipif(not DSN, reason="set WINGMAN_TEST_DSN to run the HTTP smoke test"),
    pytest.mark.filterwarnings("ignore:Use `streamable_http_client`:DeprecationWarning"),
]


class _StubVerifier:
    def verify(self, token):
        if token == "alice-token":
            return {"sub": "alice", "email": "a@x.com", "name": "Alice"}
        if token == "bob-token":
            return {"sub": "bob", "email": "b@x.com", "name": "Bob"}
        raise auth.InvalidToken("bad token")


def _cfg():
    return CloudConfig(
        database_url=DSN, workos_api_key="x", workos_client_id="x",
        base_url="https://smoke.test", allowed_origins=["https://claude.ai"],
        sentry_dsn=None, posthog_key=None, max_plans_per_user=100,
        max_tasks_per_plan=500, max_batch_size=50, max_body_bytes=262144,
    )


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def _truncate():
    import asyncpg
    conn = await asyncpg.connect(DSN, statement_cache_size=0)
    await conn.execute("TRUNCATE tasks, plans, users RESTART IDENTITY CASCADE")
    await conn.close()


@contextlib.contextmanager
def _running_server(app, port):
    import uvicorn

    class _Server(uvicorn.Server):
        def install_signal_handlers(self):  # never touch signals off the main thread
            pass

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = _Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 20
        while not server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("uvicorn did not start in time")
            time.sleep(0.05)
        yield
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def _text(result) -> str:
    parts = []
    for c in result.content or []:
        parts.append(getattr(c, "text", ""))
    return " ".join(parts)


async def test_end_to_end_round_trip_and_isolation():
    await _truncate()

    async def _startup():
        pool = await store_pg.create_pool(DSN)
        await store_pg.init_db(pool)
        store_pg.set_pool(pool)

    app = server_http.build_app(_cfg(), _StubVerifier(), on_startup=_startup)
    port = _free_port()
    base = f"http://127.0.0.1:{port}/mcp"

    with _running_server(app, port):
        # ---- Alice: a full authenticated round trip ----
        async with streamablehttp_client(base, headers={"Authorization": "Bearer alice-token"}) as (r, w, _):
            async with ClientSession(r, w) as session:
                await session.initialize()

                tools = await session.list_tools()
                names = {t.name for t in tools.tools}
                assert LLM_TOOL_NAMES <= names, f"missing tools: {LLM_TOOL_NAMES - names}"

                created = await session.call_tool("create_plan", {"name": "SmokePlan", "tasks": ["a", "b"]})
                assert not created.isError, _text(created)

                got = await session.call_tool("get_plan", {"plan_name": "SmokePlan"})
                assert not got.isError, _text(got)
                # identity propagated -> Alice's plan carries her two tasks
                assert "a" in _text(got) and "b" in _text(got)

        # ---- Bob: cannot see Alice's plan (isolation over the wire) ----
        async with streamablehttp_client(base, headers={"Authorization": "Bearer bob-token"}) as (r, w, _):
            async with ClientSession(r, w) as session:
                await session.initialize()
                bob_view = await session.call_tool("get_plan", {"plan_name": "SmokePlan"})
                assert bob_view.isError, "Bob must not be able to read Alice's plan"


async def test_unauthenticated_request_is_rejected():
    app = server_http.build_app(_cfg(), _StubVerifier())
    port = _free_port()
    with _running_server(app, port):
        import httpx
        # No Authorization header -> the MCP endpoint must 401 before any tool runs.
        resp = httpx.post(f"http://127.0.0.1:{port}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        assert resp.status_code == 401
        # Health endpoint is public.
        assert httpx.get(f"http://127.0.0.1:{port}/healthz").status_code == 200
