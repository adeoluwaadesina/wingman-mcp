from wingman.tools import plan_tools, task_tools
from wingman.storage import db


def test_create_show_get():
    res = plan_tools.create_plan("p", ["a", "b"])
    assert "Created plan 'p'" in res["text"]
    assert res["plan"]["counts"]["total"] == 2

    shown = plan_tools.show_plan("p")
    assert "## p" in shown["text"]
    assert "[ ] " in shown["text"]


def test_list_plans_empty_then_populated():
    res = plan_tools.list_plans()
    assert "No plans" in res["text"]
    assert res["plans"] == []

    plan_tools.create_plan("p", ["a"])
    res = plan_tools.list_plans()
    assert any(r["name"] == "p" for r in res["plans"])


def test_rename_and_delete():
    plan_tools.create_plan("old", [])
    res = plan_tools.rename_plan("old", "new")
    assert res["plan"]["name"] == "new"
    res2 = plan_tools.delete_plan("new")
    assert "Deleted" in res2["text"]


def test_task_lifecycle():
    plan_tools.create_plan("p", [])
    add = task_tools.add_task("p", "first")
    tid = add["task"]["id"]
    assert add["task"]["content"] == "first"

    res = task_tools.tick_task("p", tid)
    assert res["task"]["status"] == "done"

    res = task_tools.update_task_status("p", tid, "in_progress")
    assert res["task"]["status"] == "in_progress"

    task_tools.delete_task("p", tid)
    assert plan_tools.show_plan("p")["plan"]["counts"]["total"] == 0


def test_add_tasks_bulk():
    plan_tools.create_plan("p", [])
    res = task_tools.add_tasks("p", ["x", "y", "z"])
    assert len(res["tasks"]) == 3


def test_reorder():
    plan_tools.create_plan("p", ["a", "b", "c"])
    ids = [t["id"] for t in plan_tools.show_plan("p")["plan"]["tasks"]]
    res = task_tools.reorder_tasks("p", [ids[1], ids[2], ids[0]])
    assert [t["id"] for t in res["plan"]["tasks"]] == [ids[1], ids[2], ids[0]]


def test_export_markdown():
    plan_tools.create_plan("p", ["a", "b"])
    plan = db.get_plan("p")
    md = plan_tools.export_markdown(plan)
    assert md.startswith("# p")
    assert "- [ ] a" in md
