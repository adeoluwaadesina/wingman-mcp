# tests/cloud/test_server_tools.py
import pytest
from wingman.cloud import server_http, identity, store_pg
from wingman.cloud.config_cloud import CloudConfig

pytestmark = pytest.mark.asyncio


def _cfg():
    return CloudConfig(
        database_url="x", workos_api_key="x", workos_client_id="x",
        base_url="https://w.example.com", allowed_origins=[], sentry_dsn=None,
        posthog_key=None, max_plans_per_user=100, max_tasks_per_plan=500,
        max_batch_size=50, max_body_bytes=262144,
    )


async def test_tool_parity_with_local():
    # The cloud LLM-visible tool set equals the documented local set.
    from wingman import server as local_server
    mcp = local_server.build_server()
    local_tools = {t.name for t in (await mcp.list_tools()) if not t.name.startswith("_ui_")}
    assert server_http.LLM_TOOL_NAMES == local_tools


async def test_create_and_get_through_tools(pg_pool):
    tok = identity.set_current_user("user_T", "t@x.com", "Tee")
    try:
        await server_http.tool_create_plan(_cfg(), "Demo", ["a", "b"])
        plan = await server_http.tool_get_plan("Demo")
        assert [t["content"] for t in plan["tasks"]] == ["a", "b"]
    finally:
        identity.reset(tok)


async def test_tools_require_identity(pg_pool):
    with pytest.raises(identity.Unauthenticated):
        await server_http.tool_get_plan("Demo")
