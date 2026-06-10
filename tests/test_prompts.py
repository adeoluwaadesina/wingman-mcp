import pytest

from wingman import prompts
from wingman.storage import db
from wingman.tools import plan_tools, task_tools


def test_run_task_prompt_basic():
    plan_tools.create_plan("p", ["only"])
    tid = db.get_plan("p").tasks[0].id
    text = prompts.render_run_task_prompt("p", tid)
    assert "**p**" in text
    assert "only" in text
    # v0.2 (post-impl): tick_task / task_id instructions removed from template —
    # Claude has tick_task available and decides when to call it from context.
    assert "tick_task" not in text
    assert "task_id" not in text


def test_run_task_prompt_is_lean_no_plan_enumeration():
    # The slimmed template must NOT re-enumerate other tasks' state.
    plan_tools.create_plan(
        "p",
        ["alpha-done", "bravo-sibling", "charlie-target", "delta-sibling"],
    )
    plan = db.get_plan("p")
    task_tools.tick_task("p", plan.tasks[0].id)
    target = plan.tasks[2]  # "charlie-target"
    text = prompts.render_run_task_prompt("p", target.id)
    assert "Plan context:" not in text
    assert "Completed so far:" not in text
    assert "Still to do:" not in text
    # Only the target task's content appears; siblings are not dumped.
    assert "charlie-target" in text
    assert "alpha-done" not in text
    assert "bravo-sibling" not in text
    assert "delta-sibling" not in text
    # It is short — a handful of lines, not a state dump.
    assert text.count("\n") <= 6


def test_build_from_chat_prompt():
    plan_tools.create_plan("Launch", [])
    text = prompts.render_build_from_chat_prompt("Launch")
    assert "**Launch**" in text
    assert 'add_tasks(plan_name="Launch")' in text
    assert "Look back through our conversation" in text


def test_run_task_prompt_missing_task():
    plan_tools.create_plan("p", ["a"])
    with pytest.raises(db.TaskNotFound):
        prompts.render_run_task_prompt("p", 9999)
