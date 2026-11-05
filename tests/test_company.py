"""End-to-end runs of whole companies against a scripted model."""

from __future__ import annotations

from conftest import ScriptedModel, call, say

from tinyco.runner import Runner

CHECK = 'python -c "import calc; assert calc.add(2, 3) == 5"'
GOOD = "def add(a, b):\n    return a + b\n"
BAD = "def add(a, b):\n    return a - b\n"


def test_full_company_happy_path(office):
    goal = office.start_goal("Build a calculator", checks=[CHECK])
    assert goal.id == 1
    model = ScriptedModel(
        {
            "ceo": [
                [
                    say("Delegating."),
                    call(
                        "create_task",
                        parent_id=1,
                        title="Implement add",
                        assignee="engineer",
                        description="calc.py with add()",
                        checks=[CHECK],
                        reviewer="reviewer",
                    ),
                ],
                [say("Waiting for the engineer.")],
                # woken: subtask done
                [call("submit", task_id=1, summary="calc.add works")],
                [say("Done.")],
            ],
            "engineer": [
                [call("write_file", path="calc.py", content=GOOD)],
                [call("run", command=CHECK)],
                [call("submit", task_id=2, summary="Implemented add in calc.py")],
                [say("Submitted.")],
            ],
            "reviewer": [
                [call("read_file", path="calc.py")],
                [call("review", task_id=2, approve=True, notes="Looks right.")],
                [say("Approved.")],
            ],
        }
    )
    summary = Runner(office, model).run()

    assert summary.stopped == "idle"
    t1, t2 = office.get(1), office.get(2)
    assert t2.status == "done" and t2.summary == "Implemented add in calc.py"
    assert t1.status == "done"
    assert model.remaining() == {}
    # Wake order: ceo (assigned) -> engineer -> reviewer -> ceo (subtask done)
    assert [c["agent"] for c in model.calls] == [
        "ceo",
        "ceo",
        "engineer",
        "engineer",
        "engineer",
        "engineer",
        "reviewer",
        "reviewer",
        "reviewer",
        "ceo",
        "ceo",
    ]
    kinds = [e["kind"] for e in office.store.events(limit=500)]
    assert "goal_done" in kinds
    assert office.store.spent() > 0
    assert set(office.store.spend_by_agent()) == {"ceo", "engineer", "reviewer"}
    assert not office.store.pending_wakes()


def test_ceo_briefing_includes_subtask_summary(office):
    office.start_goal("Build a calculator", checks=[CHECK])
    model = ScriptedModel(
        {
            "ceo": [
                [
                    call(
                        "create_task",
                        parent_id=1,
                        title="Implement add",
                        assignee="engineer",
                        description="x",
                        checks=[CHECK],
                    )
                ],
                [say("ok")],
                [say("ok")],
            ],
            "engineer": [
                [call("write_file", path="calc.py", content=GOOD), call("submit", task_id=2, summary="did it")],
                [say("done")],
            ],
        }
    )
    Runner(office, model).run(max_wakes=3)
    ceo_second_wake = [c for c in model.calls if c["agent"] == "ceo"][2]
    brief = ceo_second_wake["messages"][0]["content"]
    assert "Subtask T2 is done" in brief
    assert "Their summary: did it" in brief


def test_failed_checks_retry_then_escalate_to_manager(office):
    office.start_goal("Build a calculator")
    model = ScriptedModel(
        {
            "ceo": [
                [
                    call(
                        "create_task",
                        parent_id=1,
                        title="Implement add",
                        assignee="engineer",
                        description="x",
                        checks=[CHECK],
                    )
                ],
                [say("waiting")],
                [say("I'll look at it.")],  # woken with subtask_failed
            ],
            "engineer": [
                [call("write_file", path="calc.py", content=BAD), call("submit", task_id=2, summary="1")],
                [call("submit", task_id=2, summary="2")],
                [call("submit", task_id=2, summary="3")],
                [say("giving up")],
            ],
        }
    )
    Runner(office, model).run()
    t2 = office.get(2)
    assert t2.status == "failed"
    assert t2.attempts == 3
    escalated = office.store.events(task_id=2, kinds=("escalated",))
    assert len(escalated) == 1 and escalated[0]["agent"] == "engineer"
    briefs = [c["messages"][0]["content"] for c in model.calls if c["agent"] == "ceo"]
    brief = next(b for b in briefs if "Subtask T2 failed" in b)
    assert "AssertionError" in brief  # the last check output travels with the escalation
    # The failing check output was shown to the engineer each time.
    eng_results = [
        m
        for c in model.calls
        if c["agent"] == "engineer"
        for m in c["messages"]
        if m["role"] == "user" and isinstance(m["content"], list)
    ]
    assert any("Checks failed" in r["content"] for m in eng_results for r in m["content"])


