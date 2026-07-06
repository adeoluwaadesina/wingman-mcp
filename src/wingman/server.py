"""MCP server entry point — registers tools and the UI resource.

Tool visibility is conveyed via the nested ``_meta.ui.visibility`` key per
the MCP Apps GA spec (SEP-1865, 2026-01-26). The UI panel is a single,
**static, predeclared** resource at ``ui://wingman/panel`` (no template
parameters) so it appears in ``resources/list`` and can be prefetched by the
host. ``show_plan`` advertises it via ``_meta.ui.resourceUri`` (plus the
legacy flat ``_meta["ui/resourceUri"]``) and ships the plan state in
``structuredContent``; the iframe reads that state via the render-data
channel. Hosts without MCP Apps still get the text fallback.
"""
from __future__ import annotations

import logging
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field

from . import prompts as prompt_templates
from .storage import db
from .tools import plan_tools, task_tools, ui_tools
from .ui import resource as ui_resource
from .ui.resource import MCP_UI_MIME_TYPE, PANEL_URI

log = logging.getLogger("wingman")

PlanName = Annotated[
    str,
    Field(
        min_length=1,
        max_length=64,
        description="Plan name (letters, digits, space, hyphen, underscore, apostrophe, period, colon, parentheses)",
    ),
]
TaskContent = Annotated[str, Field(min_length=1, max_length=2000)]
TaskStatus = Literal["pending", "in_progress", "done", "blocked"]

# Tool visibility per the MCP Apps schema (McpUiToolMeta.visibility under _meta.ui).
MODEL_AND_APP = {"ui": {"visibility": ["model", "app"]}}
APP_ONLY = {"ui": {"visibility": ["app"]}}

# ---------------------------------------------------------------------------
# show_plan registration meta — the panel-binding declaration. READ THIS before
# changing it; we have looped on it twice. Authority: vendored ext-apps@1.7.2
# (src/wingman/ui/static/mcp-app.js) + its server helper `registerAppTool`
# (dist/src/server/index.js, fn K3) and the McpUiToolMeta schema.
#
# Each key, what it does, what breaks if you remove it:
#
#   "ui": {"resourceUri": PANEL_URI}
#       THE PANEL BINDING. At connect time the host reads this from tools/list
#       and learns "calling show_plan mounts the ui://wingman/panel resource."
#       This is exactly the SDK's canonical `get-weather` example shape
#       (_meta: { ui: { resourceUri } }). REMOVE IT → the tool stays visible
#       and the call still returns text, but Claude Desktop never mounts the
#       iframe. That was the 2026-05-30 (afternoon) regression.
#
#   "ui/resourceUri": PANEL_URI
#       Legacy flat mirror of the above. `registerAppTool`'s normalizer (K3)
#       always emits BOTH the nested and the flat key for older-host compat,
#       so we match that contract. Harmless on modern hosts; required by some.
#
#   NO explicit "visibility" key.
#       McpUiToolMeta defaults visibility to ["model", "app"] when absent, so
#       omitting it keeps show_plan model-visible. We deliberately DO NOT write
#       an explicit `visibility: ["model","app"]` here: that exact shape
#       (explicit ["model","app"] alongside resourceUri) is what Claude Desktop
#       dropped from tools/list in the 2026-05-30 (morning) bug — 10 tools
#       instead of 11. `get-weather`, the SDK's blessed model-visible panel
#       tool, also omits visibility and relies on the default. We mirror it.
#
# Net: resourceUri present (panel mounts) + visibility defaulted (model sees it).
# Both coupled conditions satisfied. Changing either line risks regressing one.
SHOW_PLAN_META = {
    "ui": {"resourceUri": PANEL_URI},
    "ui/resourceUri": PANEL_URI,
}


def _panel_result_meta() -> dict[str, Any]:
    # Belt-and-suspenders: the CallToolResult ALSO carries the binding (the
    # 2026-05-30 morning CallToolResult fix). Do not remove — some hosts read
    # resourceUri from the result, not the registration. Both is correct.
    return {
        "ui": {"resourceUri": PANEL_URI},
        "ui/resourceUri": PANEL_URI,
    }


