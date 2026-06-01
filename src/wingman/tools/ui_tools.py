"""UI-only tools (visibility = ["app"]).

These are called by the iframe via the MCP Apps ``callServerTool`` bridge.
Underscore prefix marks them as internal in the codebase even though they
are still registered with the MCP server.
"""
from __future__ import annotations

from typing import Any, Literal

from .. import prompts
from ..storage import db
from . import plan_tools, task_tools

TaskStatus = Literal["pending", "in_progress", "done", "blocked"]


def get_plan(plan_name: str) -> dict[str, Any]:
    return plan_tools.show_plan(plan_name)


def tick_task(plan_name: str, task_id: int) -> dict[str, Any]:
    return task_tools.tick_task(plan_name, task_id)


def update_status(plan_name: str, task_id: int, status: TaskStatus) -> dict[str, Any]:
    return task_tools.update_task_status(plan_name, task_id, status)


def delete_task(plan_name: str, task_id: int) -> dict[str, Any]:
    return task_tools.delete_task(plan_name, task_id)


def add_task(plan_name: str, content: str) -> dict[str, Any]:
    return task_tools.add_task(plan_name, content)


def rename_plan(current_name: str, new_name: str) -> dict[str, Any]:
    return plan_tools.rename_plan(current_name, new_name)


def reorder_tasks(plan_name: str, ordered_ids: list[int]) -> dict[str, Any]:
    return task_tools.reorder_tasks(plan_name, ordered_ids)


def clear_completed(plan_name: str) -> dict[str, Any]:
    n = db.clear_completed(plan_name)
    return {"text": f"Cleared {n} completed task(s).", "removed": n}


def clear_all(plan_name: str) -> dict[str, Any]:
    n = db.clear_all_tasks(plan_name)
    return {"text": f"Cleared {n} task(s).", "removed": n}


def delete_plan(plan_name: str) -> dict[str, Any]:
    return plan_tools.delete_plan(plan_name)


def export_markdown(plan_name: str) -> dict[str, Any]:
    plan = db.get_plan(plan_name)
    md = plan_tools.export_markdown(plan)
    return {"text": md, "markdown": md}


def get_run_task_prompt(plan_name: str, task_id: int) -> dict[str, Any]:
    text = prompts.render_run_task_prompt(plan_name, task_id)
    # Also flip the task into in_progress as a side-effect — the user just
    # said "start this", so the panel should reflect that immediately.
    try:
        task_tools.update_task_status(plan_name, task_id, "in_progress")
    except Exception:
        pass
    return {"text": text, "prompt": text}


def get_build_from_chat_prompt(plan_name: str) -> dict[str, Any]:
    text = prompts.render_build_from_chat_prompt(plan_name)
    return {"text": text, "prompt": text}
