"""Regression tests for issues found in an independent review."""

from __future__ import annotations

import http.client
import json
import threading

import pytest
from conftest import STARTUP, ScriptedModel, call, say

from tinyco import gitlog
from tinyco.config import ConfigError, parse
from tinyco.office import OfficeError
from tinyco.runner import Runner, run_lock
from tinyco.tools import Toolbox

# -- agents can't make git (or tinyco) run code behind the shell policy ------------------------------


@pytest.mark.skipif(not gitlog.available(), reason="git not installed")
def test_agents_cannot_write_git_metadata_or_tinyco_state(make_office):
    office = make_office({**STARTUP, "company": {**STARTUP["company"], "shell": "deny"}})
    gitlog.init(office.company.workspace)
    office.start_goal("Goal")
    box = Toolbox(office, "ceo", office.get(1))
    for path in (".git/config", ".git/hooks/post-commit", "sub/.gitattributes", ".gitmodules"):
        out, err = box.call("write_file", {"path": path, "content": "x"})
        assert err and "git metadata" in out, path
    out, err = box.call("edit_file", {"path": ".git/config", "old": "[core]", "new": "[core]\n\tfsmonitor = x"})
    assert err and "git metadata" in out


@pytest.mark.skipif(not gitlog.available(), reason="git not installed")
def test_git_ignores_hostile_repo_config(make_office, tmp_path):
    office = make_office()
    ws = office.company.workspace
    gitlog.init(ws)
    marker = tmp_path / "PWNED"
    # Even if .git/config were changed some other way (e.g. an approved shell command):
    with open(ws / ".git" / "config", "a") as f:
        f.write(f'[core]\n\tfsmonitor = "touch {marker}; false"\n\thooksPath = hooks\n')
    (ws / "hooks").mkdir()
    hook = ws / "hooks" / "post-commit"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o755)
    (ws / "a.txt").write_text("a")
    assert gitlog.commit(ws, "engineer", "T1 x")
    assert not marker.exists()


def test_workspace_cannot_contain_the_company(tmp_path):
    for ws in (".", ".."):
        with pytest.raises(ConfigError, match="workspace"):
            parse({"company": {"workspace": ws}, "agents": {"a": {}}}, tmp_path / "co")


def test_agents_cannot_write_company_toml_or_state(tmp_path):
    from tinyco.office import Office
    from tinyco.store import Store

    # An absolute workspace path is allowed, but tinyco's own files stay off-limits even if it overlaps.
    root = tmp_path / "co"
    company = parse({"company": {"workspace": str(tmp_path / "ws")}, "agents": {"a": {}}}, root)
    office = Office(company, Store(":memory:"))
    office.start_goal("Goal")
    box = Toolbox(office, "a", office.get(1))
    assert not box.call("write_file", {"path": "ok.txt", "content": "x"})[1]


# -- dashboard: DNS rebinding ---------------------------------------------------------------------


