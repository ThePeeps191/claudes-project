"""Regression tests for the second independent review (routines, watch, budgets, migration)."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta

import pytest
from conftest import STARTUP, ScriptedModel, call, say

from tinyco.config import ConfigError, parse
from tinyco.office import OfficeError
from tinyco.routines import Routine, Scheduler, parse_routines
from tinyco.runner import Runner
from tinyco.store import Store


def ts(*a) -> float:
    return datetime(*a).timestamp()


def test_weekly_routine_catches_up_once_and_keeps_its_weekday(office):
    weekly = Routine("weekly", 7 * 86400, "Weekly report", checks=["true"], at=(9, 0))
    office.company.routines = [weekly]
    sched = Scheduler(office, [weekly])
    fires = []
    # Mon 2026-11-02 09:00 fires; the machine is off until Tue 11-10 08:00; then runs every hour.
    t = ts(2026, 11, 2, 9, 0)
    for now in [t] + [ts(2026, 11, 10, 8, 0) + h * 3600 for h in range(0, 24 * 8)]:
        for tid in sched.fire_due(now):
            fires.append(datetime.fromtimestamp(now))
            office.submit("ceo", tid, "done")  # close it so the next slot isn't skipped
    assert [f.strftime("%a %m-%d %H:%M") for f in fires] == ["Mon 11-02 09:00", "Tue 11-10 08:00", "Mon 11-16 09:00"]


def test_interval_routine_does_not_drift(office):
    r = Routine("hourly", 3600, "Ping", checks=["true"])
    sched = Scheduler(office, [r])
    start = ts(2026, 11, 5, 10, 0)
    assert sched.fire_due(start)
    office.submit("ceo", 1, "ok")
    assert sched.fire_due(start + 3600 + 900)  # fired 15 minutes late
    office.submit("ceo", 2, "ok")
    assert not sched.fire_due(start + 2 * 3600 - 1)
    assert sched.fire_due(start + 2 * 3600)  # still on the hour, not 15 minutes later


def test_routine_review_and_checks_are_validated(tmp_path):
    agents = {"ceo": {}, "qa": {"reports_to": "ceo"}}
    for review in ("nobody", "ceo"):
        with pytest.raises(ConfigError, match="routine"):
            parse({"agents": agents, "routines": [{"goal": "g", "every": "1d", "review": review}]}, tmp_path)
    c = parse(
        {"agents": agents, "routines": [{"goal": "g", "every": "1d", "review": "", "checks": "make test"}]}, tmp_path
    )
    assert c.routines[0].review is None and c.routines[0].checks == ["make test"]
    with pytest.raises(ConfigError, match="checks"):
        parse_routines([{"goal": "g", "every": "1d", "checks": 5}])


def test_routine_that_cannot_start_logs_instead_of_crashing(office):
    bad = Routine("bad", 3600, "g", review="ghost")  # e.g. the agent was removed from company.toml later
    assert Scheduler(office, [bad]).fire_due() == []
    assert office.store.events(kinds=("routine_error",))


def test_reopen_resets_stall_counter(office):
    office.start_goal("Goal")
    Runner(office, ScriptedModel({"ceo": [[say("hmm")]] * 3})).run()
    assert office.get(1).blocked_on.startswith("human: stalled")
    office.update_task("human", 1, cancel=True)
    office.reopen(1, "please actually do it")
    Runner(office, ScriptedModel({"ceo": [[say("partial")]]})).run(max_wakes=1)
    assert office.get(1).status == "in_progress"  # nudged, not escalated straight away


def test_reopen_needs_parent_in_progress(office):
    office.start_goal("Goal", reviewer="human")
    office.create_task("ceo", 1, "Part", "engineer", "", ["true"])
    office.submit("engineer", 2, "done")
    office.submit("ceo", 1, "all done")
    assert office.get(1).status == "in_review"
    with pytest.raises(OfficeError, match="in progress"):
        office.reopen(2)


def _old_db(path):
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE tasks (id INTEGER PRIMARY KEY, parent_id INTEGER, title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '', assignee TEXT NOT NULL, creator TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'todo', checks TEXT NOT NULL DEFAULT '[]', reviewer TEXT,
            attempts INTEGER NOT NULL DEFAULT 0, summary TEXT, blocked_on TEXT,
            created_at REAL NOT NULL, updated_at REAL NOT NULL);
        CREATE INDEX idx_tasks_parent ON tasks(parent_id);
    """)
    db.execute(
        "INSERT INTO tasks (title, assignee, creator, created_at, updated_at) VALUES ('Goal', 'ceo', 'human', 0, 0)"
    )
    db.executemany(
        "INSERT INTO tasks (parent_id, title, assignee, creator, created_at, updated_at) VALUES (1, ?, 'eng', 'ceo', 0, 0)",
        [(f"Write the Parser #{i}",) for i in range(500)],
    )
    db.commit()
    db.close()


