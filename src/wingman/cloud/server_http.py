"""Cloud MCP server: same tools as local, served over streamable-HTTP,
scoped to the authenticated user and persisted to Postgres."""
from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, ToolAnnotations

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
        annotations=ToolAnnotations(
            title="Show Plan Panel", readOnlyHint=True, openWorldHint=False,
        ),
    )
    async def show_plan(plan_name: str) -> CallToolResult:
        return await tool_show_plan(plan_name)

    @mcp.tool(
        meta=SHOW_PLAN_META,
        description=(
            "Render a clickable list of all plans as an interactive panel. "
            "Use this when the user wants to see or pick from their plans."
        ),
        annotations=ToolAnnotations(
            title="Show All Plans", readOnlyHint=True, openWorldHint=False,
        ),
    )
    async def show_plans() -> CallToolResult:
        return await tool_show_plans()


# ---------------------------------------------------------------------------
# Panel resource + app-only _ui_* tools (the interactive iframe calls these)
# ---------------------------------------------------------------------------

def _register_ui_tools(mcp: FastMCP, cfg: CloudConfig) -> None:
    """Serve the panel HTML resource and the 14 app-only tools the iframe uses.

    Same names/shapes as the local server so the bundled panel JS works, but
    every op is scoped to the authenticated user via identity + store_pg.
    """
    from .. import prompts
    from ..ui import resource as ui_resource
    from ..ui.resource import MCP_UI_MIME_TYPE, PANEL_URI

    app_only = {"ui": {"visibility": ["app"]}}

    def _uid() -> str:
        return identity.current_user_id()

    async def _plan_payload(plan_name: str) -> dict[str, Any]:
        plan = await _load_plan_obj(_uid(), plan_name)
        return {"text": plan_tools.format_plan_text(plan), "plan": plan_tools.plan_to_dict(plan)}

    @mcp.resource(
        PANEL_URI, mime_type=MCP_UI_MIME_TYPE, name="Wingman plan panel",
        description="Interactive plan/to-do panel rendered in a sandboxed iframe.",
    )
    def panel() -> str:
        return ui_resource.render_panel()

    @mcp.tool(name="_ui_get_plan", meta=app_only, description="Internal: fetch plan state for live polling.")
    async def _ui_get_plan(plan_name: str) -> dict[str, Any]:
        return await _plan_payload(plan_name)

    @mcp.tool(name="_ui_list_plans", meta=app_only, description="Internal: fetch plan list for picker polling.")
    async def _ui_list_plans() -> dict[str, Any]:
        return await tool_list_plans()

    @mcp.tool(name="_ui_tick_task", meta=app_only, description="Internal: tick from UI.")
    async def _ui_tick_task(plan_name: str, task_id: int) -> dict[str, Any]:
        return await store_pg.tick_task(_uid(), plan_name, task_id)

    @mcp.tool(name="_ui_update_status", meta=app_only, description="Internal: status change from UI.")
    async def _ui_update_status(plan_name: str, task_id: int, status: str) -> dict[str, Any]:
        return await store_pg.update_task_status(_uid(), plan_name, task_id, status)

    @mcp.tool(name="_ui_delete_task", meta=app_only, description="Internal: delete task from UI.")
    async def _ui_delete_task(plan_name: str, task_id: int) -> dict[str, Any]:
        await store_pg.delete_task(_uid(), plan_name, task_id)
        return {"deleted": task_id}

    @mcp.tool(name="_ui_add_task", meta=app_only, description="Internal: add task from UI input.")
    async def _ui_add_task(plan_name: str, content: str) -> dict[str, Any]:
        return await store_pg.add_task(_uid(), plan_name, content, max_tasks=cfg.max_tasks_per_plan)

    @mcp.tool(name="_ui_rename_plan", meta=app_only, description="Internal: inline title rename.")
    async def _ui_rename_plan(current_name: str, new_name: str) -> dict[str, Any]:
        return await store_pg.rename_plan(_uid(), current_name, new_name)

    @mcp.tool(name="_ui_reorder_tasks", meta=app_only, description="Internal: drag-to-reorder.")
    async def _ui_reorder_tasks(plan_name: str, ordered_ids: list[int]) -> dict[str, Any]:
        return await store_pg.reorder_tasks(_uid(), plan_name, ordered_ids)

    @mcp.tool(name="_ui_clear_completed", meta=app_only, description="Internal: bulk-delete completed tasks.")
    async def _ui_clear_completed(plan_name: str) -> dict[str, Any]:
        n = await store_pg.clear_completed(_uid(), plan_name)
        return {"text": f"Cleared {n} completed task(s).", "removed": n}

    @mcp.tool(name="_ui_clear_all", meta=app_only, description="Internal: bulk-delete all tasks (plan kept).")
    async def _ui_clear_all(plan_name: str) -> dict[str, Any]:
        n = await store_pg.clear_all(_uid(), plan_name)
        return {"text": f"Cleared {n} task(s).", "removed": n}

    @mcp.tool(name="_ui_delete_plan", meta=app_only, description="Internal: delete plan from menu.")
    async def _ui_delete_plan(plan_name: str) -> dict[str, Any]:
        await store_pg.delete_plan(_uid(), plan_name)
        return {"deleted": plan_name}

    @mcp.tool(name="_ui_export_markdown", meta=app_only, description="Internal: return plan as markdown.")
    async def _ui_export_markdown(plan_name: str) -> dict[str, Any]:
        plan = await _load_plan_obj(_uid(), plan_name)
        md = plan_tools.export_markdown(plan)
        return {"text": md, "markdown": md}

    @mcp.tool(name="_ui_get_run_task_prompt", meta=app_only, description="Internal: build Run-this-task prompt for sendMessage.")
    async def _ui_get_run_task_prompt(plan_name: str, task_id: int) -> dict[str, Any]:
        # Build the prompt from Postgres. (The local prompts.render_run_task_prompt
        # reads the on-disk SQLite db, which does not exist on the cloud server -
        # calling it here raised and made Run-task fail with an error.)
        plan = await _load_plan_obj(_uid(), plan_name)
        task = next((t for t in plan.tasks if t.id == task_id), None)
        if task is None:
            raise store_pg.TaskNotFound(f"task {task_id} not found in plan '{plan_name}'")
        text = prompts.RUN_TASK_PROMPT.format(plan_name=plan.name, task_content=task.content)
        try:
            await store_pg.update_task_status(_uid(), plan_name, task_id, "in_progress")
        except Exception:
            pass
        return {"text": text, "prompt": text}

    @mcp.tool(name="_ui_get_build_from_chat_prompt", meta=app_only, description="Internal: build the empty-state CTA prompt.")
    async def _ui_get_build_from_chat_prompt(plan_name: str) -> dict[str, Any]:
        text = prompts.render_build_from_chat_prompt(plan_name)
        return {"text": text, "prompt": text}


