"""High-level operations for plans (used by both LLM-visible and UI tools).

Functions return plain dict payloads so they can be serialized directly into
MCP tool results. A ``text`` field is always included so hosts without MCP
Apps support still get a useful response.
"""
from __future__ import annotations

from typing import Any

from ..storage import db
from ..storage.models import Plan, Task


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

STATUS_ICONS = {
    "pending": "[ ]",
    "in_progress": "[~]",
    "done": "[x]",
    "blocked": "[!]",
}


def plan_to_dict(plan: Plan) -> dict[str, Any]:
    return {
        "name": plan.name,
        "created_at": plan.created_at.isoformat(),
        "updated_at": plan.updated_at.isoformat(),
        "counts": plan.counts,
        "tasks": [task_to_dict(t) for t in plan.tasks],
    }


def task_to_dict(t: Task) -> dict[str, Any]:
    return {
        "id": t.id,
        "position": t.position,
        "content": t.content,
        "status": t.status,
        "sort_order": t.sort_order,
        "completed_at": t.completed_at.isoformat() if t.completed_at else None,
    }


def format_plan_text(plan: Plan) -> str:
    """Markdown rendering used as text fallback for hosts without MCP Apps."""
    c = plan.counts
    lines = [
        f"## {plan.name}",
        f"_{c['done']} of {c['total']} done · {c['in_progress']} in progress · {c['pending']} pending_",
        "",
    ]
    if not plan.tasks:
        lines.append("_No tasks yet._")
    else:
        for t in plan.tasks:
            icon = STATUS_ICONS.get(t.status, "[ ]")
            lines.append(f"{icon} {t.position}. {t.content}")
    return "\n".join(lines)


def export_markdown(plan: Plan) -> str:
    """GitHub-flavored markdown checklist for clipboard export."""
    lines = [f"# {plan.name}", ""]
    for t in plan.tasks:
        mark = STATUS_ICONS.get(t.status, "[ ]")
        lines.append(f"- {mark} {t.content}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------

def create_plan(name: str, tasks: list[str] | None = None) -> dict[str, Any]:
    plan = db.create_plan(name, tasks or [])
    return {
        "text": f"Created plan '{plan.name}' with {len(plan.tasks)} task(s).",
        "plan": plan_to_dict(plan),
    }


def get_plan(name: str) -> dict[str, Any]:
    plan = db.get_plan(name)
    return {
        "text": format_plan_text(plan),
        "plan": plan_to_dict(plan),
    }


def show_plan(name: str) -> dict[str, Any]:
    plan = db.get_plan(name)
    return {
        "text": format_plan_text(plan),
        "plan": plan_to_dict(plan),
    }


def list_plans() -> dict[str, Any]:
    rows = db.list_plans()
    if not rows:
        text = "No plans yet. Create one with `create_plan`."
    else:
        lines = ["Plans:"]
        for r in rows:
            lines.append(f"- {r['name']} — {r['done']}/{r['total']} done")
        text = "\n".join(lines)
    return {"text": text, "plans": rows}


def rename_plan(current_name: str, new_name: str) -> dict[str, Any]:
    plan = db.rename_plan(current_name, new_name)
    return {
        "text": f"Renamed plan '{current_name}' to '{plan.name}'.",
        "plan": plan_to_dict(plan),
    }


def delete_plan(name: str) -> dict[str, Any]:
    db.delete_plan(name)
    return {"text": f"Deleted plan '{name}'."}