def test_migration_is_atomic_and_safe_with_concurrent_openers(tmp_path):
    path = tmp_path / "state.db"
    _old_db(path)
    errors = []

    def open_it():
        try:
            Store(path)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=open_it) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    s = Store(path)
    assert s.find_open_sibling(1, "write the parser #7") is not None  # backfilled
    names = {r[0] for r in s.db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"idx_tasks_parent_status", "idx_events_task_kind", "idx_wakes_pending", "idx_tasks_title"} <= names


def test_brief_and_status_show_spend_for_the_current_period(make_office):
    from tinyco.briefing import briefing
    from tinyco.store import Wake

    data = {
        **STARTUP,
        "company": {**STARTUP["company"], "budget_period": "day"},
        "agents": {**STARTUP["agents"], "ceo": {"budget_usd": 1.0}},
    }
    office = make_office(data)
    office.start_goal("Goal")
    yesterday = (datetime.now() - timedelta(days=1)).timestamp()
    office.store.db.execute(
        "INSERT INTO spend (ts, agent, task_id, model, input, output, cache_read, cache_write, usd)"
        " VALUES (?, 'ceo', 1, 'm', 0, 0, 0, 0, 5.0)",
        (yesterday,),
    )
    brief = briefing(office, office.company.agents["ceo"], Wake(0, "ceo", 1, [{"reason": "assigned"}], "", 0))
    assert "Your spend this day: $0.00 of $1.00." in brief


def test_next_period_start(tmp_path):
    c = parse({"company": {"budget_period": "month"}, "agents": {"a": {}}}, tmp_path)
    assert c.next_period_start(ts(2026, 12, 15, 10, 0)) == ts(2027, 1, 1)
    c.budget_period = "week"
    assert c.next_period_start(ts(2026, 11, 5, 10, 0)) == ts(2026, 11, 9)  # next Monday
    c.budget_period = "day"
    assert c.next_period_start(ts(2026, 11, 5, 23, 59)) == ts(2026, 11, 6)
    c.budget_period = "total"
    assert c.next_period_start() is None


def test_stop_is_honoured_mid_burst(make_office):
    office = make_office()
    for i in range(5):
        office.start_goal(f"Goal {i}", checks=["true"])
    stop = threading.Event()

    def turn(messages):
        stop.set()  # ask to stop while work is still queued
        import re

        tid = int(re.search(r"## T(\d+):", messages[0]["content"]).group(1))
        return [call("submit", task_id=tid, summary="ok")]

    model = ScriptedModel({"ceo": [turn, [say("ok")]] * 5})
    summary = Runner(office, model).watch(poll=0.01, stop=stop)
    assert summary.wakes == 1
    assert len(office.store.pending_wakes()) == 4  # the rest is kept for next time


def test_cli_goal_file_encoding_and_watch_flags(tmp_path, monkeypatch, capsys):
    from tinyco.cli import main

    monkeypatch.chdir(tmp_path)
    main(["init"])
    (tmp_path / "spec.txt").write_bytes("caf\xe9".encode("latin-1"))
    assert main(["goal", "X", "-f", "spec.txt"]) == 1
    assert "can't read" in capsys.readouterr().err
    assert main(["run", "--watch", "--max-wakes", "3"]) == 1


def test_watch_wakes_at_budget_period_rollover(make_office):
    import time

    data = {**STARTUP, "company": {**STARTUP["company"], "budget_usd": 1.0, "budget_period": "day"}}
    office = make_office(data)
    office.start_goal("Goal", checks=["true"])
    office.store.db.execute(
        "INSERT INTO spend (ts, agent, task_id, model, input, output, cache_read, cache_write, usd)"
        " VALUES (?, 'ceo', 1, 'm', 0, 0, 0, 0, 5.0)",
        (time.time(),),
    )
    boundary = time.time() + 0.5  # "midnight" in half a second
    office.company.next_period_start = lambda now=None: (
        boundary if (now or time.time()) < boundary else boundary + 86400
    )
    office.company.period_start = lambda now=None: boundary if (now or time.time()) >= boundary else boundary - 86400
    stop = threading.Event()
    model = ScriptedModel({"ceo": [[call("submit", task_id=1, summary="ok")], [say("ok")]]})
    th = threading.Thread(target=lambda: Runner(office, model).watch(poll=60, stop=stop))  # long poll on purpose
    th.start()
    deadline = time.time() + 5
    while time.time() < deadline and office.get(1).status != "done":
        time.sleep(0.05)
    stop.set()
    th.join(5)
    assert office.get(1).status == "done"  # resumed at the boundary, not at the hourly re-check
