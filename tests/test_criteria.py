"""Acceptance criteria: a definition of done for work that isn't code."""

from __future__ import annotations

import pytest
from conftest import ScriptedModel, call, say

from tinyco.office import OfficeError
from tinyco.runner import Runner
from tinyco.tools import Toolbox

CRITERIA = ["Names 3 competitors with current prices", "Cites a source for every price"]


def test_criteria_default_the_reviewer_to_the_manager(office):
    office.start_goal("Launch")
    office.create_task("ceo", 1, "Competitor research", "engineer", "Write research.md", criteria=CRITERIA)
    t = office.get(2)
    assert t.reviewer == "ceo" and t.criteria == CRITERIA and t.checks == []


def test_goals_with_criteria_need_a_reviewer(office):
    with pytest.raises(OfficeError, match="judge them"):
        office.start_goal("Launch", criteria=["Has a landing page"])
    t = office.start_goal("Launch", criteria=["Has a landing page"], reviewer="human")
    assert t.criteria == ["Has a landing page"]


def test_review_must_judge_every_criterion(office):
    office.start_goal("Launch")
    office.create_task("ceo", 1, "Research", "engineer", "", criteria=CRITERIA)
    office.submit("engineer", 2, "wrote research.md")
    assert office.get(2).status == "in_review"
    with pytest.raises(OfficeError, match="missing verdicts"):
        office.review("ceo", 2, True, "", [{"criterion": 1, "passed": True, "reason": "3 listed"}])
    with pytest.raises(OfficeError, match="reject with those notes"):
        office.review(
            "ceo",
            2,
            True,
            "",
            [
                {"criterion": 1, "passed": True, "reason": "ok"},
                {"criterion": 2, "passed": False, "reason": "no source for Streaks"},
            ],
        )
    with pytest.raises(OfficeError, match="there is no criterion 3"):
        office.review("ceo", 2, False, "", [{"criterion": 3, "passed": False, "reason": "x"}])
    office.review(
        "ceo",
        2,
        False,
        "",
        [
            {"criterion": 1, "passed": True, "reason": "ok"},
            {"criterion": 2, "passed": False, "reason": "no source for Streaks"},
        ],
    )
    t = office.get(2)
    assert t.status == "in_progress" and t.attempts == 1
    wake = [w for w in office.store.pending_wakes() if w.agent == "engineer"][0]
    assert "Criterion 2 not met (Cites a source for every price): no source for Streaks" in wake.reasons[-1]["notes"]
    office.submit("engineer", 2, "added sources")
    office.review(
        "ceo",
        2,
        True,
        "",
        [{"criterion": 1, "passed": True, "reason": "ok"}, {"criterion": 2, "passed": True, "reason": "links present"}],
    )
    assert office.get(2).status == "done"
    verdicts = office.store.events(task_id=2, kinds=("reviewed",))[-1]["data"]["verdicts"]
    assert [v["passed"] for v in verdicts] == [True, True]


def test_human_can_approve_without_verdicts(office):
    office.start_goal("Launch", criteria=["Landing page states the price"], reviewer="human")
    office.submit("ceo", 1, "done")
    assert office.human_review(1, True) == "Approved T1; it's done."


def test_brief_shows_criteria_and_review_tool_takes_verdicts(office):
    from tinyco.briefing import briefing
    from tinyco.store import Wake

    office.start_goal("Launch")
    office.create_task("ceo", 1, "Research", "engineer", "", criteria=CRITERIA)
    brief = briefing(office, office.company.agents["engineer"], Wake(0, "engineer", 2, [{"reason": "assigned"}], "", 0))
    assert "Acceptance criteria (ceo judges each one):" in brief and "2. Cites a source for every price" in brief
    office.submit("engineer", 2, "done")
    box = Toolbox(office, "ceo", office.get(1))
    out, err = box.call(
        "review",
        {
            "task_id": 2,
            "approve": True,
            "notes": "",
            "verdicts": [
                {"criterion": 1, "passed": True, "reason": "yes"},
                {"criterion": 2, "passed": True, "reason": "yes"},
            ],
        },
    )
    assert not err, out
    out, err = box.call("review", {"task_id": 2, "approve": True, "notes": "", "verdicts": [{"criterion": "x"}]})
    assert err


def test_update_cannot_strand_criteria_without_a_reviewer(office):
    office.start_goal("Launch")
    office.create_task("ceo", 1, "Research", "engineer", "", criteria=CRITERIA)
    with pytest.raises(OfficeError, match="need a reviewer"):
        office.update_task("ceo", 2, reviewer="none")
    office.update_task("ceo", 2, criteria=["Names 5 competitors"])
    assert office.get(2).criteria == ["Names 5 competitors"]


def test_business_flow_end_to_end(office):
    office.start_goal("Launch a newsletter", criteria=["Has a name and a first issue"], reviewer="human")
    model = ScriptedModel(
        {
            "ceo": [
                [
                    call(
                        "create_task",
                        parent_id=1,
                        title="Write issue #1",
                        assignee="engineer",
                        description="newsletter/issue-1.md",
                        criteria=["Under 600 words", "Ends with a call to subscribe"],
                    )
                ],
                [say("delegated")],
                [
                    call(
                        "review",
                        task_id=2,
                        approve=True,
                        notes="",
                        verdicts=[
                            {"criterion": 1, "passed": True, "reason": "412 words"},
                            {"criterion": 2, "passed": True, "reason": "last line asks to subscribe"},
                        ],
                    )
                ],
                [call("submit", task_id=1, summary="Name: Tiny Signals. Issue 1 in newsletter/issue-1.md")],
                [say("done")],
            ],
            "engineer": [
                [
                    call("write_file", path="newsletter/issue-1.md", content="# Tiny Signals #1\n...\nSubscribe!\n"),
                    call("submit", task_id=2, summary="issue 1 written"),
                ],
                [say("submitted")],
            ],
        }
    )
    Runner(office, model).run()
    assert office.get(2).status == "done"
    assert office.get(1).status == "in_review" and [t.id for t in office.inbox()] == [1]


def test_cli_goal_with_criteria(tmp_path, monkeypatch, capsys):
    from tinyco.cli import main

    monkeypatch.chdir(tmp_path)
    main(["init"])
    assert main(["goal", "Launch", "-a", "Has a price"]) == 1
    assert "judge them" in capsys.readouterr().err
    assert main(["goal", "Launch", "-a", "Has a price", "--review", "human"]) == 0
    assert main(["task", "1"]) == 0
    assert "1. Has a price" in capsys.readouterr().out