def test_manager_can_reassign_failed_task(office):
    office.start_goal("Build")
    office.create_task("ceo", 1, "Implement add", "engineer", "x", [CHECK])
    t2 = office.get(2)
    office.store.update_task(t2.id, status="failed", attempts=3)
    msg = office.update_task("ceo", 2, assignee="ceo")
    assert "handed it to ceo" in msg
    t2 = office.get(2)
    assert (t2.status, t2.assignee, t2.attempts) == ("todo", "ceo", 0)
    assert office.store.has_pending_wake("ceo", 2)


def test_subtask_completions_coalesce_into_one_wake(office):
    office.start_goal("Two things")
    office.create_task("ceo", 1, "A", "engineer", "", ["true"])
    office.create_task("ceo", 1, "B", "reviewer", "", ["true"])
    office.store.next_wake()  # consume the goal wake for ceo
    office.submit("engineer", 2, "a")
    office.submit("reviewer", 3, "b")
    ceo_wakes = [w for w in office.store.pending_wakes() if w.agent == "ceo"]
    assert len(ceo_wakes) == 1
    assert [r["reason"] for r in ceo_wakes[0].reasons] == ["subtask_done", "subtask_done"]


def test_run_that_ends_without_progress_is_nudged_then_escalated(office):
    office.start_goal("Build")
    office.create_task("ceo", 1, "Implement", "engineer", "", ["true"])
    office.store.finish_wake(office.store.next_wake().id)  # drop the ceo's goal wake for this test
    model = ScriptedModel({"engineer": [[say("hmm")], [say("hmm")], [say("hmm")]], "ceo": [[say("noted")]]})
    Runner(office, model).run()
    assert len(office.store.events(task_id=2, kinds=("nudged",))) == 2
    assert office.get(2).status == "failed"
    # The CEO also did nothing with its goal afterwards, so that stalled up to the human.
    assert office.get(1).status == "blocked" and "stalled" in office.get(1).blocked_on
    assert [t.id for t in office.inbox()] == [1]
    briefs = [c["messages"][0]["content"] for c in model.calls if c["agent"] == "ceo"]
    assert any("Subtask T2 failed (stalled" in b for b in briefs)


def test_agent_budget_blocks_then_resumes(make_office):
    data = {
        "company": {"name": "B", "budget_usd": 100, "shell": "allow"},
        "agents": {"solo": {"title": "Solo", "budget_usd": 0.001}},
    }
    office = make_office(data)
    office.start_goal("Do it", checks=["true"])
    model = ScriptedModel({"solo": [[call("note", text="start")], [say("more")]]})
    Runner(office, model).run()
    t = office.get(1)
    assert t.status == "blocked" and t.blocked_on.startswith("budget:")
    # One call was made, then the budget check stopped the agent before the next one.
    assert len(model.calls) == 1

    office.company.agents["solo"].budget_usd = 10
    model2 = ScriptedModel({"solo": [[call("submit", task_id=1, summary="done")], [say("ok")]]})
    Runner(office, model2).run()
    assert office.get(1).status == "done"
    assert "paused (budget" in model2.calls[0]["messages"][0]["content"]


def test_company_budget_stops_queue(make_office):
    data = {"company": {"name": "B", "budget_usd": 0.0001, "shell": "allow"}, "agents": {"solo": {}}}
    office = make_office(data)
    office.start_goal("Do it")
    model = ScriptedModel({"solo": [[call("note", text="x")], [say("y")]]})
    summary = Runner(office, model).run()
    assert len(model.calls) == 1
    assert office.get(1).status == "blocked"
    assert summary.stopped in ("idle", "budget")


def test_ask_human_and_reply(office):
    office.start_goal("Pick a name")
    model = ScriptedModel({"ceo": [[call("ask_human", task_id=1, question="Blue or green?")], [say("waiting")]]})
    Runner(office, model).run()
    assert office.get(1).status == "blocked"
    assert [t.id for t in office.inbox()] == [1]
    office.reply(1, "Green.")
    assert office.get(1).status == "in_progress"
    model2 = ScriptedModel({"ceo": [[call("submit", task_id=1, summary="Green")], [say("ok")]]})
    Runner(office, model2).run()
    assert office.get(1).status == "done"
    assert "Reply: Green." in model2.calls[0]["messages"][0]["content"]


def test_human_review_of_goal(office):
    office.start_goal("Write a haiku", reviewer="human")
    model = ScriptedModel(
        {
            "ceo": [
                [
                    call("write_file", path="haiku.txt", content="a\nb\nc\n"),
                    call("submit", task_id=1, summary="haiku.txt"),
                ],
                [say("ok")],
            ]
        }
    )
    Runner(office, model).run()
    assert office.get(1).status == "in_review"
    assert [t.id for t in office.inbox()] == [1]
    office.human_review(1, False, "Make it about the sea.")
    assert office.get(1).status == "in_progress"
    assert office.store.has_pending_wake("ceo", 1)
    model2 = ScriptedModel({"ceo": [[call("submit", task_id=1, summary="sea haiku")], [say("ok")]]})
    Runner(office, model2).run()
    assert "Make it about the sea." in model2.calls[0]["messages"][0]["content"]
    office.human_review(1, True)
    assert office.get(1).status == "done"


