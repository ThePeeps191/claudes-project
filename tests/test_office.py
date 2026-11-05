"""Rules of the company, tested directly."""

from __future__ import annotations

import pytest

from tinyco.office import OfficeError
from tinyco.tools import Toolbox, validate


def test_can_only_assign_down_the_org(office):
    office.start_goal("Goal")
    office.create_task("ceo", 1, "Part", "engineer", "", ["true"])
    with pytest.raises(OfficeError, match="doesn't report to you"):
        office.create_task("engineer", 2, "Boss work", "ceo", "", ["true"])
    with pytest.raises(OfficeError, match="doesn't report to you"):
        office.create_task("engineer", 2, "Peer work", "reviewer", "", ["true"])
    # Assigning to yourself is fine.
    assert "Created T3" in office.create_task("engineer", 2, "My subpart", "engineer", "", ["true"])


def test_only_owner_breaks_down_a_task(office):
    office.start_goal("Goal")
    with pytest.raises(OfficeError, match="tasks you own"):
        office.create_task("engineer", 1, "Sneaky", "engineer", "", ["true"])


def test_every_task_needs_a_definition_of_done(office):
    office.start_goal("Goal")
    with pytest.raises(OfficeError, match="definition of done"):
        office.create_task("ceo", 1, "Vague", "engineer", "do stuff")
    assert "Created" in office.create_task("ceo", 1, "Reviewed", "engineer", "", reviewer="reviewer")


def test_reviewer_cannot_be_assignee(office):
    office.start_goal("Goal")
    with pytest.raises(OfficeError, match="other than the assignee"):
        office.create_task("ceo", 1, "Self review", "engineer", "", reviewer="engineer")


def test_duplicate_open_sibling_is_not_created(office):
    office.start_goal("Goal")
    office.create_task("ceo", 1, "Write the parser", "engineer", "", ["true"])
    msg = office.create_task("ceo", 1, "write the  Parser!", "engineer", "", ["true"])
    assert "already exists" in msg
    assert len(office.store.children(1)) == 1


def test_only_assignee_submits_and_subtasks_must_close_first(office):
    office.start_goal("Goal")
    office.create_task("ceo", 1, "Part", "engineer", "", ["true"])
    with pytest.raises(OfficeError, match="assigned to engineer"):
        office.submit("reviewer", 2, "me")
    with pytest.raises(OfficeError, match="open subtasks"):
        office.submit("ceo", 1, "done?")
    office.submit("engineer", 2, "ok")
    assert office.get(2).status == "done"
    office.submit("ceo", 1, "done")
    assert office.get(1).status == "done"


def test_only_reviewer_reviews(office):
    office.start_goal("Goal")
    office.create_task("ceo", 1, "Part", "engineer", "", ["true"], reviewer="reviewer")
    office.submit("engineer", 2, "ok")
    assert office.get(2).status == "in_review"
    with pytest.raises(OfficeError, match="not the reviewer"):
        office.review("ceo", 2, True)
    office.review("reviewer", 2, False, "needs tests")
    t = office.get(2)
    assert t.status == "in_progress" and t.attempts == 1
    assert office.store.has_pending_wake("engineer", 2)


def test_cancel_cascades(office):
    office.start_goal("Goal")
    office.create_task("ceo", 1, "Part", "engineer", "", ["true"])
    office.create_task("engineer", 2, "Sub", "engineer", "", ["true"])
    office.update_task("ceo", 2, cancel=True)
    assert office.get(2).status == "cancelled"
    assert office.get(3).status == "cancelled"


def test_shell_policy_deny_blocks_agent_checks_but_not_human_checks(make_office):
    from conftest import STARTUP

    data = {**STARTUP, "company": {**STARTUP["company"], "shell": "deny"}}
    office = make_office(data)
    office.start_goal("Goal", checks=["true"])
    office.create_task("ceo", 1, "Part", "engineer", "", ["true"])
    msg = office.submit("engineer", 2, "x")
    assert "Checks failed" in msg and "not approved" in msg
    office.update_task("ceo", 2, cancel=True)
    # Checks written by the human are trusted.
    assert "is done" in office.submit("ceo", 1, "x")


def test_ask_policy_uses_approver(make_office):
    from conftest import STARTUP

    asked = []
    data = {**STARTUP, "company": {**STARTUP["company"], "shell": "ask"}}
    office = make_office(data, approve=lambda kind, detail: asked.append((kind, detail)) or True)
    office.start_goal("Goal")
    task = office.get(1)
    box = Toolbox(office, "ceo", task)
    out, err = box.call("run", {"command": "echo hi"})
    assert not err and "hi" in out
    assert asked == [("shell", "echo hi")]


def test_workspace_paths_are_confined(office):
    office.start_goal("Goal")
    box = Toolbox(office, "ceo", office.get(1))
    out, err = box.call("write_file", {"path": "../escape.txt", "content": "x"})
    assert err and "outside the workspace" in out
    out, err = box.call("read_file", {"path": "/etc/passwd"})
    assert err and "outside the workspace" in out


