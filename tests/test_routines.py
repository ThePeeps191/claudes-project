"""Routines, budget periods and watch mode."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

import pytest
from conftest import STARTUP, ScriptedModel, call, say

from tinyco.config import ConfigError, parse
from tinyco.routines import Routine, Scheduler, parse_every, parse_routines
from tinyco.runner import Runner


def test_parse_every_and_routines(tmp_path):
    assert parse_every("15m") == 900 and parse_every("1d") == 86400 and parse_every("2w") == 14 * 86400
    for bad in ("", "0d", "1y", "fast"):
        with pytest.raises(ConfigError):
            parse_every(bad)
    rs = parse_routines([{"name": "daily", "every": "1d", "at": "09:30", "goal": "Do it", "checks": ["true"]}])
    assert rs[0].at == (9, 30) and rs[0].checks == ["true"]
    for bad in (
        [{"every": "1d"}],
        [{"goal": "x", "every": "6h", "at": "09:00"}],
        [{"goal": "x", "every": "1d", "at": "25:00"}],
        [{"name": "a", "goal": "x", "every": "1d"}] * 2,
    ):
        with pytest.raises(ConfigError):
            parse_routines(bad)
    c = parse({"agents": {"a": {}}, "routines": [{"goal": "Tidy up", "every": "1h"}]}, tmp_path)
    assert c.routines[0].every == 3600


def test_next_due_interval_and_time_of_day():
    now = datetime(2026, 11, 5, 8, 0).timestamp()
    r = Routine("r", 3600, "g")
    assert r.next_due(None, now) == now
    assert r.next_due(now - 600, now) == now + 3000
    daily = Routine("d", 86400, "g", at=(9, 0))
    assert daily.next_due(None, now) == datetime(2026, 11, 5, 9, 0).timestamp()  # later today
    late = datetime(2026, 11, 5, 10, 0).timestamp()
    assert daily.next_due(None, late) == datetime(2026, 11, 6, 9, 0).timestamp()  # tomorrow
    fired = datetime(2026, 11, 5, 9, 0, 5).timestamp()
    assert daily.next_due(fired, late) == datetime(2026, 11, 6, 9, 0).timestamp()


def test_scheduler_fires_and_does_not_pile_up(office):
    office.company.routines = [Routine("tidy", 3600, "Tidy the repo", checks=["true"])]
    sched = Scheduler(office, office.company.routines)
    now = time.time()
    assert sched.fire_due(now) == [1]
    assert office.get(1).title == "Tidy the repo" and office.get(1).checks == ["true"]
    assert sched.fire_due(now + 60) == []  # not due yet
    assert sched.fire_due(now + 3700) == []  # due, but T1 is still open
    assert office.store.events(kinds=("routine_skipped",))
    office.submit("ceo", 1, "tidied")
    assert sched.fire_due(now + 7400 + 1) == [2]


def test_budget_period_only_counts_current_period(make_office):
    office = make_office({**STARTUP, "company": {**STARTUP["company"], "budget_usd": 1.0, "budget_period": "day"}})
    office.start_goal("Goal", checks=["true"])
    yesterday = (datetime.now() - timedelta(days=1)).timestamp()
    office.store.db.execute(
        "INSERT INTO spend (ts, agent, task_id, model, input, output, cache_read, cache_write, usd)"
        " VALUES (?, 'ceo', 1, 'm', 0, 0, 0, 0, 5.0)",
        (yesterday,),
    )
    model = ScriptedModel({"ceo": [[call("submit", task_id=1, summary="ok")], [say("ok")]]})
    summary = Runner(office, model).run()
    assert summary.stopped == "idle" and office.get(1).status == "done"  # yesterday's $5 doesn't block today
    with pytest.raises(ConfigError):
        parse({"company": {"budget_period": "year"}, "agents": {"a": {}}}, office.company.root)


def test_watch_picks_up_new_work_and_routines(make_office):
    office = make_office()
    office.company.routines = [Routine("standup", 3600, "Write the standup", checks=["true"])]

    def ceo_turn(messages):
        brief = messages[0]["content"]
        import re

        tid = int(re.search(r"## T(\d+):", brief).group(1))
        return [call("submit", task_id=tid, summary="done")]

    model = ScriptedModel({"ceo": [ceo_turn, [say("ok")]] * 5})
    stop = threading.Event()
    runner = Runner(office, model)
    result = {}
    th = threading.Thread(target=lambda: result.setdefault("s", runner.watch(poll=0.05, stop=stop)))
    th.start()
    deadline = time.time() + 10
    while time.time() < deadline and not (office.store.task(1) and office.get(1).status == "done"):
        time.sleep(0.05)
    assert office.get(1).title == "Write the standup" and office.get(1).status == "done"
    office.start_goal("Something new", checks=["true"])  # e.g. from the dashboard or CLI
    while time.time() < deadline and office.get(2).status != "done":
        time.sleep(0.05)
    stop.set()
    th.join(5)
    assert not th.is_alive()
    assert office.get(2).status == "done"
    assert result["s"].wakes == 2 and result["s"].stopped == "stopped"
