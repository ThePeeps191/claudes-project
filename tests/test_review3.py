"""Regression tests for the third independent review (criteria, MCP)."""

from __future__ import annotations

import pytest
from conftest import STARTUP

from tinyco.config import ConfigError, parse
from tinyco.office import OfficeError
from tinyco.tools import Toolbox

CRIT = ["Names 3 competitors", "Cites sources"]
CRM = {"name": "crm", "url": "https://crm.example/mcp", "token_env": "CRM_TOKEN"}


def _office_with_mcp(make_office):
    data = {**STARTUP, "agents": {**STARTUP["agents"], "engineer": {"reports_to": "ceo", "mcp": [CRM]}}}
    return make_office(data)


def test_mcp_tokens_never_reach_agent_commands(make_office, monkeypatch):
    office = _office_with_mcp(make_office)
    monkeypatch.setenv("CRM_TOKEN", "secret-xyz")
    office.start_goal("Goal", checks=['test -z "$CRM_TOKEN"'])
    box = Toolbox(office, "reviewer", office.get(1))
    out, _ = box.call("run", {"command": "echo token=${CRM_TOKEN:-unset}"})
    assert "token=unset" in out and "secret-xyz" not in out
    assert "is done" in office.submit("ceo", 1, "done")  # the check didn't see it either


def test_claude_code_missing_token_is_fatal_not_charged(tmp_path, monkeypatch):
    from tinyco.office import Office
    from tinyco.runner import Runner
    from tinyco.store import Store

    monkeypatch.delenv("CRM_TOKEN", raising=False)
    company = parse(
        {"company": {"shell": "allow"}, "agents": {"dev": {"runtime": "claude-code", "command": "true", "mcp": [CRM]}}},
        tmp_path,
    )
    office = Office(company, Store(":memory:"))
    office.start_goal("Anything")
    summary = Runner(office, object()).run()
    assert summary.stopped == "fatal" and "$CRM_TOKEN" in summary.error
    assert office.store.spent() == 0 and len(office.store.pending_wakes()) == 1


def test_duplicate_and_prefixed_verdicts(office):
    office.start_goal("Goal")
    office.create_task("ceo", 1, "Research", "engineer", "", criteria=CRIT)
    office.submit("engineer", 2, "done")
    with pytest.raises(OfficeError, match="judged twice"):
        office.review(
            "ceo",
            2,
            True,
            "",
            [
                {"criterion": 1, "passed": False, "reason": "missing"},
                {"criterion": 1, "passed": True, "reason": "ok"},
                {"criterion": 2, "passed": True, "reason": "ok"},
            ],
        )
    office.review(
        "ceo",
        2,
        True,
        "",
        [{"criterion": "T1", "passed": True, "reason": "ok"}, {"criterion": "2", "passed": True, "reason": "ok"}],
    )
    assert office.get(2).status == "done"


def test_verdicts_on_task_without_criteria_are_refused(office):
    office.start_goal("Goal")
    office.create_task("ceo", 1, "Part", "engineer", "", ["true"], reviewer="reviewer")
    office.submit("engineer", 2, "done")
    with pytest.raises(OfficeError, match="no acceptance criteria"):
        office.review("reviewer", 2, True, "", [{"criterion": 7, "passed": True, "reason": "x"}])
    box = Toolbox(office, "reviewer", office.get(2))
    out, err = box.call(
        "review",
        {"task_id": 2, "approve": False, "notes": "", "verdicts": [{"criterion": 1, "passed": False, "reason": "bad"}]},
    )
    assert err


def test_empty_tool_allowlist_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="empty"):
        parse({"agents": {"a": {"mcp": [{**CRM, "tools": []}]}}}, tmp_path)


def test_update_criteria_defaults_reviewer_and_new_reviewer_is_woken(office):
    office.start_goal("Goal")
    office.create_task("ceo", 1, "Part", "engineer", "", ["true"])
    office.update_task("ceo", 2, criteria=["Is good"])
    assert office.get(2).reviewer == "ceo"
    office.update_task("ceo", 2, reviewer="reviewer")
    office.submit("engineer", 2, "done")
    assert office.get(2).status == "in_review"
    office.store.db.execute("UPDATE wakes SET status = 'done'")
    office.update_task("ceo", 2, reviewer="ceo")
    wakes = [(w.agent, w.reasons[-1]["reason"]) for w in office.store.pending_wakes()]
    assert ("ceo", "review_requested") in wakes


def test_review_brief_asks_for_verdicts(office):
    from tinyco.briefing import briefing
    from tinyco.store import Wake

    office.start_goal("Goal")
    office.create_task("ceo", 1, "Research", "engineer", "", criteria=CRIT)
    office.submit("engineer", 2, "done")
    brief = briefing(
        office,
        office.company.agents["ceo"],
        Wake(0, "ceo", 2, [{"reason": "review_requested", "by": "engineer"}], "", 0),
    )
    assert "checks passed" not in brief and "verdict with evidence on every numbered criterion" in brief


def test_upgraded_db_drops_superseded_indexes(tmp_path):
    import sqlite3

    from tinyco.store import Store

    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript(
        "CREATE TABLE tasks (id INTEGER PRIMARY KEY, parent_id INTEGER, title TEXT NOT NULL,"
        " description TEXT NOT NULL DEFAULT '', assignee TEXT NOT NULL, creator TEXT NOT NULL,"
        " status TEXT NOT NULL DEFAULT 'todo', checks TEXT NOT NULL DEFAULT '[]', reviewer TEXT,"
        " attempts INTEGER NOT NULL DEFAULT 0, summary TEXT, blocked_on TEXT, created_at REAL NOT NULL,"
        " updated_at REAL NOT NULL); CREATE INDEX idx_tasks_parent ON tasks(parent_id);"
    )
    db.close()
    s = Store(path)
    names = {r[0] for r in s.db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_tasks_parent" not in names and "idx_tasks_parent_status" in names
