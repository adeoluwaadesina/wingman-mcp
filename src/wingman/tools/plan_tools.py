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


def _progress_bar(done: int, total: int, width: int = 22) -> str:
    """A unicode meter, e.g. ``████████░░░░░░░░░░░░░░``."""
    if total <= 0:
        return "░" * width
    filled = round(width * done / total)
    return "█" * filled + "░" * (width - filled)


def _phase_label(content: str) -> str | None:
    """The grouping label for a task: the short token before the first colon
    (e.g. ``PHASE 1``, ``SECURITY``, ``Zeli``), or ``None`` when the task has no
    such prefix. Sentences that merely contain a colon are not treated as labels."""
    head, sep, _rest = content.partition(":")
    if sep and 0 < len(head) <= 24 and "." not in head:
        return head.strip()
    return None


def format_plan_text(plan: Plan) -> str:
    """Markdown rendering used as text fallback for hosts without MCP Apps (e.g.
    Claude Code CLI). Adds a progress bar and groups prefixed tasks under their
    phase so a long plan stays scannable in plain text."""
    c = plan.counts
    pct = round(100 * c["done"] / c["total"]) if c["total"] else 0
    lines = [
        f"## {plan.name}",
        f"`{_progress_bar(c['done'], c['total'])}`  {c['done']}/{c['total']} done ({pct}%)",
    ]
    detail = []
    if c["in_progress"]:
        detail.append(f"{c['in_progress']} in progress")
    if c["blocked"]:
        detail.append(f"{c['blocked']} blocked")
    if c["pending"]:
        detail.append(f"{c['pending']} pending")
    if detail:
        lines.append("_" + " · ".join(detail) + "_")
    lines.append("")

    if not plan.tasks:
        lines.append("_No tasks yet._")
        return "\n".join(lines)

    sentinel: object = object()
    current: object = sentinel
    for t in plan.tasks:
        label = _phase_label(t.content)
        if label != current:
            # Space out group changes so a header (or a return to ungrouped tasks)
            # is visually separated from the previous group.
            if current is not sentinel:
                lines.append("")
            if label is not None:
                lines.append(f"**{label}**")
            current = label
        icon = STATUS_ICONS.get(t.status, "[ ]")
        text = t.content.split(":", 1)[1].strip() if label is not None else t.content
        lines.append(f"{icon} {t.position}. {text}")
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
