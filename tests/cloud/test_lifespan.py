"""The production lifespan wiring runs without a database.

The smoke test proves the full stack but skips without WINGMAN_TEST_DSN, so this
DSN-less test guards the critical wiring in CI: build_app's lifespan must run the
caller startup AND enter the mounted MCP session manager without error.
"""
from wingman.cloud import server_http
from wingman.cloud.config_cloud import CloudConfig


class _StubVerifier:
    def verify(self, token):
        return {"sub": "x"}


def _cfg():
    return CloudConfig(
        database_url="postgresql://u:p@localhost/db", workos_api_key="x",
        workos_client_id="x", base_url="https://w.example.com", allowed_origins=[],
        sentry_dsn=None, posthog_key=None, max_plans_per_user=100,
        max_tasks_per_plan=500, max_batch_size=50, max_body_bytes=262144,
    )


async def test_lifespan_runs_startup_and_mcp_session_manager():
    started = {"ran": False}

    async def _startup():
        started["ran"] = True

    app = server_http.build_app(_cfg(), _StubVerifier(), on_startup=_startup)
    # Entering the parent lifespan must run our startup and the mounted MCP
    # streamable-http session manager; exiting must unwind both cleanly.
    async with app.router.lifespan_context(app):
        assert started["ran"] is True
