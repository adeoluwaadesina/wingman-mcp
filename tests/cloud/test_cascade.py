"""The FK ON DELETE CASCADE and rename re-point are load-bearing - test them
directly rather than relying on indirect coverage."""
import pytest

from wingman.cloud import store_pg

pytestmark = pytest.mark.asyncio
A = "user_A"


async def _tasks_in(pool, user_id, plan_name):
    async with pool.acquire() as conn:
        return int(await conn.fetchval(
            "SELECT count(*) FROM tasks WHERE user_id = $1 AND plan_name = $2",
            user_id, plan_name,
        ))


async def test_delete_plan_cascades_its_tasks_only(pg_pool):
    await store_pg.upsert_user(A, None, None)
    await store_pg.create_plan(A, "P", ["t1", "t2"], max_plans=100, max_tasks=500)
    await store_pg.create_plan(A, "Q", ["keep"], max_plans=100, max_tasks=500)

    await store_pg.delete_plan(A, "P")

    with pytest.raises(store_pg.PlanNotFound):
        await store_pg.get_plan(A, "P")
    # P's tasks are cascaded away - no orphans left in the tasks table.
    assert await _tasks_in(pg_pool, A, "P") == 0
    # The sibling plan and its task are untouched.
    assert await _tasks_in(pg_pool, A, "Q") == 1


async def test_rename_plan_repoints_tasks(pg_pool):
    await store_pg.upsert_user(A, None, None)
    await store_pg.create_plan(A, "Old", ["one", "two"], max_plans=100, max_tasks=500)

    await store_pg.rename_plan(A, "Old", "New")

    assert await _tasks_in(pg_pool, A, "Old") == 0
    assert await _tasks_in(pg_pool, A, "New") == 2
    assert [t["content"] for t in (await store_pg.get_plan(A, "New"))["tasks"]] == ["one", "two"]
