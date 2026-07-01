"""The migration tool copies local SQLite plans into the cloud, scoped to a user."""
import os

import pytest

from wingman.cloud import migrate, store_pg
from wingman.storage import db as local_db

pytestmark = pytest.mark.asyncio
MIG_USER = "mig_user"


async def test_migrate_copies_plans_with_status(pg_pool, tmp_path, monkeypatch):
    # Point the LOCAL sqlite at a temp dir and seed a plan there.
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path))
    local_db.set_db_path(None)  # fall back to config path (uses WINGMAN_DATA_DIR)
    local_db.create_plan("Imported", ["a", "b", "c"])
    ids = [t.id for t in local_db.get_plan("Imported").tasks]
    local_db.tick_task("Imported", ids[0])

    dsn = os.environ["WINGMAN_TEST_DSN"]
    migrated, skipped = await migrate._run(dsn, MIG_USER, None, dry_run=False)
    assert migrated == ["Imported"]
    assert skipped == []

    # Verify it landed in the cloud, scoped to MIG_USER, with status preserved.
    store_pg.set_pool(pg_pool)
    plan = await store_pg.get_plan(MIG_USER, "Imported")
    assert [t["content"] for t in plan["tasks"]] == ["a", "b", "c"]
    assert plan["tasks"][0]["status"] == "done"
    assert plan["tasks"][1]["status"] == "pending"


async def test_migrate_skips_existing_plan(pg_pool, tmp_path, monkeypatch):
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path))
    local_db.set_db_path(None)
    local_db.create_plan("Dupe", ["x"])

    store_pg.set_pool(pg_pool)
    await store_pg.upsert_user(MIG_USER, None, None)
    await store_pg.create_plan(MIG_USER, "Dupe", ["already here"], max_plans=100, max_tasks=100)

    dsn = os.environ["WINGMAN_TEST_DSN"]
    migrated, skipped = await migrate._run(dsn, MIG_USER, None, dry_run=False)
    assert "Dupe" in skipped
    assert migrated == []
    # The existing cloud plan is untouched.
    store_pg.set_pool(pg_pool)
    plan = await store_pg.get_plan(MIG_USER, "Dupe")
    assert [t["content"] for t in plan["tasks"]] == ["already here"]