def test_dashboard_rejects_foreign_host_and_origin(tmp_path):
    from tinyco.config import load
    from tinyco.dashboard import serve
    from tinyco.demo import run_demo

    run_demo(tmp_path / "co", delay=0)
    srv = serve(load(tmp_path / "co"), port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:

        def req(method, path, headers, body=None):
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            c.request(method, path, body=body, headers=headers)
            r = c.getresponse()
            return r.status, r.read()

        assert req("GET", "/api/state", {"Host": f"attacker.example:{port}"})[0] == 403
        goal = json.dumps({"title": "x", "checks": ["id > /tmp/pwned"]})
        post = {"Content-Type": "application/json", "X-Tinyco": "1"}
        assert req("POST", "/api/goal", {**post, "Host": f"attacker.example:{port}"}, goal)[0] == 403
        assert (
            req("POST", "/api/goal", {**post, "Host": f"127.0.0.1:{port}", "Origin": "http://evil.example"}, goal)[0]
            == 403
        )
        assert req("GET", "/api/state", {"Host": f"localhost:{port}"})[0] == 200
        assert req("GET", "/api/events?after=abc", {"Host": f"127.0.0.1:{port}"})[0] == 400
        assert req("GET", "/api/events?task=99999999999999999999", {"Host": f"127.0.0.1:{port}"})[0] == 400
    finally:
        srv.shutdown()
        srv.server_close()


# -- nothing gets stuck --------------------------------------------------------------------------


def test_crashed_running_wake_is_recovered_by_next_run(office):
    office.start_goal("Goal", checks=["true"])
    office.store.next_wake()  # claimed, then the process "died"
    model = ScriptedModel({"ceo": [[call("submit", task_id=1, summary="ok")], [say("ok")]]})
    summary = Runner(office, model).run()
    assert summary.wakes == 1 and office.get(1).status == "done"


def test_second_concurrent_run_is_refused(office):
    office.start_goal("Goal")
    with run_lock(office.company.state_dir) as locked:
        assert locked
        summary = Runner(office, ScriptedModel({})).run()
    assert summary.stopped == "locked"
    assert len(office.store.pending_wakes()) == 1


def test_cancelling_a_subtask_wakes_its_manager(office):
    office.start_goal("Goal")
    office.create_task("ceo", 1, "Part", "engineer", "", ["true"])
    office.store.finish_wake(office.store.next_wake().id)
    office.update_task("human", 2, cancel=True)
    ceo = [w for w in office.store.pending_wakes() if w.agent == "ceo"]
    assert ceo and ceo[0].reasons[-1]["reason"] == "subtask_cancelled"


def test_refusal_on_subtask_escalates_to_manager_and_on_goal_to_human(office):
    office.start_goal("Goal")
    office.create_task("ceo", 1, "Part", "engineer", "", ["true"])
    office.store.finish_wake(office.store.next_wake().id)
    model = ScriptedModel(
        {"engineer": [[say("")]], "ceo": [[say("")]]}, stop_reasons={("engineer", 1): "refusal", ("ceo", 1): "refusal"}
    )
    Runner(office, model).run()
    assert office.get(2).status == "failed"
    assert office.get(1).status == "blocked" and office.get(1).blocked_on.startswith("human:")
    assert [t.id for t in office.inbox()] == [1]
    office.reply(1, "Try a different approach.")
    assert office.get(1).status == "in_progress" and office.store.has_pending_wake("ceo", 1)


def test_submission_discarded_if_task_cancelled_while_checks_run(office, monkeypatch):
    office.start_goal("Goal")
    office.create_task("ceo", 1, "Part", "engineer", "", ["true"])
    real = office.run_checks

    def cancel_midway(task):
        res = real(task)
        office.update_task("human", task.id, cancel=True)
        return res

    monkeypatch.setattr(office, "run_checks", cancel_midway)
    msg = office.submit("engineer", 2, "done")
    assert "discarded" in msg
    assert office.get(2).status == "cancelled"


def test_over_budget_bystander_does_not_block_the_task(make_office):
    data = {**STARTUP, "agents": {**STARTUP["agents"], "reviewer": {"reports_to": "ceo", "budget_usd": 0.0}}}
    office = make_office(data)
    office.start_goal("Goal")
    office.create_task("ceo", 1, "Part", "engineer", "", ["false"])
    office.comment("ceo", 2, "fyi", notify=["reviewer"])
    model = ScriptedModel({"engineer": [[say("working")]] * 6, "ceo": [[say("ok")]] * 6})
    Runner(office, model).run(max_wakes=3)
    assert office.get(2).status != "blocked"
    assert any(c["agent"] == "engineer" for c in model.calls)


def test_reviewer_budget_pause_resumes_into_review(make_office):
    data = {**STARTUP, "agents": {**STARTUP["agents"], "reviewer": {"reports_to": "ceo", "budget_usd": 0.0}}}
    office = make_office(data)
    office.start_goal("Goal")
    office.create_task("ceo", 1, "Part", "engineer", "", ["true"], reviewer="reviewer")
    office.submit("engineer", 2, "done")
    office.store.finish_wake(office.store.next_wake().id)  # skip ceo goal wake
    office.store.finish_wake(office.store.next_wake().id)  # skip engineer assigned wake
    Runner(office, ScriptedModel({})).run()
    t = office.get(2)
    assert t.status == "blocked" and t.blocked_on.startswith("budget:")
    office.company.agents["reviewer"].budget_usd = 5
    model = ScriptedModel({"reviewer": [[call("review", task_id=2, approve=True, notes="ok")], [say("ok")]]})
    Runner(office, model).run(max_wakes=1)
    assert model.calls[0]["agent"] == "reviewer"
    assert office.get(2).status == "done"


def test_nudge_count_resets_after_human_reply(office):
    office.start_goal("Goal")
    model = ScriptedModel({"ceo": [[say("hmm")]] * 3})
    Runner(office, model).run()
    assert office.get(1).status == "blocked"
    office.reply(1, "Keep going.")
    model = ScriptedModel({"ceo": [[say("partial")]]})
    Runner(office, model).run(max_wakes=1)
    assert office.get(1).status == "in_progress"  # nudged again, not instantly re-escalated
    assert office.store.has_pending_wake("ceo", 1)


def test_question_in_comment_is_not_a_stall(office):
    office.start_goal("Goal")
    office.create_task("ceo", 1, "Part", "engineer", "", ["true"])
    office.store.finish_wake(office.store.next_wake().id)
    model = ScriptedModel(
        {
            "engineer": [
                [call("comment", task_id=2, text="Which format, JSON or CSV?", notify=["ceo"])],
                [say("asked")],
                [call("submit", task_id=2, summary="done")],
                [say("ok")],
            ],
            "ceo": [[call("comment", task_id=2, text="JSON.")], [say("answered")], [say("great")]],
        }
    )
    Runner(office, model).run()
    assert not office.store.events(task_id=2, kinds=("nudged",))
    eng_briefs = [c["messages"][0]["content"] for c in model.calls if c["agent"] == "engineer"]
    assert any("has read your comment" in b for b in eng_briefs)
    assert office.get(2).status == "done"


# -- rules that updates and late calls could get around ---------------------------------------------


def test_update_cannot_remove_definition_of_done_or_self_review(office):
    office.start_goal("Goal")
    office.create_task("ceo", 1, "Part", "engineer", "", ["false"])
    with pytest.raises(OfficeError, match="definition of done"):
        office.update_task("ceo", 2, checks=[])
    with pytest.raises(OfficeError, match="other than the assignee"):
        office.update_task("ceo", 2, reviewer="engineer")
    office.update_task("ceo", 2, reviewer="reviewer")
    office.update_task("ceo", 2, checks=[])  # fine now: the reviewer is the definition of done
    with pytest.raises(OfficeError, match="definition of done"):
        office.update_task("ceo", 2, reviewer="none")


def test_no_subtasks_under_work_in_review(office):
    office.start_goal("Goal")
    office.create_task("ceo", 1, "Part", "engineer", "", ["true"], reviewer="reviewer")
    office.submit("engineer", 2, "done")
    with pytest.raises(OfficeError, match="in progress"):
        office.create_task("engineer", 2, "Sneaky", "engineer", "", ["true"])


def test_ask_human_only_on_live_work(office):
    office.start_goal("Goal", checks=["true"])
    office.submit("ceo", 1, "done")
    with pytest.raises(OfficeError, match="right now"):
        office.ask_human("ceo", 1, "Was that ok?")
    assert office.get(1).status == "done"


def test_huge_task_ids_are_errors_not_crashes(office):
    office.start_goal("Goal")
    box = Toolbox(office, "ceo", office.get(1))
    out, err = box.call("view_task", {"task_id": 10**20})
    assert err and "no such task" in out
    out, err = box.call("view_task", {"task_id": 0})
    assert err


def test_cli_rejects_bad_input_cleanly(tmp_path, monkeypatch, capsys):
    from tinyco.cli import main

    monkeypatch.chdir(tmp_path)
    main(["init"])
    assert main(["goal", "Y", "--review", "bob"]) == 1
    assert "unknown agent" in capsys.readouterr().err
    for argv in (["task", "abc"], ["reply", "T", "x"], ["approve", "99999999999999999999"]):
        with pytest.raises(SystemExit) as e:
            main(argv)
        assert e.value.code == 2


# -- claude-code runs that die without reporting cost ---------------------------------------------


def test_claude_code_timeout_charges_cap_and_escalates(make_office, tmp_path, monkeypatch):
    import stat
    import sys
    from pathlib import Path

    from tinyco import external
    from tinyco.office import Office
    from tinyco.store import Store

    fake = Path(__file__).with_name("fake_claude.py")
    exe = tmp_path / "bin" / "claude"
    exe.parent.mkdir()
    exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{fake}" "$@"\n')
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "sleep")
    monkeypatch.setattr(external, "TIMEOUT", 1)
    company = parse(
        {
            "company": {"shell": "allow", "budget_usd": 5},
            "agents": {"dev": {"runtime": "claude-code", "command": str(exe), "budget_usd": 1.5}},
        },
        tmp_path / "co",
    )
    office = Office(company, Store(":memory:"))
    office.start_goal("Anything")
    Runner(office, object()).run()
    assert office.store.spent(agent="dev") == pytest.approx(1.5)
    t = office.get(1)
    assert t.status == "blocked" and t.blocked_on.startswith("human:")  # not auto-retried
    assert Runner(office, object()).run().wakes == 0
