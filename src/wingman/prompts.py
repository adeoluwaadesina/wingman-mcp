"""Send-to-chat prompt templates used by `_ui_get_*_prompt` tools.

Wording is tunable here without touching the iframe code. The UI fetches
the rendered prompt text and injects it as the next user turn via
``app.sendMessage``.

The templates are intentionally lean: Claude already has the plan state in
context (it just rendered the panel), so re-enumerating completed/pending
tasks only wastes tokens.
"""
from __future__ import annotations

from .storage import db
from .storage.models import Task


RUN_TASK_PROMPT = """Help me work on this task from my **{plan_name}** plan: {task_content}

When complete, call tick_task with plan_name="{plan_name}" and task_id={task_id}.
"""


BUILD_FROM_CHAT_PROMPT = """Look back through our conversation and build out my **{plan_name}** plan. Identify concrete, distinct, actionable tasks I've mentioned or implied, then call add_tasks(plan_name="{plan_name}"). Ask me before adding anything you're unsure about.
"""


def render_run_task_prompt(plan_name: str, task_id: int) -> str:
    plan = db.get_plan(plan_name)
    task: Task | None = next((t for t in plan.tasks if t.id == task_id), None)
    if task is None:
        raise db.TaskNotFound(f"task {task_id} not found in plan '{plan_name}'")
    return RUN_TASK_PROMPT.format(
        plan_name=plan.name,
        task_content=task.content,
        task_id=task.id,
    )


def render_build_from_chat_prompt(plan_name: str) -> str:
    plan = db.get_plan(plan_name)  # validates existence
    return BUILD_FROM_CHAT_PROMPT.format(plan_name=plan.name)
