# tests/cloud/test_store_schema.py
import pytest
from wingman.cloud import store_pg

pytestmark = pytest.mark.asyncio


async def test_init_db_creates_tables(pg_pool):
    async with pg_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public'"
        )
    names = {r["table_name"] for r in rows}
    assert {"users", "plans", "tasks"} <= names


async def test_init_db_idempotent(pg_pool):
    # running init twice must not error
    await store_pg.init_db(pg_pool)
    await store_pg.init_db(pg_pool)
