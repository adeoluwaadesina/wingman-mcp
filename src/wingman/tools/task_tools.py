"""High-level task operations."""
from __future__ import annotations

from typing import Any, Literal

from ..storage import db
from .plan_tools import plan_to_dict, task_to_dict

TaskStatus = Literal["pending", "in_progress", "done", "blocked"]


def add_task(plan_name: str, content: str) -> dict[str, Any]:
    task = db.add_task(plan_name, content)
    return {
        "text": f"Added task {task.id} to '{plan_name}': {task.content}",
        "task": task_to_dict(task),
    }


def add_tasks(plan_name: str, tasks: list[str]) -> dict[str, Any]:
    created = db.add_tasks(plan_name, tasks)
    return {
        "text": f"Added {len(created)} task(s) to '{plan_name}'.",
        "tasks": [task_to_dict(t) for t in created],
    }


def tick_task(plan_name: str, task_id: int) -> dict[str, Any]:
    task = db.tick_task(plan_name, task_id)
    return {
        "text": f"Marked task {task.id} done in '{plan_name}': {task.content}",
        "task": task_to_dict(task),
    }


def update_task_status(plan_name: str, task_id: int, status: TaskStatus) -> dict[str, Any]:
    task = db.update_task_status(plan_name, task_id, status)
    return {
        "text": f"Set task {task.id} in '{plan_name}' to {status}.",
        "task": task_to_dict(task),
    }


def reorder_tasks(plan_name: str, ordered_ids: list[int]) -> dict[str, Any]:
    plan = db.reorder_tasks(plan_name, ordered_ids)
    return {
        "text": f"Reordered {len(plan.tasks)} task(s) in '{plan_name}'.",
        "plan": plan_to_dict(plan),
    }


def delete_task(plan_name: str, task_id: int) -> dict[str, Any]:
    db.delete_task(plan_name, task_id)
    return {"text": f"Deleted task {task_id} from '{plan_name}'."}
