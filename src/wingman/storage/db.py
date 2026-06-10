"""SQLite storage layer for Wingman plans and tasks.

All interaction with the database goes through this module. Plan-name
validation is enforced here as a defence-in-depth measure on top of the
tool-level Pydantic validators.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from .. import config
from .models import Plan, Task, TaskStatus, validate_plan_name

SCHEMA = """
CREATE TABLE IF NOT EXISTS plans (
    name        TEXT PRIMARY KEY,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tasks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_name    TEXT NOT NULL REFERENCES plans(name) ON DELETE CASCADE,
    content      TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'in_progress', 'done', 'blocked')),
    sort_order   INTEGER NOT NULL,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_plan ON tasks(plan_name, sort_order);
"""

VALID_STATUSES: set[str] = {"pending", "in_progress", "done", "blocked"}

_DB_PATH: Path | None = None


def _path() -> Path:
    return _DB_PATH if _DB_PATH is not None else config.db_path()


def set_db_path(path: Path | None) -> None:
    """Override the database path (mostly for tests)."""
    global _DB_PATH
    _DB_PATH = path


def _parse_dt(value) -> datetime:
    if isinstance(value, datetime):
        return value
    if value is None:
        return datetime.utcnow()
    # SQLite returns ISO-ish strings; trim fractional seconds tolerance
    return datetime.fromisoformat(str(value).replace("Z", "+00:00").split(".")[0])


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(_path())
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------

class PlanExists(Exception):
    pass


class PlanNotFound(Exception):
    pass


class TaskNotFound(Exception):
    pass


def create_plan(name: str, tasks: list[str] | None = None) -> Plan:
    name = validate_plan_name(name)
    tasks = tasks or []
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT 1 FROM plans WHERE name = ?", (name,)).fetchone()
        if row is not None:
            raise PlanExists(f"plan '{name}' already exists")
        conn.execute("INSERT INTO plans(name) VALUES (?)", (name,))
        for idx, content in enumerate(tasks):
            content = (content or "").strip()
            if not content:
                continue
            if len(content) > 2000:
                raise ValueError("task content must be 1-2000 chars")
            conn.execute(
                "INSERT INTO tasks(plan_name, content, status, sort_order) VALUES (?,?,?,?)",
                (name, content, "pending", idx),
            )
    return get_plan(name)


def get_plan(name: str) -> Plan:
    name = validate_plan_name(name)
    init_db()
    with connect() as conn:
        prow = conn.execute(
            "SELECT name, created_at, updated_at FROM plans WHERE name = ?", (name,)
        ).fetchone()
        if prow is None:
            raise PlanNotFound(f"plan '{name}' not found")
        trows = conn.execute(
            "SELECT id, plan_name, content, status, sort_order, created_at, updated_at, completed_at "
            "FROM tasks WHERE plan_name = ? ORDER BY sort_order ASC, id ASC",
            (name,),
        ).fetchall()
    return Plan(
        name=prow["name"],
        created_at=_parse_dt(prow["created_at"]),
        updated_at=_parse_dt(prow["updated_at"]),
        tasks=[
            Task(
                id=r["id"],
                plan_name=r["plan_name"],
                content=r["content"],
                status=r["status"],
                sort_order=r["sort_order"],
                position=idx + 1,
                created_at=_parse_dt(r["created_at"]),
                updated_at=_parse_dt(r["updated_at"]),
                completed_at=_parse_dt(r["completed_at"]) if r["completed_at"] else None,
            )
            for idx, r in enumerate(trows)
        ],
    )


def list_plans() -> list[dict]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT p.name AS name,
                   COUNT(t.id) AS total,
                   SUM(CASE WHEN t.status = 'done' THEN 1 ELSE 0 END) AS done
            FROM plans p
            LEFT JOIN tasks t ON t.plan_name = p.name
            GROUP BY p.name
            ORDER BY p.updated_at DESC, p.name ASC
            """
        ).fetchall()
    return [
        {"name": r["name"], "total": r["total"] or 0, "done": r["done"] or 0}
        for r in rows
    ]


def rename_plan(current_name: str, new_name: str) -> Plan:
    current_name = validate_plan_name(current_name)
    new_name = validate_plan_name(new_name)
    if current_name == new_name:
        return get_plan(current_name)
    init_db()
    with connect() as conn:
        if conn.execute("SELECT 1 FROM plans WHERE name = ?", (current_name,)).fetchone() is None:
            raise PlanNotFound(f"plan '{current_name}' not found")
        if conn.execute("SELECT 1 FROM plans WHERE name = ?", (new_name,)).fetchone() is not None:
            raise PlanExists(f"plan '{new_name}' already exists")
        # foreign key with ON DELETE CASCADE — update child rows manually
        conn.execute("INSERT INTO plans(name, created_at) SELECT ?, created_at FROM plans WHERE name = ?", (new_name, current_name))
        conn.execute("UPDATE tasks SET plan_name = ? WHERE plan_name = ?", (new_name, current_name))
        conn.execute("DELETE FROM plans WHERE name = ?", (current_name,))
        conn.execute("UPDATE plans SET updated_at = CURRENT_TIMESTAMP WHERE name = ?", (new_name,))
    return get_plan(new_name)


def delete_plan(name: str) -> None:
    name = validate_plan_name(name)
    init_db()
    with connect() as conn:
        cur = conn.execute("DELETE FROM plans WHERE name = ?", (name,))
        if cur.rowcount == 0:
            raise PlanNotFound(f"plan '{name}' not found")


def _touch_plan(conn: sqlite3.Connection, name: str) -> None:
    conn.execute("UPDATE plans SET updated_at = CURRENT_TIMESTAMP WHERE name = ?", (name,))


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

