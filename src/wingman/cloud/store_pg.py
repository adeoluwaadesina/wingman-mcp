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