def test_file_tools_roundtrip(office):
    office.start_goal("Goal")
    box = Toolbox(office, "ceo", office.get(1))
    assert not box.call("write_file", {"path": "src/a.py", "content": "x = 1\ny = 2\n"})[1]
    out, _ = box.call("read_file", {"path": "src/a.py"})
    assert "    1  x = 1" in out
    out, err = box.call("edit_file", {"path": "src/a.py", "old": "y = 2", "new": "y = 3"})
    assert not err
    assert (office.company.workspace / "src/a.py").read_text() == "x = 1\ny = 3\n"
    out, _ = box.call("list_files", {})
    assert "src/a.py" in out
    out, err = box.call("edit_file", {"path": "src/a.py", "old": "nope", "new": ""})
    assert err


def test_commands_do_not_see_api_key(office, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-secret")
    office.start_goal("Goal")
    box = Toolbox(office, "ceo", office.get(1))
    out, _ = box.call("run", {"command": "echo key=${ANTHROPIC_API_KEY:-unset}"})
    assert "key=unset" in out


def test_tool_input_validation(office):
    office.start_goal("Goal")
    box = Toolbox(office, "ceo", office.get(1))
    out, err = box.call("submit", {"task_id": 1})
    assert err and "missing required field 'summary'" in out
    out, err = box.call(
        "create_task", {"parent_id": 1, "title": "x", "description": "", "assignee": "engineer", "checks": "pytest"}
    )
    assert err and "must be an array" in out
    out, err = box.call("nope", {})
    assert err
    # "T1" style ids are accepted.
    out, err = box.call("view_task", {"task_id": "T1"})
    assert not err and "T1: Goal" in out


def test_validate_rejects_unknown_fields():
    schema = {"type": "object", "properties": {"a": {"type": "string"}}, "required": [], "additionalProperties": False}
    assert validate(schema, {"a": "x"}) is None
    assert "unknown field" in validate(schema, {"b": 1})
    assert validate({"type": "boolean"}, 1) is not None


def test_notes_persist_into_briefings(office):
    from tinyco.briefing import briefing
    from tinyco.store import Wake

    office.start_goal("Goal")
    office.note("ceo", "We use pytest.")
    brief = briefing(office, office.company.agents["ceo"], Wake(0, "ceo", 1, [{"reason": "assigned"}], "running", 0))
    assert "We use pytest." in brief
    assert "top-level goal" in brief


def test_done_tasks_are_committed_as_the_agent(office):
    import subprocess

    from tinyco import gitlog

    if not gitlog.available():
        import pytest

        pytest.skip("git not installed")
    ws = office.company.workspace
    assert gitlog.init(ws)
    office.start_goal("Goal")
    office.create_task("ceo", 1, "Write file", "engineer", "", ["test -f out.txt"], reviewer="reviewer")
    (ws / "out.txt").write_text("hi\n")
    office.submit("engineer", 2, "wrote out.txt")
    office.review("reviewer", 2, True)
    log = subprocess.run(["git", "log", "--format=%an|%s"], cwd=ws, capture_output=True, text=True).stdout
    assert log.splitlines() == ["engineer (tinyco)|T2 Write file"]  # authored by the assignee, not the reviewer
    assert office.store.events(kinds=("committed",))[0]["data"]["sha"]
    # Nothing changed: no empty commit.
    office.submit("ceo", 1, "done")
    log = subprocess.run(["git", "log", "--format=%an|%s"], cwd=ws, capture_output=True, text=True).stdout
    assert len(log.splitlines()) == 1


def test_git_can_be_disabled(make_office):
    from conftest import STARTUP

    from tinyco import gitlog

    office = make_office({**STARTUP, "company": {**STARTUP["company"], "git": False}})
    gitlog.init(office.company.workspace)
    office.start_goal("Goal", checks=["true"])
    (office.company.workspace / "x.txt").write_text("x")
    office.submit("ceo", 1, "done")
    assert not office.store.events(kinds=("committed",))


def test_briefing_shows_workspace_and_last_failure(office):
    from tinyco.briefing import briefing
    from tinyco.store import Wake

    office.start_goal("Goal")
    office.create_task("ceo", 1, "Part", "engineer", "", ["echo boom; exit 3"])
    ws = office.company.workspace
    (ws / "src").mkdir()
    (ws / "src" / "app.py").write_text("x = 1\n")
    (ws / "node_modules").mkdir()
    (ws / "node_modules" / "junk.js").write_text("")
    office.submit("engineer", 2, "try")
    brief = briefing(office, office.company.agents["engineer"], Wake(0, "engineer", 2, [{"reason": "nudge"}], "", 0))
    assert "- src/app.py" in brief and "junk.js" not in brief
    assert "The last submission's checks failed:" in brief and "boom" in brief and "(exit 3)" in brief


def test_manager_brief_is_bounded(office):
    from tinyco.briefing import briefing
    from tinyco.store import Wake

    office.start_goal("Big goal")
    for i in range(60):
        office.create_task("ceo", 1, f"Part {i}", "engineer", "", ["true"])
    for i in range(2, 42):
        office.submit("engineer", i, f"did part {i}")
    brief = briefing(office, office.company.agents["ceo"], Wake(0, "ceo", 1, [{"reason": "subtask_done"}], "", 0))
    assert "Subtasks (60: 40 done, 20 todo):" in brief
    assert "… and 5 more open" in brief and "… and 32 earlier closed subtasks" in brief
    assert brief.count("\n- T") < 30  # bounded, not 60 lines
    assert "did part 41" in brief  # the most recent completions keep their summaries