def test_refusal_blocks_task(office):
    office.start_goal("Something")
    model = ScriptedModel({"ceo": [[say("")]]}, stop_reasons={("ceo", 1): "refusal"})
    Runner(office, model).run()
    t = office.get(1)
    assert t.status == "blocked" and "declined" in t.blocked_on


def test_max_tokens_tool_calls_are_not_executed(office):
    office.start_goal("Something")
    model = ScriptedModel(
        {"ceo": [[call("write_file", path="big.txt", content="partial")], [say("retrying later")]]},
        stop_reasons={("ceo", 1): "max_tokens"},
    )
    Runner(office, model).run(max_wakes=1)
    assert not (office.company.workspace / "big.txt").exists()
    results = model.calls[1]["messages"][-1]["content"]
    assert results[0]["is_error"] and "output limit" in results[0]["content"]


def test_interrupted_run_requeues(office):
    office.start_goal("Something")
    w = office.store.next_wake()
    assert w is not None and not office.store.pending_wakes()
    assert office.store.requeue_running() == 1
    assert len(office.store.pending_wakes()) == 1


def test_tools_and_system_prompt_are_stable_for_caching(office):
    office.start_goal("Build", checks=[CHECK])
    model = ScriptedModel(
        {
            "ceo": [[call("note", text="a")], [call("note", text="b")], [say("ok")]],
        }
    )
    Runner(office, model).run(max_wakes=1)
    systems = {c["system"] for c in model.calls}
    assert len(systems) == 1
    assert all(c["tools"] is model.calls[0]["tools"] for c in model.calls)
    # The message history only ever grows (append-only), which keeps cached prefixes valid.
    for prev, nxt in zip(model.calls, model.calls[1:], strict=False):
        assert nxt["messages"][: len(prev["messages"])] == prev["messages"]


def test_unexpected_error_keeps_the_wake(office):
    class Boom:
        def respond(self, **kw):
            raise RuntimeError("kaboom")

    office.start_goal("Something")
    summary = Runner(office, Boom()).run()
    assert summary.stopped == "fatal" and "kaboom" in summary.error
    assert len(office.store.pending_wakes()) == 1


def test_parallel_agents_really_overlap(make_office):
    import threading

    data = {
        "company": {"name": "P", "budget_usd": 5, "shell": "allow"},
        "agents": {"lead": {}, "a": {"reports_to": "lead"}, "b": {"reports_to": "lead"}},
    }
    office = make_office(data)
    office.start_goal("Two parts")
    office.create_task("lead", 1, "Part A", "a", "", ["true"])
    office.create_task("lead", 1, "Part B", "b", "", ["true"])
    office.store.finish_wake(office.store.next_wake().id)  # skip the lead's goal wake
    barrier = threading.Barrier(2, timeout=5)

    def meet(task_id):
        def turn(messages):
            barrier.wait()  # deadlocks (and times out) unless a and b run at the same time
            return [call("submit", task_id=task_id, summary="ok")]

        return turn

    def lead(messages):
        # Depending on timing, the lead may hear about a's completion while b is still working.
        if isinstance(messages[-1]["content"], list):  # just got tool results back
            return [say("ok")]
        if "Subtasks (2: 2 done)" in messages[0]["content"]:
            return [call("submit", task_id=1, summary="both parts")]
        return [say("waiting for the other part")]

    model = ScriptedModel(
        {
            "a": [meet(2), [say("done")]],
            "b": [meet(3), [say("done")]],
            "lead": [lead] * 4,
        }
    )
    summary = Runner(office, model).run(parallel=2)
    assert summary.stopped == "idle", summary.error
    assert {t.id: t.status for t in office.store.tasks()} == {1: "done", 2: "done", 3: "done"}
    # Completions arriving together merge into one wake; arriving apart, the lead hears about each.
    lead_wakes = [e for e in office.store.events(limit=200, kinds=("wake",)) if e["agent"] == "lead"]
    assert 1 <= len(lead_wakes) <= 2


def test_one_agent_never_runs_two_wakes_at_once(office):
    import threading

    office.start_goal("Goal")
    office.create_task("ceo", 1, "One", "engineer", "", ["true"])
    office.create_task("ceo", 1, "Two", "engineer", "", ["true"])
    office.store.finish_wake(office.store.next_wake().id)
    active, peak = [0], [0]
    lock = threading.Lock()

    def work(task_id):
        def turn(messages):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            import time

            time.sleep(0.05)
            with lock:
                active[0] -= 1
            return [call("submit", task_id=task_id, summary="ok")]

        return turn

    model = ScriptedModel({"engineer": [work(2), [say("ok")], work(3), [say("ok")]], "ceo": [[say("wait")]] * 3})
    Runner(office, model).run(parallel=4)
    assert peak[0] == 1
    assert office.get(2).status == office.get(3).status == "done"
