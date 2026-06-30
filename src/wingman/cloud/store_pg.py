"""Async Postgres persistence for Wingman Cloud.

Every function is scoped by user_id. user_id is supplied by the caller from
the validated session (see identity.py), never from a tool argument. All SQL
is parameterized. Mirrors the semantics of wingman.storage.db but multi-tenant.
"""
from __future__ import annotations

from pathlib import Path

import asyncpg

_MIGRATION = Path(__file__).resolve().parents[3] / "migrations" / "001_init.sql"
SCHEMA_SQL = _MIGRATION.read_text(encoding="utf-8")

_POOL: asyncpg.Pool | None = None


class PlanExists(Exception):
    pass


class PlanNotFound(Exception):
    pass


class TaskNotFound(Exception):
    pass


class QuotaExceeded(Exception):
    pass


async def create_pool(dsn: str) -> asyncpg.Pool:
    # statement_cache_size=0 required for Neon's PgBouncer pooler in production;
    # harmless on the direct endpoint used in tests.
    return await asyncpg.create_pool(dsn, min_size=1, max_size=10, statement_cache_size=0)


async def init_db(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_SQL)


def set_pool(pool: asyncpg.Pool) -> None:
    global _POOL
    _POOL = pool


def get_pool() -> asyncpg.Pool:
    if _POOL is None:
        raise RuntimeError("store_pg pool not initialized")
    return _POOL


from ..storage.models import Plan, Task, validate_plan_name
from ..tools import plan_tools  # reuse plan_to_dict / list serialization


async def upsert_user(user_id: str, email: str | None, display_name: str | None) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (user_id, email, display_name, last_seen_at)
            VALUES ($1, $2, $3, now())
            ON CONFLICT (user_id) DO UPDATE
              SET email = COALESCE(EXCLUDED.email, users.email),
                  display_name = COALESCE(EXCLUDED.display_name, users.display_name),
                  last_seen_at = now()
            """,
            user_id, email, display_name,
        )


async def count_users() -> int:
    pool = get_pool()
    async with pool.acquire() as conn:
        return int(await conn.fetchval("SELECT count(*) FROM users"))


def _row_to_task(r, position: int) -> Task:
    return Task(
        id=r["id"],
        plan_name=r["plan_name"],
        content=r["content"],
        status=r["status"],
        sort_order=r["sort_order"],
        position=position,
        created_at=r["created_at"],
        updated_at=r["updated_at"],
        completed_at=r["completed_at"],
    )


async def _load_plan(conn, user_id: str, name: str) -> Plan:
    prow = await conn.fetchrow(
        "SELECT name, created_at, updated_at FROM plans WHERE user_id = $1 AND name = $2",
        user_id, name,
    )
    if prow is None:
        raise PlanNotFound(f"plan '{name}' not found")
    trows = await conn.fetch(
        "SELECT id, plan_name, content, status, sort_order, created_at, updated_at, completed_at "
        "FROM tasks WHERE user_id = $1 AND plan_name = $2 ORDER BY sort_order ASC, id ASC",
        user_id, name,
    )
    return Plan(
        name=prow["name"],
        created_at=prow["created_at"],
        updated_at=prow["updated_at"],
        tasks=[_row_to_task(r, i + 1) for i, r in enumerate(trows)],
    )


async def get_plan(user_id: str, name: str) -> dict:
    name = validate_plan_name(name)
    pool = get_pool()
    async with pool.acquire() as conn:
        plan = await _load_plan(conn, user_id, name)
    return plan_tools.plan_to_dict(plan)


async def create_plan(user_id: str, name: str, tasks, *, max_plans: int, max_tasks: int) -> dict:
    name = validate_plan_name(name)
    tasks = tasks or []
    cleaned = []
    for c in tasks:
        c = (c or "").strip()
        if not c:
            continue
        if len(c) > 2000:
            raise ValueError("task content must be 1-2000 chars")
        cleaned.append(c)
    if len(cleaned) > max_tasks:
        raise QuotaExceeded(f"a plan may have at most {max_tasks} tasks")
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            count = await conn.fetchval("SELECT count(*) FROM plans WHERE user_id = $1", user_id)
            if count >= max_plans:
                raise QuotaExceeded(f"you may have at most {max_plans} plans")
            exists = await conn.fetchval(
                "SELECT 1 FROM plans WHERE user_id = $1 AND name = $2", user_id, name
            )
            if exists:
                raise PlanExists(f"plan '{name}' already exists")
            await conn.execute(
                "INSERT INTO plans (user_id, name) VALUES ($1, $2)", user_id, name
            )
            for idx, content in enumerate(cleaned):
                await conn.execute(
                    "INSERT INTO tasks (user_id, plan_name, content, status, sort_order) "
                    "VALUES ($1, $2, $3, 'pending', $4)",
                    user_id, name, content, idx,
                )
            plan = await _load_plan(conn, user_id, name)
    return plan_tools.plan_to_dict(plan)


async def list_plans(user_id: str) -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT p.name AS name,
                   count(t.id) AS total,
                   coalesce(sum(CASE WHEN t.status = 'done' THEN 1 ELSE 0 END), 0) AS done
            FROM plans p
            LEFT JOIN tasks t ON t.user_id = p.user_id AND t.plan_name = p.name
            WHERE p.user_id = $1
            GROUP BY p.name, p.updated_at
            ORDER BY p.updated_at DESC, p.name ASC
            """,
            user_id,
        )
    return [{"name": r["name"], "total": int(r["total"]), "done": int(r["done"])} for r in rows]


async def rename_plan(user_id: str, current_name: str, new_name: str) -> dict:
    current_name = validate_plan_name(current_name)
    new_name = validate_plan_name(new_name)
    if current_name == new_name:
        return await get_plan(user_id, current_name)
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            if not await conn.fetchval(
                "SELECT 1 FROM plans WHERE user_id = $1 AND name = $2", user_id, current_name
            ):
                raise PlanNotFound(f"plan '{current_name}' not found")
            if await conn.fetchval(
                "SELECT 1 FROM plans WHERE user_id = $1 AND name = $2", user_id, new_name
            ):
                raise PlanExists(f"plan '{new_name}' already exists")
            await conn.execute(
                "INSERT INTO plans (user_id, name, created_at) "
                "SELECT user_id, $3, created_at FROM plans WHERE user_id = $1 AND name = $2",
                user_id, current_name, new_name,
            )
            await conn.execute(
                "UPDATE tasks SET plan_name = $3 WHERE user_id = $1 AND plan_name = $2",
                user_id, current_name, new_name,
            )
            await conn.execute(
                "DELETE FROM plans WHERE user_id = $1 AND name = $2", user_id, current_name
            )
            await conn.execute(
                "UPDATE plans SET updated_at = now() WHERE user_id = $1 AND name = $2",
                user_id, new_name,
            )
            plan = await _load_plan(conn, user_id, new_name)
    return plan_tools.plan_to_dict(plan)


async def delete_plan(user_id: str, name: str) -> None:
    name = validate_plan_name(name)
    pool = get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM plans WHERE user_id = $1 AND name = $2", user_id, name
        )
    # asyncpg returns e.g. "DELETE 1"
    if result.split()[-1] == "0":
        raise PlanNotFound(f"plan '{name}' not found")