# ---------------------------------------------------------------------------
# MCP app builder
# ---------------------------------------------------------------------------

def build_mcp(cfg: CloudConfig) -> FastMCP:
    """Return a FastMCP instance with all 12 LLM-visible tools registered.

    Tool signatures match the local server exactly so existing clients see an
    unchanged Wingman. Panel tools (show_plan, show_plans) carry the same
    _meta / resourceUri as local so the iframe mounts identically.
    """
    # DNS-rebinding protection validates the Host header against an allow-list.
    # The default permits localhost but not our deployment host, so a request
    # behind Render (Host: <app>.onrender.com) is rejected with 421. Allow the
    # host derived from WINGMAN_BASE_URL, plus localhost (wildcard port) for the
    # smoke test, plus any extra hosts from ALLOWED_HOSTS.
    from urllib.parse import urlparse
    from mcp.server.transport_security import TransportSecuritySettings

    base_host = urlparse(cfg.base_url).netloc
    allowed_hosts = ["127.0.0.1:*", "localhost:*"]
    if base_host:
        allowed_hosts.append(base_host)
    allowed_hosts += [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()]

    from ..ui.resource import server_icons

    mcp = FastMCP(
        name="wingman",
        icons=server_icons(cfg.base_url),
        website_url="https://github.com/adeoluwaadesina/wingman-mcp",
        instructions=(
            "Wingman is an interactive plan/to-do panel for this conversation. "
            "Plans persist across messages and sync across your devices."
        ),
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowed_hosts,
            allowed_origins=cfg.allowed_origins,
        ),
    )

    @mcp.tool(
        description="Create a new named plan with optional initial tasks.",
        annotations=ToolAnnotations(
            title="Create Plan", readOnlyHint=False, destructiveHint=False,
            idempotentHint=False, openWorldHint=False,
        ),
    )
    async def create_plan(name: str, tasks: list[str] | None = None) -> dict[str, Any]:
        return await tool_create_plan(cfg, name, tasks)

    @mcp.tool(
        description="Append a single task to a plan.",
        annotations=ToolAnnotations(
            title="Add Task", readOnlyHint=False, destructiveHint=False,
            idempotentHint=False, openWorldHint=False,
        ),
    )
    async def add_task(plan_name: str, content: str) -> dict[str, Any]:
        return await tool_add_task(cfg, plan_name, content)

    @mcp.tool(
        description="Append multiple tasks to a plan in one call.",
        annotations=ToolAnnotations(
            title="Add Tasks", readOnlyHint=False, destructiveHint=False,
            idempotentHint=False, openWorldHint=False,
        ),
    )
    async def add_tasks(plan_name: str, tasks: list[str]) -> dict[str, Any]:
        return await tool_add_tasks(cfg, plan_name, tasks)

    @mcp.tool(
        description="Return plan state as formatted text (no panel).",
        annotations=ToolAnnotations(
            title="Get Plan", readOnlyHint=True, openWorldHint=False,
        ),
    )
    async def get_plan(plan_name: str) -> dict[str, Any]:
        return await tool_get_plan(plan_name)

    @mcp.tool(
        description="Mark a task as done.",
        annotations=ToolAnnotations(
            title="Tick Task", readOnlyHint=False, destructiveHint=False,
            idempotentHint=True, openWorldHint=False,
        ),
    )
    async def tick_task(plan_name: str, task_id: int) -> dict[str, Any]:
        return await tool_tick_task(plan_name, task_id)

    @mcp.tool(
        description="Change a task's status.",
        annotations=ToolAnnotations(
            title="Update Task Status", readOnlyHint=False, destructiveHint=False,
            idempotentHint=True, openWorldHint=False,
        ),
    )
    async def update_task_status(plan_name: str, task_id: int, status: str) -> dict[str, Any]:
        return await tool_update_status(plan_name, task_id, status)

    @mcp.tool(
        description="Rename a plan.",
        annotations=ToolAnnotations(
            title="Rename Plan", readOnlyHint=False, destructiveHint=False,
            idempotentHint=False, openWorldHint=False,
        ),
    )
    async def rename_plan(current_name: str, new_name: str) -> dict[str, Any]:
        return await tool_rename_plan(current_name, new_name)

    @mcp.tool(
        description="Reorder tasks within a plan.",
        annotations=ToolAnnotations(
            title="Reorder Tasks", readOnlyHint=False, destructiveHint=False,
            idempotentHint=True, openWorldHint=False,
        ),
    )
    async def reorder_tasks(plan_name: str, ordered_ids: list[int]) -> dict[str, Any]:
        return await tool_reorder(plan_name, ordered_ids)

    @mcp.tool(
        description="List all plans with task counts.",
        annotations=ToolAnnotations(
            title="List Plans", readOnlyHint=True, openWorldHint=False,
        ),
    )
    async def list_plans() -> dict[str, Any]:
        return await tool_list_plans()

    @mcp.tool(
        description="Delete a plan and all its tasks.",
        annotations=ToolAnnotations(
            title="Delete Plan", readOnlyHint=False, destructiveHint=True,
            idempotentHint=True, openWorldHint=False,
        ),
    )
    async def delete_plan(plan_name: str) -> dict[str, Any]:
        return await tool_delete_plan(plan_name)

    _register_panel_tools(mcp, cfg)
    _register_ui_tools(mcp, cfg)
    return mcp


# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------

import logging
import os

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from . import auth as auth_mod
from . import hardening

log = logging.getLogger("wingman.cloud")


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, verifier, public_paths, resource_metadata_url=None, userinfo_url=None):
        super().__init__(app)
        self._verifier = verifier
        self._public = public_paths
        # RFC 9728: a 401 points clients at the protected-resource metadata via
        # WWW-Authenticate, so MCP clients know where to begin the OAuth flow.
        self._challenge = (
            f'Bearer resource_metadata="{resource_metadata_url}"'
            if resource_metadata_url else None
        )
        # WorkOS access tokens carry no email; fetch it from the IdP userinfo
        # endpoint once per user (cached), so we can store it for Wrapped.
        self._userinfo_url = userinfo_url
        self._enriched: set[str] = set()

    def _unauth(self, body):
        headers = {"WWW-Authenticate": self._challenge} if self._challenge else None
        return JSONResponse(body, status_code=401, headers=headers)

    async def dispatch(self, request: Request, call_next):
        if request.url.path in self._public:
            return await call_next(request)
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("bearer "):
            return self._unauth({"error": "unauthenticated"})
        token = header.split(" ", 1)[1].strip()
        try:
            claims = self._verifier.verify(token)
        except auth_mod.InvalidToken as exc:
            client = request.client.host if request.client else "?"
            # Log the rejection reason + the token's unverified iss/aud (not PII)
            # so audience/issuer mismatches are diagnosable in production.
            try:
                import jwt as _jwt
                unv = _jwt.decode(token, options={"verify_signature": False})
                log.warning(
                    "auth failure ip=%s reason=%s token_iss=%s token_aud=%s",
                    client, exc, unv.get("iss"), unv.get("aud"),
                )
            except Exception:
                log.warning("auth failure ip=%s reason=%s (token not a JWT)", client, exc)
            return self._unauth({"error": "invalid_token"})
        except Exception:
            log.exception("verifier error path=%s", request.url.path)
            return JSONResponse({"error": "server_error"}, status_code=503)
        uid = claims["sub"]
        email = claims.get("email")
        name = claims.get("name")
        # Enrich from userinfo once per user (the access token lacks email).
        if self._userinfo_url and uid not in self._enriched:
            info = await auth_mod.fetch_userinfo(self._userinfo_url, token)
            if info:
                email = info.get("email") or email
                name = (
                    info.get("name")
                    or " ".join(p for p in (info.get("given_name"), info.get("family_name")) if p)
                    or " ".join(p for p in (info.get("first_name"), info.get("last_name")) if p)
                    or name
                ) or None
            self._enriched.add(uid)
        tok = identity.set_current_user(uid, email, name)
        try:
            await store_pg.upsert_user(uid, email, name)
            return await call_next(request)
        finally:
            identity.reset(tok)


