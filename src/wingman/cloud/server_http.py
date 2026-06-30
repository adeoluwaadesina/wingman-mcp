"""Cloud MCP server: same tools as local, served over streamable-HTTP,
scoped to the authenticated user and persisted to Postgres."""
from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent

from . import identity, store_pg
from .config_cloud import CloudConfig
from ..tools import plan_tools
from ..server import SHOW_PLAN_META, _panel_result_meta

# LLM-visible tool names for this server. Must equal the local model-visible
# (non _ui_) set from src/wingman/server.py. Verified by test_tool_parity_with_local.
LLM_TOOL_NAMES: set[str] = {
    "create_plan", "add_task", "add_tasks", "show_plan", "get_plan",
    "tick_task", "update_task_status", "rename_plan", "reorder_tasks",
    "list_plans", "delete_plan", "show_plans",
}


# ---------------------------------------------------------------------------
# Internal helper: load a Plan object for format_plan_text
# ---------------------------------------------------------------------------

async def _load_plan_obj(uid: str, name: str):
    """Return a Plan model object (needed by format_plan_text)."""
    from .store_pg import _load_plan, get_pool
    from ..storage.models import validate_plan_name
    name = validate_plan_name(name)
    pool = get_pool()
    async with pool.acquire() as conn:
        return await _load_plan(conn, uid, name)


# ---------------------------------------------------------------------------
# Thin, directly-testable helpers (identity always read here, never a param)
# ---------------------------------------------------------------------------

async def tool_create_plan(cfg: CloudConfig, name: str, tasks: list[str] | None) -> dict[str, Any]:
    uid = identity.current_user_id()
    # Upsert user row before creating plan (mirrors what auth middleware does on
    # every real request; required here to satisfy the FK plans.user_id -> users).
    await store_pg.upsert_user(uid, identity.current_email(), identity.current_display_name())
    return await store_pg.create_plan(
        uid, name, tasks or [],
        max_plans=cfg.max_plans_per_user,
        max_tasks=cfg.max_tasks_per_plan,
    )


async def tool_get_plan(name: str) -> dict[str, Any]:
    # Returns raw plan_to_dict result (has "tasks" key) so tests can assert on it.
    return await store_pg.get_plan(identity.current_user_id(), name)


async def tool_list_plans() -> dict[str, Any]:
    # Build picker payload inline - no plan_tools.list_plans_payload helper exists.
    # Uses a plain hyphen (not em dash) per project no-em-dash rule; deliberately
    # diverges from local plan_tools.list_plans which uses an em dash in that line.
    rows = await store_pg.list_plans(identity.current_user_id())
    if not rows:
        text = "No plans yet. Create one with `create_plan`."
    else:
        lines = ["Plans:"]
        for r in rows:
            lines.append(f"- {r['name']} - {r['done']}/{r['total']} done")
        text = "\n".join(lines)
    return {"text": text, "plans": rows}


async def tool_add_task(cfg: CloudConfig, plan_name: str, content: str) -> dict[str, Any]:
    return await store_pg.add_task(
        identity.current_user_id(), plan_name, content,
        max_tasks=cfg.max_tasks_per_plan,
    )


async def tool_add_tasks(cfg: CloudConfig, plan_name: str, tasks: list[str]) -> dict[str, Any]:
    uid = identity.current_user_id()
    await store_pg.add_tasks(
        uid, plan_name, tasks,
        max_tasks=cfg.max_tasks_per_plan,
        max_batch=cfg.max_batch_size,
    )
    return await store_pg.get_plan(uid, plan_name)


async def tool_tick_task(plan_name: str, task_id: int) -> dict[str, Any]:
    return await store_pg.tick_task(identity.current_user_id(), plan_name, task_id)


async def tool_update_status(plan_name: str, task_id: int, status: str) -> dict[str, Any]:
    return await store_pg.update_task_status(identity.current_user_id(), plan_name, task_id, status)


async def tool_rename_plan(current_name: str, new_name: str) -> dict[str, Any]:
    return await store_pg.rename_plan(identity.current_user_id(), current_name, new_name)


async def tool_reorder(plan_name: str, ordered_ids: list[int]) -> dict[str, Any]:
    return await store_pg.reorder_tasks(identity.current_user_id(), plan_name, ordered_ids)


async def tool_delete_plan(name: str) -> dict[str, Any]:
    await store_pg.delete_plan(identity.current_user_id(), name)
    return {"deleted": name}


