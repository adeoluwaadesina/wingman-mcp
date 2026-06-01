import pytest

from wingman.tools import plan_tools, ui_tools
from wingman.storage import db


def test_ui_tick_and_get():
    plan_tools.create_plan("p", ["a", "b"])
    plan = db.get_plan("p")
    res = ui_tools.tick_task("p", plan.tasks[0].id)
    assert res["task"]["status"] == "done"
    fetched = ui_tools.get_plan("p")
    assert fetched["plan"]["counts"]["done"] == 1


def test_ui_add_and_delete():
    plan_tools.create_plan("p", [])
    a = ui_tools.add_task("p", "first")
    ui_tools.delete_task("p", a["task"]["id"])
    assert ui_tools.get_plan("p")["plan"]["counts"]["total"] == 0


def test_ui_reorder():
    plan_tools.create_plan("p", ["a", "b", "c"])
    ids = [t["id"] for t in ui_tools.get_plan("p")["plan"]["tasks"]]
    res = ui_tools.reorder_tasks("p", list(reversed(ids)))
    assert [t["id"] for t in res["plan"]["tasks"]] == list(reversed(ids))


def test_ui_rename_via_ui_tool():
    plan_tools.create_plan("old", [])
    res = ui_tools.rename_plan("old", "new")
    assert res["plan"]["name"] == "new"


def test_ui_clear_completed_and_all():
    plan_tools.create_plan("p", ["a", "b", "c"])
    plan = db.get_plan("p")
    ui_tools.tick_task("p", plan.tasks[0].id)
    res = ui_tools.clear_completed("p")
    assert res["removed"] == 1
    res2 = ui_tools.clear_all("p")
    assert res2["removed"] == 2


def test_ui_export_markdown():
    plan_tools.create_plan("p", ["a"])
    res = ui_tools.export_markdown("p")
    assert res["markdown"].startswith("# p")


def test_ui_delete_plan():
    plan_tools.create_plan("p", ["a", "b"])
    res = ui_tools.delete_plan("p")
    assert "Deleted" in res["text"]
    with pytest.raises(db.PlanNotFound):
        db.get_plan("p")


def test_ui_get_run_task_prompt_flips_status():
    plan_tools.create_plan("p", ["a", "b"])
    tid = db.get_plan("p").tasks[0].id
    res = ui_tools.get_run_task_prompt("p", tid)
    assert "**p**" in res["prompt"]
    # side effect: task is now in_progress
    assert db.get_plan("p").tasks[0].status == "in_progress"


def test_ui_get_build_from_chat_prompt():
    plan_tools.create_plan("p", [])
    res = ui_tools.get_build_from_chat_prompt("p")
    assert "add_tasks" in res["prompt"]
