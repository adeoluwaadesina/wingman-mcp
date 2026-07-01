from wingman.cloud import server_http
from wingman.cloud.config_cloud import CloudConfig


def _cfg():
    return CloudConfig(
        database_url="x", workos_api_key="x", workos_client_id="x",
        base_url="https://smoke.test", allowed_origins=[], sentry_dsn=None,
        posthog_key=None, max_plans_per_user=100, max_tasks_per_plan=500,
        max_batch_size=50, max_body_bytes=262144,
    )


async def test_ui_tools_and_panel_resource_registered():
    mcp = server_http.build_mcp(_cfg())
    tool_names = {t.name for t in await mcp.list_tools()}
    ui = {n for n in tool_names if n.startswith("_ui_")}
    assert len(ui) == 14, sorted(ui)
    resources = await mcp.list_resources()
    assert any(str(r.uri) == "ui://wingman/panel" for r in resources)