async def tool_show_plan(plan_name: str) -> CallToolResult:
    uid = identity.current_user_id()
    plan = await _load_plan_obj(uid, plan_name)
    result = {
        "text": plan_tools.format_plan_text(plan),
        "plan": plan_tools.plan_to_dict(plan),
    }
    return CallToolResult(
        content=[TextContent(type="text", text=result["text"])],
        structuredContent=result,
        _meta=_panel_result_meta(),
        isError=False,
    )


async def tool_show_plans() -> CallToolResult:
    # Same text shape as tool_list_plans (hyphen, not em dash).
    rows = await store_pg.list_plans(identity.current_user_id())
    if not rows:
        text = "No plans yet. Create one with `create_plan`."
    else:
        lines = ["Plans:"]
        for r in rows:
            lines.append(f"- {r['name']} - {r['done']}/{r['total']} done")
        text = "\n".join(lines)
    result = {"text": text, "plans": rows}
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structuredContent=result,
        _meta=_panel_result_meta(),
        isError=False,
    )


# ---------------------------------------------------------------------------
# Panel tool registration (show_plan + show_plans, same binding as local)
# ---------------------------------------------------------------------------

def _register_panel_tools(mcp: FastMCP, cfg: CloudConfig) -> None:
    @mcp.tool(
        meta=SHOW_PLAN_META,
        description="Render a plan as an interactive panel inline in the conversation.",
    )
    async def show_plan(plan_name: str) -> CallToolResult:
        return await tool_show_plan(plan_name)

    @mcp.tool(
        meta=SHOW_PLAN_META,
        description=(
            "Render a clickable list of all plans as an interactive panel. "
            "Use this when the user wants to see or pick from their plans."
        ),
    )
    async def show_plans() -> CallToolResult:
        return await tool_show_plans()


# ---------------------------------------------------------------------------
# MCP app builder
# ---------------------------------------------------------------------------

def build_mcp(cfg: CloudConfig) -> FastMCP:
    """Return a FastMCP instance with all 12 LLM-visible tools registered.

    Tool signatures match the local server exactly so existing clients see an
    unchanged Wingman. Panel tools (show_plan, show_plans) carry the same
    _meta / resourceUri as local so the iframe mounts identically.
    """
    mcp = FastMCP(
        name="wingman",
        instructions=(
            "Wingman is an interactive plan/to-do panel for this conversation. "
            "Plans persist across messages and sync across your devices."
        ),
    )

    @mcp.tool(description="Create a new named plan with optional initial tasks.")
    async def create_plan(name: str, tasks: list[str] | None = None) -> dict[str, Any]:
        return await tool_create_plan(cfg, name, tasks)

    @mcp.tool(description="Append a single task to a plan.")
    async def add_task(plan_name: str, content: str) -> dict[str, Any]:
        return await tool_add_task(cfg, plan_name, content)

    @mcp.tool(description="Append multiple tasks to a plan in one call.")
    async def add_tasks(plan_name: str, tasks: list[str]) -> dict[str, Any]:
        return await tool_add_tasks(cfg, plan_name, tasks)

    @mcp.tool(description="Return plan state as formatted text (no panel).")
    async def get_plan(plan_name: str) -> dict[str, Any]:
        return await tool_get_plan(plan_name)

    @mcp.tool(description="Mark a task as done.")
    async def tick_task(plan_name: str, task_id: int) -> dict[str, Any]:
        return await tool_tick_task(plan_name, task_id)

    @mcp.tool(description="Change a task's status.")
    async def update_task_status(plan_name: str, task_id: int, status: str) -> dict[str, Any]:
        return await tool_update_status(plan_name, task_id, status)

    @mcp.tool(description="Rename a plan.")
    async def rename_plan(current_name: str, new_name: str) -> dict[str, Any]:
        return await tool_rename_plan(current_name, new_name)

    @mcp.tool(description="Reorder tasks within a plan.")
    async def reorder_tasks(plan_name: str, ordered_ids: list[int]) -> dict[str, Any]:
        return await tool_reorder(plan_name, ordered_ids)

    @mcp.tool(description="List all plans with task counts.")
    async def list_plans() -> dict[str, Any]:
        return await tool_list_plans()

    @mcp.tool(description="Delete a plan and all its tasks.")
    async def delete_plan(plan_name: str) -> dict[str, Any]:
        return await tool_delete_plan(plan_name)

    _register_panel_tools(mcp, cfg)
    return mcp
