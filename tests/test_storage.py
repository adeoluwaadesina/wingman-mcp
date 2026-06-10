import pytest

from wingman.storage import db
from wingman.storage.models import validate_plan_name


def test_create_and_get_plan():
    plan = db.create_plan("Launch Footprint", ["spec", "build", "ship"])
    assert plan.name == "Launch Footprint"
    assert [t.content for t in plan.tasks] == ["spec", "build", "ship"]
    assert all(t.status == "pending" for t in plan.tasks)
    # sort_order is monotonic
    assert [t.sort_order for t in plan.tasks] == [0, 1, 2]


def test_create_duplicate_plan_raises():
    db.create_plan("p", [])
    with pytest.raises(db.PlanExists):
        db.create_plan("p", [])


def test_get_missing_plan():
    with pytest.raises(db.PlanNotFound):
        db.get_plan("nope")


def test_list_plans_counts():
    db.create_plan("a", ["x", "y"])
    db.create_plan("b", ["z"])
    db.tick_task("a", db.get_plan("a").tasks[0].id)
    rows = {r["name"]: r for r in db.list_plans()}
    assert rows["a"]["total"] == 2
    assert rows["a"]["done"] == 1
    assert rows["b"]["total"] == 1
    assert rows["b"]["done"] == 0


def test_add_task_appends():
    db.create_plan("p", ["one"])
    t = db.add_task("p", "two")
    plan = db.get_plan("p")
    assert [x.content for x in plan.tasks] == ["one", "two"]
    assert t.sort_order == 1


def test_add_tasks_bulk():
    db.create_plan("p", [])
    tasks = db.add_tasks("p", ["a", "  ", "b", "c"])
    assert [t.content for t in tasks] == ["a", "b", "c"]
    assert [t.sort_order for t in tasks] == [0, 1, 2]


def test_tick_and_status_transitions():
    db.create_plan("p", ["t"])
    tid = db.get_plan("p").tasks[0].id
    t = db.tick_task("p", tid)
    assert t.status == "done"
    assert t.completed_at is not None
    t2 = db.update_task_status("p", tid, "pending")
    assert t2.status == "pending"
    assert t2.completed_at is None


def test_invalid_status():
    db.create_plan("p", ["t"])
    tid = db.get_plan("p").tasks[0].id
    with pytest.raises(ValueError):
        db.update_task_status("p", tid, "nonsense")  # type: ignore[arg-type]


def test_delete_task():
    db.create_plan("p", ["a", "b"])
    plan = db.get_plan("p")
    db.delete_task("p", plan.tasks[0].id)
    assert [t.content for t in db.get_plan("p").tasks] == ["b"]


def test_reorder_tasks():
    db.create_plan("p", ["a", "b", "c"])
    ids = [t.id for t in db.get_plan("p").tasks]
    new_order = [ids[2], ids[0], ids[1]]
    plan = db.reorder_tasks("p", new_order)
    assert [t.id for t in plan.tasks] == new_order


def test_reorder_tasks_requires_full_set():
    db.create_plan("p", ["a", "b"])
    ids = [t.id for t in db.get_plan("p").tasks]
    with pytest.raises(ValueError):
        db.reorder_tasks("p", [ids[0]])


def test_rename_plan():
    db.create_plan("old", ["t"])
    plan = db.rename_plan("old", "new")
    assert plan.name == "new"
    assert [t.content for t in plan.tasks] == ["t"]
    with pytest.raises(db.PlanNotFound):
        db.get_plan("old")


def test_rename_conflict():
    db.create_plan("a", [])
    db.create_plan("b", [])
    with pytest.raises(db.PlanExists):
        db.rename_plan("a", "b")


def test_delete_plan_cascades():
    db.create_plan("p", ["a", "b"])
    db.delete_plan("p")
    with pytest.raises(db.PlanNotFound):
        db.get_plan("p")


def test_clear_completed_and_all():
    db.create_plan("p", ["a", "b", "c"])
    plan = db.get_plan("p")
    db.tick_task("p", plan.tasks[0].id)
    db.tick_task("p", plan.tasks[1].id)
    n = db.clear_completed("p")
    assert n == 2
    assert [t.content for t in db.get_plan("p").tasks] == ["c"]
    n2 = db.clear_all_tasks("p")
    assert n2 == 1
    assert db.get_plan("p").tasks == []


def test_validate_plan_name():
    assert validate_plan_name("Launch Footprint") == "Launch Footprint"
    assert validate_plan_name("  spaced  ") == "spaced"
    # v0.2 widened: apostrophe, period, colon, parentheses
    assert validate_plan_name("Adeolu's plan") == "Adeolu's plan"
    assert validate_plan_name("Q1 2026 launch") == "Q1 2026 launch"
    assert validate_plan_name("Wingman: v0.2") == "Wingman: v0.2"
    assert validate_plan_name("Footprint (MVP)") == "Footprint (MVP)"
    for bad in ["", "x" * 65, "with/slash", "../traversal", "tab\ttab", "back\\slash", "new\nline"]:
        with pytest.raises(ValueError):
            validate_plan_name(bad)


def test_get_plan_sets_position_1_based():
    db.create_plan("p", ["a", "b", "c", "d"])
    plan = db.get_plan("p")
    assert [t.position for t in plan.tasks] == [1, 2, 3, 4]


def test_position_recomputes_after_reorder():
    db.create_plan("p", ["a", "b", "c"])
    ids = [t.id for t in db.get_plan("p").tasks]
    # Move the last task (largest id) to the front.
    plan = db.reorder_tasks("p", [ids[2], ids[0], ids[1]])
    assert plan.tasks[0].id == ids[2]
    assert plan.tasks[0].position == 1
    assert [t.position for t in plan.tasks] == [1, 2, 3]
