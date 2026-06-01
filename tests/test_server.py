"""Server-level tests: MCP Apps resource enumeration and tool metadata.

The panel resource MUST be a static, predeclared resource that shows up in
resources/list (not resources/templates/list) — otherwise MCP Apps hosts
never prefetch it and the panel never renders.
"""
from wingman.server import build_server, MODEL_AND_APP, APP_ONLY, SHOW_PLAN_META, _panel_result_meta
from wingman.ui.resource import PANEL_URI, MCP_UI_MIME_TYPE

# Handover §5.2 — these 11 tools MUST be visible to the model.
LLM_VISIBLE_TOOLS = {
    "create_plan",
    "add_task",
    "add_tasks",
    "show_plan",
    "get_plan",
    "tick_task",
    "update_task_status",
    "rename_plan",
    "reorder_tasks",
    "list_plans",
    "delete_plan",
}


async def test_panel_resource_is_enumerable():
    mcp = build_server()
    resources = await mcp.list_resources()
    uris = {str(r.uri) for r in resources}
    assert PANEL_URI in uris, f"{PANEL_URI} not in resources/list: {uris}"
    panel = next(r for r in resources if str(r.uri) == PANEL_URI)
    assert panel.mimeType == MCP_UI_MIME_TYPE


async def test_panel_resource_is_not_a_template():
    mcp = build_server()
    templates = await mcp.list_resource_templates()
    template_uris = {t.uriTemplate for t in templates}
    # No parameterized panel template should exist.
    assert not any("wingman/panel" in u for u in template_uris), template_uris


async def test_panel_resource_renders_static_html():
    mcp = build_server()
    contents = await mcp.read_resource(PANEL_URI)
    blocks = list(contents)
    assert blocks, "panel resource returned no content"
    html = blocks[0].content
    assert "globalThis.WingmanMCP={" in html  # SDK inlined + globalized
    assert "__PLAN_NAME__" not in html  # no baked-in plan data
    assert "WINGMAN &middot; MCP plan agent" in html
    assert 'data-plan=""' in html  # plan name not baked in; comes via render data


import re


async def test_deferred_menu_items_render_disabled():
    """v0.1 guard: three menu actions (clear-all, export, delete-plan) are
    deferred to v0.2 because the sandboxed iframe blocks confirm() and Blob
    downloads. They must render disabled (greyed, non-interactive, tooltip) and
    must NOT be silently re-enabled. The two working items must stay active."""
    mcp = build_server()
    blocks = list(await mcp.read_resource(PANEL_URI))
    html = blocks[0].content

    def button_tag(action):
        m = re.search(r'<button[^>]*data-action="' + re.escape(action) + r'"[^>]*>', html)
        assert m, f"no menu button for {action!r}"
        return m.group(0)

    for action in ("clear-all", "export", "delete-plan"):
        tag = button_tag(action)
        assert "disabled" in tag, f"{action} must carry the disabled attribute: {tag}"
        assert "menu-disabled" in tag, f"{action} must carry the menu-disabled class: {tag}"
        assert "Coming in v0.2" in tag, f"{action} must carry the v0.2 tooltip: {tag}"

    # The working items must remain interactive.
    for action in ("rename", "clear-completed"):
        tag = button_tag(action)
        assert "disabled" not in tag, f"{action} must NOT be disabled: {tag}"
        assert "menu-disabled" not in tag, f"{action} must NOT be greyed out: {tag}"

    # The disabling CSS must be present and non-interactive.
    assert ".menu button.menu-disabled" in html
    assert "pointer-events: none" in html


def _visibility(meta):
    if not isinstance(meta, dict):
        return None
    ui = meta.get("ui")
    return ui.get("visibility") if isinstance(ui, dict) else None


def _is_model_visible(meta):
    # Per McpUiToolMeta the default visibility (when absent) is ["model", "app"].
    vis = _visibility(meta)
    return vis is None or "model" in vis


