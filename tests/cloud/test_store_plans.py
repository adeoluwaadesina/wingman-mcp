# tests/cloud/test_store_plans.py
import pytest
from wingman.cloud import store_pg

pytestmark = pytest.mark.asyncio
A, B = "user_A", "user_B"

async def _seed_users(pool):
    await store_pg.upsert_user(A, "a@x.com", "Alice")
    await store_pg.upsert_user(B, "b@x.com", "Bob")

async def test_upsert_user_idempotent_and_counts(pg_pool):
    await store_pg.upsert_user(A, "a@x.com", "Alice")
    await store_pg.upsert_user(A, "a2@x.com", "Alice2")  # update
    assert await store_pg.count_users() == 1

async def test_create_and_get_plan(pg_pool):
    await _seed_users(pg_pool)
    await store_pg.create_plan(A, "Roadmap", ["t1", "t2"], max_plans=100, max_tasks=500)
    plan = await store_pg.get_plan(A, "Roadmap")
    assert plan["name"] == "Roadmap"
    assert [t["content"] for t in plan["tasks"]] == ["t1", "t2"]
    assert plan["tasks"][0]["position"] == 1

async def test_two_users_share_a_plan_name(pg_pool):
    await _seed_users(pg_pool)
    await store_pg.create_plan(A, "Roadmap", ["a-task"], max_plans=100, max_tasks=500)
    await store_pg.create_plan(B, "Roadmap", ["b-task"], max_plans=100, max_tasks=500)
    assert (await store_pg.get_plan(A, "Roadmap"))["tasks"][0]["content"] == "a-task"
    assert (await store_pg.get_plan(B, "Roadmap"))["tasks"][0]["content"] == "b-task"

async def test_user_b_cannot_read_user_a_plan(pg_pool):
    await _seed_users(pg_pool)
    await store_pg.create_plan(A, "Secret", ["x"], max_plans=100, max_tasks=500)
    with pytest.raises(store_pg.PlanNotFound):
        await store_pg.get_plan(B, "Secret")

async def test_user_b_cannot_delete_user_a_plan(pg_pool):
    await _seed_users(pg_pool)
    await store_pg.create_plan(A, "Secret", ["x"], max_plans=100, max_tasks=500)
    with pytest.raises(store_pg.PlanNotFound):
        await store_pg.delete_plan(B, "Secret")
    # A's plan still intact
    assert (await store_pg.get_plan(A, "Secret"))["name"] == "Secret"

async def test_plan_quota_enforced(pg_pool):
    await _seed_users(pg_pool)
    await store_pg.create_plan(A, "P1", [], max_plans=1, max_tasks=500)
    with pytest.raises(store_pg.QuotaExceeded):
        await store_pg.create_plan(A, "P2", [], max_plans=1, max_tasks=500)

async def test_rename_is_scoped_and_atomic(pg_pool):
    await _seed_users(pg_pool)
    await store_pg.create_plan(A, "Old", ["keep"], max_plans=100, max_tasks=500)
    await store_pg.rename_plan(A, "Old", "New")
    assert (await store_pg.get_plan(A, "New"))["tasks"][0]["content"] == "keep"
    with pytest.raises(store_pg.PlanNotFound):
        await store_pg.get_plan(A, "Old")

async def test_list_plans_scoped(pg_pool):
    await _seed_users(pg_pool)
    await store_pg.create_plan(A, "A1", ["x"], max_plans=100, max_tasks=500)
    await store_pg.create_plan(B, "B1", [], max_plans=100, max_tasks=500)
    names = {p["name"] for p in await store_pg.list_plans(A)}
    assert names == {"A1"}
