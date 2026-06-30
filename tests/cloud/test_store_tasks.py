# tests/cloud/test_store_tasks.py
import pytest
from wingman.cloud import store_pg

pytestmark = pytest.mark.asyncio
A, B = "user_A", "user_B"

async def _setup(pool):
    await store_pg.upsert_user(A, "a@x.com", "Alice")
    await store_pg.upsert_user(B, "b@x.com", "Bob")
    await store_pg.create_plan(A, "P", ["one", "two"], max_plans=100, max_tasks=500)

async def _ids(user, plan):
    return [t["id"] for t in (await store_pg.get_plan(user, plan))["tasks"]]

async def test_add_task(pg_pool):
    await _setup(pg_pool)
    await store_pg.add_task(A, "P", "three", max_tasks=500)
    assert [t["content"] for t in (await store_pg.get_plan(A, "P"))["tasks"]] == ["one", "two", "three"]

async def test_add_tasks_batch_cap(pg_pool):
    await _setup(pg_pool)
    with pytest.raises(store_pg.QuotaExceeded):
        await store_pg.add_tasks(A, "P", ["a", "b", "c"], max_tasks=500, max_batch=2)

async def test_add_task_plan_quota(pg_pool):
    await _setup(pg_pool)
    with pytest.raises(store_pg.QuotaExceeded):
        await store_pg.add_task(A, "P", "overflow", max_tasks=2)

async def test_tick_task(pg_pool):
    await _setup(pg_pool)
    tid = (await _ids(A, "P"))[0]
    res = await store_pg.tick_task(A, "P", tid)
    assert res["status"] == "done"
    assert res["completed_at"] is not None

async def test_user_b_cannot_tick_user_a_task(pg_pool):
    await _setup(pg_pool)
    tid = (await _ids(A, "P"))[0]
    # B owns no plan "P"; even with A's real task id, ownership fails
    await store_pg.upsert_user(B, None, None)
    with pytest.raises(store_pg.TaskNotFound):
        await store_pg.tick_task(B, "P", tid)
    # A's task still pending
    assert (await store_pg.get_plan(A, "P"))["tasks"][0]["status"] == "pending"

async def test_user_b_cannot_delete_user_a_task(pg_pool):
    await _setup(pg_pool)
    tid = (await _ids(A, "P"))[0]
    with pytest.raises(store_pg.TaskNotFound):
        await store_pg.delete_task(B, "P", tid)
    assert len((await store_pg.get_plan(A, "P"))["tasks"]) == 2

async def test_reorder(pg_pool):
    await _setup(pg_pool)
    ids = await _ids(A, "P")
    await store_pg.reorder_tasks(A, "P", list(reversed(ids)))
    assert [t["id"] for t in (await store_pg.get_plan(A, "P"))["tasks"]] == list(reversed(ids))

async def test_reorder_rejects_foreign_ids(pg_pool):
    await _setup(pg_pool)
    with pytest.raises(ValueError):
        await store_pg.reorder_tasks(A, "P", [999999])

async def test_clear_completed(pg_pool):
    await _setup(pg_pool)
    ids = await _ids(A, "P")
    await store_pg.tick_task(A, "P", ids[0])
    removed = await store_pg.clear_completed(A, "P")
    assert removed == 1
    assert len((await store_pg.get_plan(A, "P"))["tasks"]) == 1