def build_server() -> FastMCP:
    mcp = FastMCP(
        name="wingman",
        icons=ui_resource.server_icons(),
        instructions=(
            "Wingman is an interactive plan/to-do panel for this conversation. "
            "Use `create_plan` to start a named plan, `add_task` / `add_tasks` to "
            "populate it, `show_plan` to render the interactive panel inline, and "
            "`tick_task` when you complete work. Plans persist across messages."
        ),
    )

    # -----------------------------------------------------------------
    # LLM-visible tools
    # -----------------------------------------------------------------

    @mcp.tool(
        meta=MODEL_AND_APP,
        description="Create a new named plan with optional initial tasks.",
        annotations=ToolAnnotations(
            title="Create Plan", readOnlyHint=False, destructiveHint=False,
            idempotentHint=False, openWorldHint=False,
        ),
    )
    def create_plan(name: PlanName, tasks: list[TaskContent] | None = None) -> dict[str, Any]:
        return plan_tools.create_plan(name, tasks or [])

    @mcp.tool(
        meta=MODEL_AND_APP,
        description="Append a single task to a plan.",
        annotations=ToolAnnotations(
            title="Add Task", readOnlyHint=False, destructiveHint=False,
            idempotentHint=False, openWorldHint=False,
        ),
    )
    def add_task(plan_name: PlanName, content: TaskContent) -> dict[str, Any]:
        return task_tools.add_task(plan_name, content)

    @mcp.tool(
        meta=MODEL_AND_APP,
        description="Append multiple tasks to a plan in one call.",
        annotations=ToolAnnotations(
            title="Add Tasks", readOnlyHint=False, destructiveHint=False,
            idempotentHint=False, openWorldHint=False,
        ),
    )
    def add_tasks(plan_name: PlanName, tasks: list[TaskContent]) -> dict[str, Any]:
        return task_tools.add_tasks(plan_name, tasks)

    @mcp.tool(
        meta=SHOW_PLAN_META,
        description="Render a plan as an interactive panel inline in the conversation.",
        annotations=ToolAnnotations(
            title="Show Plan Panel", readOnlyHint=True, openWorldHint=False,
        ),
    )
    def show_plan(plan_name: PlanName) -> CallToolResult:
        # Return CallToolResult directly so `_meta` lands at the CallToolResult
        # top level — that's where MCP-Apps hosts read `ui.resourceUri` from
        # (per SEP-1865). If we returned a plain dict, FastMCP would shove the
        # whole thing (including any `_meta` key) into `structuredContent`,
        # and hosts would never see the resource pointer.
        result = plan_tools.show_plan(plan_name)
        return CallToolResult(
            content=[TextContent(type="text", text=result.get("text", ""))],
            structuredContent=result,
            _meta=_panel_result_meta(),
            isError=False,
        )

    @mcp.tool(
        meta=MODEL_AND_APP,
        description="Return plan state as formatted text (no panel).",
        annotations=ToolAnnotations(
            title="Get Plan", readOnlyHint=True, openWorldHint=False,
        ),
    )
    def get_plan(plan_name: PlanName) -> dict[str, Any]:
        return plan_tools.get_plan(plan_name)

    @mcp.tool(
        meta=MODEL_AND_APP,
        description="Mark a task as done.",
        annotations=ToolAnnotations(
            title="Tick Task", readOnlyHint=False, destructiveHint=False,
            idempotentHint=True, openWorldHint=False,
        ),
    )
    def tick_task(plan_name: PlanName, task_id: int) -> dict[str, Any]:
        return task_tools.tick_task(plan_name, task_id)

    @mcp.tool(
        meta=MODEL_AND_APP,
        description="Change a task's status.",
        annotations=ToolAnnotations(
            title="Update Task Status", readOnlyHint=False, destructiveHint=False,
            idempotentHint=True, openWorldHint=False,
        ),
    )
    def update_task_status(plan_name: PlanName, task_id: int, status: TaskStatus) -> dict[str, Any]:
        return task_tools.update_task_status(plan_name, task_id, status)

    @mcp.tool(
        meta=MODEL_AND_APP,
        description="Rename a plan.",
        annotations=ToolAnnotations(
            title="Rename Plan", readOnlyHint=False, destructiveHint=False,
            idempotentHint=False, openWorldHint=False,
        ),
    )
    def rename_plan(current_name: PlanName, new_name: PlanName) -> dict[str, Any]:
        return plan_tools.rename_plan(current_name, new_name)

    @mcp.tool(
        meta=MODEL_AND_APP,
        description="Reorder tasks within a plan.",
        annotations=ToolAnnotations(
            title="Reorder Tasks", readOnlyHint=False, destructiveHint=False,
            idempotentHint=True, openWorldHint=False,
        ),
    )
    def reorder_tasks(plan_name: PlanName, ordered_ids: list[int]) -> dict[str, Any]:
        return task_tools.reorder_tasks(plan_name, ordered_ids)

    @mcp.tool(
        meta=MODEL_AND_APP,
        description="List all plans with task counts.",
        annotations=ToolAnnotations(
            title="List Plans", readOnlyHint=True, openWorldHint=False,
        ),
    )
    def list_plans() -> dict[str, Any]:
        return plan_tools.list_plans()

    @mcp.tool(
        meta=MODEL_AND_APP,
        description="Delete a plan and all its tasks.",
        annotations=ToolAnnotations(
            title="Delete Plan", readOnlyHint=False, destructiveHint=True,
            idempotentHint=True, openWorldHint=False,
        ),
    )
    def delete_plan(plan_name: PlanName) -> dict[str, Any]:
        return plan_tools.delete_plan(plan_name)

    @mcp.tool(
        meta=SHOW_PLAN_META,
        description="Render a clickable list of all plans as an interactive panel. Use this when the user wants to see or pick from their plans.",
        annotations=ToolAnnotations(
            title="Show All Plans", readOnlyHint=True, openWorldHint=False,
        ),
    )
    def show_plans() -> CallToolResult:
        # Binds to the same ui://wingman/panel resource as show_plan. The panel
        # detects mode from structuredContent shape: `plans` → picker, `plan` → task view.
        result = plan_tools.list_plans()
        return CallToolResult(
            content=[TextContent(type="text", text=result.get("text", ""))],
            structuredContent=result,
            _meta=_panel_result_meta(),
            isError=False,
        )

    # -----------------------------------------------------------------
    # UI-only tools (visibility = ["app"])
    # -----------------------------------------------------------------

    @mcp.tool(name="_ui_get_plan", meta=APP_ONLY, description="Internal: fetch plan state for live polling.")
    def _ui_get_plan(plan_name: PlanName) -> dict[str, Any]:
        return ui_tools.get_plan(plan_name)

    @mcp.tool(name="_ui_list_plans", meta=APP_ONLY, description="Internal: fetch plan list for picker polling.")
    def _ui_list_plans() -> dict[str, Any]:
        return ui_tools.list_plans()

    @mcp.tool(name="_ui_tick_task", meta=APP_ONLY, description="Internal: tick from UI.")
    def _ui_tick_task(plan_name: PlanName, task_id: int) -> dict[str, Any]:
        return ui_tools.tick_task(plan_name, task_id)

    @mcp.tool(name="_ui_update_status", meta=APP_ONLY, description="Internal: status change from UI.")
    def _ui_update_status(plan_name: PlanName, task_id: int, status: TaskStatus) -> dict[str, Any]:
        return ui_tools.update_status(plan_name, task_id, status)

    @mcp.tool(name="_ui_delete_task", meta=APP_ONLY, description="Internal: delete task from UI.")
    def _ui_delete_task(plan_name: PlanName, task_id: int) -> dict[str, Any]:
        return ui_tools.delete_task(plan_name, task_id)

    @mcp.tool(name="_ui_add_task", meta=APP_ONLY, description="Internal: add task from UI input.")
    def _ui_add_task(plan_name: PlanName, content: TaskContent) -> dict[str, Any]:
        return ui_tools.add_task(plan_name, content)

    @mcp.tool(name="_ui_rename_plan", meta=APP_ONLY, description="Internal: inline title rename.")
    def _ui_rename_plan(current_name: PlanName, new_name: PlanName) -> dict[str, Any]:
        return ui_tools.rename_plan(current_name, new_name)

    @mcp.tool(name="_ui_reorder_tasks", meta=APP_ONLY, description="Internal: drag-to-reorder.")
    def _ui_reorder_tasks(plan_name: PlanName, ordered_ids: list[int]) -> dict[str, Any]:
        return ui_tools.reorder_tasks(plan_name, ordered_ids)

    @mcp.tool(name="_ui_clear_completed", meta=APP_ONLY, description="Internal: bulk-delete completed tasks.")
    def _ui_clear_completed(plan_name: PlanName) -> dict[str, Any]:
        return ui_tools.clear_completed(plan_name)

    @mcp.tool(name="_ui_clear_all", meta=APP_ONLY, description="Internal: bulk-delete all tasks (plan kept).")
    def _ui_clear_all(plan_name: PlanName) -> dict[str, Any]:
        return ui_tools.clear_all(plan_name)

    @mcp.tool(name="_ui_delete_plan", meta=APP_ONLY, description="Internal: delete plan from menu.")
    def _ui_delete_plan(plan_name: PlanName) -> dict[str, Any]:
        return ui_tools.delete_plan(plan_name)

    @mcp.tool(name="_ui_export_markdown", meta=APP_ONLY, description="Internal: return plan as markdown.")
    def _ui_export_markdown(plan_name: PlanName) -> dict[str, Any]:
        return ui_tools.export_markdown(plan_name)

    @mcp.tool(name="_ui_get_run_task_prompt", meta=APP_ONLY, description="Internal: build Run-this-task prompt for sendMessage.")
    def _ui_get_run_task_prompt(plan_name: PlanName, task_id: int) -> dict[str, Any]:
        return ui_tools.get_run_task_prompt(plan_name, task_id)

    @mcp.tool(name="_ui_get_build_from_chat_prompt", meta=APP_ONLY, description="Internal: build the empty-state CTA prompt.")
    def _ui_get_build_from_chat_prompt(plan_name: PlanName) -> dict[str, Any]:
        return ui_tools.get_build_from_chat_prompt(plan_name)

    # -----------------------------------------------------------------
    # UI resource (MCP Apps)
    # -----------------------------------------------------------------

    # Static, predeclared, no-argument resource so it appears in resources/list
    # (a templated ui://.../{plan_name} would only show in resources/templates/list,
    # which MCP Apps hosts do not prefetch/render). The panel carries no plan data;
    # state reaches the iframe via render data (the show_plan structuredContent).
    @mcp.resource(
        PANEL_URI,
        mime_type=MCP_UI_MIME_TYPE,
        name="Wingman plan panel",
        description="Interactive plan/to-do panel rendered in a sandboxed iframe.",
    )
    def panel() -> str:
        return ui_resource.render_panel()

    return mcp


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    db.init_db()
    server = build_server()
    server.run()  # stdio by default
