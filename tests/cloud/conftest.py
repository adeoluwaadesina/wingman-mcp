# tests/cloud/conftest.py
import os
import pytest_asyncio
from wingman.cloud import store_pg

TEST_DSN = os.environ.get("WINGMAN_TEST_DSN")


@pytest_asyncio.fixture
async def pg_pool():
    if not TEST_DSN:
        import pytest
        pytest.skip("set WINGMAN_TEST_DSN to run cloud storage tests")
    pool = await store_pg.create_pool(TEST_DSN)
    await store_pg.init_db(pool)
    store_pg.set_pool(pool)
    # clean slate for each test
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE tasks, plans, users RESTART IDENTITY CASCADE")
    try:
        yield pool
    finally:
        await pool.close()