def _next_sort_order(conn: sqlite3.Connection, plan_name: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(sort_order), -1) + 1 AS nxt FROM tasks WHERE plan_name = ?",
        (plan_name,),
    ).fetchone()
    return int(row["nxt"])


def add_task(plan_name: str, content: str) -> Task:
    plan_name = validate_plan_name(plan_name)
    content = (content or "").strip()
    if not content:
        raise ValueError("task content must be 1-2000 chars")
    if len(content) > 2000:
        raise ValueError("task content must be 1-2000 chars")
    init_db()
    with connect() as conn:
        if conn.execute("SELECT 1 FROM plans WHERE name = ?", (plan_name,)).fetchone() is None:
            raise PlanNotFound(f"plan '{plan_name}' not found")
        order = _next_sort_order(conn, plan_name)
        cur = conn.execute(
            "INSERT INTO tasks(plan_name, content, status, sort_order) VALUES (?,?,?,?)",
            (plan_name, content, "pending", order),
        )
        task_id = cur.lastrowid
        _touch_plan(conn, plan_name)
    return _get_task(task_id)


def add_tasks(plan_name: str, contents: list[str]) -> list[Task]:
    plan_name = validate_plan_name(plan_name)
    cleaned: list[str] = []
    for c in contents:
        c = (c or "").strip()
        if not c:
            continue
        if len(c) > 2000:
            raise ValueError("task content must be 1-2000 chars")
        cleaned.append(c)
    if not cleaned:
        return []
    init_db()
    new_ids: list[int] = []
    with connect() as conn:
        if conn.execute("SELECT 1 FROM plans WHERE name = ?", (plan_name,)).fetchone() is None:
            raise PlanNotFound(f"plan '{plan_name}' not found")
        order = _next_sort_order(conn, plan_name)
        for content in cleaned:
            cur = conn.execute(
                "INSERT INTO tasks(plan_name, content, status, sort_order) VALUES (?,?,?,?)",
                (plan_name, content, "pending", order),
            )
            new_ids.append(int(cur.lastrowid))
            order += 1
        _touch_plan(conn, plan_name)
    return [_get_task(i) for i in new_ids]


def _get_task(task_id: int) -> Task:
    with connect() as conn:
        r = conn.execute(
            "SELECT id, plan_name, content, status, sort_order, created_at, updated_at, completed_at "
            "FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
    if r is None:
        raise TaskNotFound(f"task {task_id} not found")
    return Task(
        id=r["id"],
        plan_name=r["plan_name"],
        content=r["content"],
        status=r["status"],
        sort_order=r["sort_order"],
        created_at=_parse_dt(r["created_at"]),
        updated_at=_parse_dt(r["updated_at"]),
        completed_at=_parse_dt(r["completed_at"]) if r["completed_at"] else None,
    )


def update_task_status(plan_name: str, task_id: int, status: TaskStatus) -> Task:
    plan_name = validate_plan_name(plan_name)
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {status}")
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM tasks WHERE id = ? AND plan_name = ?",
            (task_id, plan_name),
        ).fetchone()
        if row is None:
            raise TaskNotFound(f"task {task_id} not found in plan '{plan_name}'")
        if status == "done":
            conn.execute(
                "UPDATE tasks SET status = ?, completed_at = CURRENT_TIMESTAMP, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (status, task_id),
            )
        else:
            conn.execute(
                "UPDATE tasks SET status = ?, completed_at = NULL, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (status, task_id),
            )
        _touch_plan(conn, plan_name)
    return _get_task(task_id)


def tick_task(plan_name: str, task_id: int) -> Task:
    return update_task_status(plan_name, task_id, "done")


def delete_task(plan_name: str, task_id: int) -> None:
    plan_name = validate_plan_name(plan_name)
    init_db()
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM tasks WHERE id = ? AND plan_name = ?",
            (task_id, plan_name),
        )
        if cur.rowcount == 0:
            raise TaskNotFound(f"task {task_id} not found in plan '{plan_name}'")
        _touch_plan(conn, plan_name)


def reorder_tasks(plan_name: str, ordered_ids: list[int]) -> Plan:
    plan_name = validate_plan_name(plan_name)
    init_db()
    with connect() as conn:
        if conn.execute("SELECT 1 FROM plans WHERE name = ?", (plan_name,)).fetchone() is None:
            raise PlanNotFound(f"plan '{plan_name}' not found")
        existing = conn.execute(
            "SELECT id FROM tasks WHERE plan_name = ?", (plan_name,)
        ).fetchall()
        existing_ids = {int(r["id"]) for r in existing}
        provided = [int(i) for i in ordered_ids]
        if set(provided) != existing_ids:
            raise ValueError(
                "reorder_tasks requires every task id of the plan, exactly once"
            )
        for idx, tid in enumerate(provided):
            conn.execute(
                "UPDATE tasks SET sort_order = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (idx, tid),
            )
        _touch_plan(conn, plan_name)
    return get_plan(plan_name)


def clear_completed(plan_name: str) -> int:
    plan_name = validate_plan_name(plan_name)
    init_db()
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM tasks WHERE plan_name = ? AND status = 'done'",
            (plan_name,),
        )
        _touch_plan(conn, plan_name)
        return cur.rowcount


def clear_all_tasks(plan_name: str) -> int:
    plan_name = validate_plan_name(plan_name)
    init_db()
    with connect() as conn:
        cur = conn.execute("DELETE FROM tasks WHERE plan_name = ?", (plan_name,))
        _touch_plan(conn, plan_name)
        return cur.rowcount