async def test_tools_list_visibility_matches_handover_5_2():
    """CONDITION 1 — model visibility. The 11 §5.2 tools must be model-visible
    and no _ui_* tool may leak into that set. (Morning v0.1 RC bug: show_plan
    was missing → 10 tools.)"""
    mcp = build_server()
    tools = await mcp.list_tools()
    by_name = {t.name: t for t in tools}

    visible = {n for n, t in by_name.items() if _is_model_visible(t.meta)}
    assert visible == LLM_VISIBLE_TOOLS, (
        f"LLM-visible mismatch.\n  missing: {LLM_VISIBLE_TOOLS - visible}\n  extra: {visible - LLM_VISIBLE_TOOLS}"
    )

    leaked = {n for n in visible if n.startswith("_ui_")}
    assert not leaked, f"_ui_* tools leaked into model visibility: {leaked}"

    # All 13 _ui_* tools must be ["app"] only (hidden from the model).
    app_only = {n for n, t in by_name.items() if _visibility(t.meta) == ["app"]}
    assert all(n.startswith("_ui_") for n in app_only), (
        f"non-_ui_ tool registered as app-only: {[n for n in app_only if not n.startswith('_ui_')]}"
    )
    assert len(app_only) == 13, f"expected 13 _ui_* app-only tools, got {len(app_only)}: {sorted(app_only)}"


async def test_show_plan_has_panel_binding_AND_is_model_visible():
    """CONDITION 2 (coupled with 1) — the afternoon v0.1 RC regression.

    show_plan must simultaneously:
      (a) be model-visible (so Claude can call it), AND
      (b) carry the registration-level panel binding _meta.ui.resourceUri
          (so the host mounts the iframe when it's called).

    The morning fix removed (b) to get (a); that silently broke mounting.
    Both must hold. See SHOW_PLAN_META in server.py for the SDK grounding."""
    mcp = build_server()
    by_name = {t.name: t for t in await mcp.list_tools()}
    show = by_name["show_plan"]

    # (a) model-visible
    assert _is_model_visible(show.meta), f"show_plan not model-visible: {show.meta}"

    # (b) panel binding present in BOTH the nested and legacy-flat forms, exactly
    # as the SDK's registerAppTool normalizer (K3) emits them.
    assert show.meta["ui"]["resourceUri"] == PANEL_URI, (
        f"show_plan missing registration panel binding _meta.ui.resourceUri: {show.meta}. "
        "Without it Claude Desktop never mounts the iframe (afternoon regression)."
    )
    assert show.meta["ui/resourceUri"] == PANEL_URI, "missing legacy flat ui/resourceUri mirror"

    # We intentionally do NOT pin an explicit visibility key: omitting it (default
    # ["model","app"]) mirrors the SDK get-weather example and avoids the morning
    # bug where explicit ["model","app"] + resourceUri got dropped by the host.
    assert "visibility" not in show.meta["ui"], (
        "show_plan should rely on default visibility, not an explicit array — "
        "see SHOW_PLAN_META rationale in server.py."
    )
    assert show.meta == SHOW_PLAN_META


def test_panel_result_meta_both_forms():
    meta = _panel_result_meta()
    assert meta["ui"]["resourceUri"] == PANEL_URI
    assert meta["ui/resourceUri"] == PANEL_URI


async def test_show_plan_call_emits_top_level_meta():
    # Regression: previously show_plan returned a dict that included a
    # "_meta" key, which FastMCP buried inside structuredContent. The MCP
    # Apps host reads `resourceUri` from the CallToolResult's top-level
    # _meta, so the panel never connected. show_plan must now return a
    # CallToolResult whose .meta carries the pointer.
    from mcp.types import CallToolResult
    from wingman.tools import plan_tools
    mcp = build_server()
    plan_tools.create_plan("regress", ["only task"])
    result = await mcp.call_tool("show_plan", {"plan_name": "regress"})
    assert isinstance(result, CallToolResult), type(result).__name__
    assert result.meta is not None, "show_plan dropped _meta"
    assert result.meta["ui"]["resourceUri"] == PANEL_URI
    assert result.meta["ui/resourceUri"] == PANEL_URI
    assert result.structuredContent and result.structuredContent.get("plan", {}).get("name") == "regress"


def test_panel_html_carries_build_marker():
    from wingman.ui.resource import render_panel, BUILD_TIMESTAMP
    html = render_panel()
    assert f"build {BUILD_TIMESTAMP}" in html, "build marker missing from served HTML"