# ---------------------------------------------------------------------------
# ASGI app builder
# ---------------------------------------------------------------------------

def build_app(cfg: CloudConfig, verifier, on_startup=None, userinfo_url=None) -> Starlette:
    from contextlib import asynccontextmanager

    mcp = build_mcp(cfg)
    mcp_app = mcp.streamable_http_app()  # ASGI sub-app

    async def well_known(request):
        return JSONResponse(auth_mod.resource_metadata(
            cfg.base_url, authorization_servers=[_idp_issuer(cfg)]
        ))

    async def healthz(request):
        return JSONResponse({"ok": True})

    async def icon_svg(request):
        # Public brand mark. Hosts fetch this (unauthenticated) to show the
        # Wingman icon next to tool calls instead of the "W" initial.
        from starlette.responses import Response
        from ..ui.resource import icon_svg as _icon_svg
        return Response(
            _icon_svg(),
            media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    async def icon_png(request):
        # PNG variant of the brand mark. Clients whose connector cards render a
        # raster icon (ChatGPT among them) fetch this unauthenticated URL.
        from starlette.responses import Response
        from ..ui.resource import icon_png_bytes
        return Response(
            icon_png_bytes(),
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    async def admin_stats(request):
        # Operator-only, content-free metrics. Guarded by ADMIN_TOKEN (constant
        # time compare); returns 404 when unset so the route is effectively off.
        import hmac
        token = os.environ.get("ADMIN_TOKEN")
        if not token:
            return JSONResponse({"error": "not_found"}, status_code=404)
        supplied = request.headers.get("x-admin-token", "")
        if not hmac.compare_digest(supplied, token):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return JSONResponse(await store_pg.global_stats())

    @asynccontextmanager
    async def lifespan(app):
        # Run caller startup (e.g. DB pool creation) first, then enter the MCP
        # streamable-http session manager, which the mounted sub-app REQUIRES.
        # Mounting a sub-app does not run its lifespan automatically, so we run
        # it from the parent here; without this, MCP calls fail with
        # "Task group is not initialized".
        if on_startup is not None:
            await on_startup()
        async with mcp_app.router.lifespan_context(app):
            yield

    routes = [
        Route("/.well-known/oauth-protected-resource", well_known),
        Route("/healthz", healthz),
        Route("/icon.svg", icon_svg),
        Route("/icon.png", icon_png),
        Route("/admin/stats", admin_stats),
    ]
    app = Starlette(routes=routes, lifespan=lifespan)
    app.mount("/", mcp_app)

    # Middleware ordering (Starlette: last added = outermost = first inbound).
    # Final inbound chain:
    #   CORS -> body-limit -> auth (sets identity) -> rate-limit (reads identity)
    #     -> security-headers -> app
    #
    # Achieved by adding in this order (innermost first):
    #   1. hardening.apply_inner  -> SecurityHeaders, RateLimit
    #   2. AuthMiddleware         -> wraps RateLimit, sets identity via context var
    #   3. hardening.apply_outer  -> BodyLimit, CORSMiddleware (outermost)
    hardening.apply_inner(app, cfg)
    app.add_middleware(
        AuthMiddleware, verifier=verifier,
        public_paths={"/healthz", "/icon.svg", "/icon.png", "/.well-known/oauth-protected-resource", "/admin/stats"},
        resource_metadata_url=f"{cfg.base_url}/.well-known/oauth-protected-resource",
        userinfo_url=userinfo_url,
    )
    hardening.apply_outer(app, cfg)
    return app


def _idp_issuer(cfg: CloudConfig) -> str:
    # WorkOS issuer for the configured client. For AuthKit this is the AuthKit
    # domain. Wired from env var WORKOS_ISSUER. Trailing slash stripped so it
    # matches the token's `iss` claim exactly (the verifier compares exactly).
    return os.environ.get("WORKOS_ISSUER", "https://api.workos.com").rstrip("/")
