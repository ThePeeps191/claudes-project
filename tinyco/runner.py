"""Running agents: one bounded session per wake, driven by an event queue.

There are no heartbeats. An agent runs only when something happened that
needs it (it was assigned work, a subtask finished, a review was requested,
a human replied). When the queue is empty the company is idle and costs
nothing; ``run`` returns.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from . import pricing
from .briefing import briefing, system_prompt
from .llm import Model, ModelError
from .office import Office
from .store import Wake
from .tools import Toolbox, tools_for

MAX_NUDGES = 2
MODEL_ERROR_RETRY = 300  # seconds before watch mode retries work paused by an API error


@dataclass
class RunSummary:
    wakes: int = 0
    turns: int = 0
    usd: float = 0.0
    stopped: str = "idle"  # idle | budget | fatal | max_wakes | locked
    error: str | None = None
    goals: list = field(default_factory=list)


@contextmanager
def run_lock(state_dir: Path):
    """An exclusive per-company lock so two runs never claim the same wakes."""
    state_dir.mkdir(parents=True, exist_ok=True)
    f = open(state_dir / "run.lock", "a+")
    try:
        try:
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except ImportError:  # Windows
            import msvcrt

            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            yield False
            return
        yield True
    except OSError:
        yield False
    finally:
        f.close()


class Runner:
    def __init__(self, office: Office, model: Model):
        self.office = office
        self.store = office.store
        self.company = office.company
        self.model = model

    # -- budgets ----------------------------------------------------------------

    def period_spent(self, agent: str | None = None) -> float:
        """Spend counted against budgets: everything, or just the current day/week/month."""
        return self.store.spent(agent=agent, since=self.company.period_start())

    def over_budget(self, agent: str) -> str | None:
        spec = self.company.agents[agent]
        per = "" if self.company.budget_period == "total" else f" for this {self.company.budget_period}"
        if self.period_spent() >= self.company.budget_usd:
            return f"company budget of ${self.company.budget_usd:.2f}{per} is used up"
        if spec.budget_usd is not None and self.period_spent(agent) >= spec.budget_usd:
            return f"{agent}'s budget of ${spec.budget_usd:.2f}{per} is used up"
        return None

    def _budget_left(self, agent: str) -> float | None:
        left = self.company.budget_usd - self.period_spent()
        spec = self.company.agents[agent]
        if spec.budget_usd is not None:
            left = min(left, spec.budget_usd - self.period_spent(agent))
        return left

    # -- the queue --------------------------------------------------------------

    def run(self, max_wakes: int | None = None, parallel: int = 1) -> RunSummary:
        """Work through the wake queue until it's empty (or a limit is hit).

        With ``parallel > 1``, up to that many agents work at once. One agent never
        runs two wakes at the same time.
        """
        with run_lock(self.company.state_dir) as locked:
            if not locked:
                summary = RunSummary(stopped="locked")
                summary.error = "another `tinyco run` is already working on this company"
                return summary
            return self._run(max_wakes, parallel)

    def watch(
        self,
        parallel: int = 1,
        poll: float = 2.0,
        stop: threading.Event | None = None,
        on_cycle=None,
    ) -> RunSummary:
        """Stay up: fire routines when due and pick up new work as it arrives.

        Between bursts of work the only activity is a cheap check of the local
        queue every ``poll`` seconds; no model is called while there's nothing to do.
        """
        from .routines import Scheduler

        stop = stop or threading.Event()
        total = RunSummary()
        with run_lock(self.company.state_dir) as locked:
            if not locked:
                total.stopped, total.error = "locked", "another `tinyco run` is already working on this company"
                return total
            sched = Scheduler(self.office, self.company.routines)
            self.store.event("watch_started", None, None, routines=[r.name for r in self.company.routines])
            while not stop.is_set():
                sched.fire_due()
                s = self._run(None, parallel, stop)
                total.wakes += s.wakes
                total.turns += s.turns
                total.usd += s.usd
                if on_cycle is not None:
                    on_cycle(s)
                if s.stopped == "fatal" or (s.stopped == "budget" and self.company.budget_period == "total"):
                    total.stopped, total.error = s.stopped, s.error
                    break
                # Sleep until new work, the next routine, a new budget period, a retry of work paused by
                # an API error, or an hourly re-check, whichever comes first.
                now = time.time()
                deadline = now + 3600
                for t in (sched.next_wakeup(), self.company.next_period_start(now)):
                    if t is not None:
                        deadline = min(deadline, t)
                if any((t.blocked_on or "").startswith("model error:") for t in self.store.tasks(("blocked",))):
                    deadline = min(deadline, now + MODEL_ERROR_RETRY)
                while not stop.is_set() and time.time() < deadline:
                    if self.store.pending_wakes() and s.stopped != "budget":
                        break
                    stop.wait(max(0.05, min(poll, deadline - time.time())))
            total.stopped = total.stopped if total.stopped != "idle" else "stopped"
        return total

    def _run(self, max_wakes: int | None, parallel: int, stop: threading.Event | None = None) -> RunSummary:
        summary = RunSummary()
        start_spend = self.store.spent()
        recovered = self.store.requeue_running()
        if recovered:
            self.store.event("recovered", None, None, wakes=recovered)
        if self.period_spent() < self.company.budget_usd:
            self.office.resume_paused()

        parallel = max(1, int(parallel))
        busy: set[str] = set()
        inflight: dict[Future, Wake] = {}
        pool = ThreadPoolExecutor(max_workers=parallel, thread_name_prefix="tinyco") if parallel > 1 else None
        try:
            while True:
                while summary.stopped == "idle" and len(inflight) < parallel and not (stop and stop.is_set()):
                    if max_wakes is not None and summary.wakes >= max_wakes:
                        summary.stopped = "max_wakes"
                        break
                    if self.period_spent() >= self.company.budget_usd:
                        summary.stopped = "budget"
                        self.store.event("budget_exhausted", None, None, budget=self.company.budget_usd)
                        break
                    wake = self.store.next_wake(busy)
                    if wake is None:
                        break
                    summary.wakes += 1
                    if pool is None:  # sequential: run in this thread so Ctrl+C is immediate
                        self._collect(summary, *self._run_one(wake))
                        continue
                    busy.add(wake.agent)
                    inflight[pool.submit(self._run_one, wake)] = wake
                if not inflight:
                    break
                finished, _ = wait(inflight, return_when=FIRST_COMPLETED)
                for fut in finished:
                    busy.discard(inflight.pop(fut).agent)
                    self._collect(summary, *fut.result())
        except KeyboardInterrupt:
            if pool is not None:
                pool.shutdown(wait=False, cancel_futures=True)
            raise
        finally:
            if pool is not None:
                pool.shutdown(wait=True)

        summary.usd = self.store.spent() - start_spend
        summary.goals = [t for t in self.store.roots()]
        if summary.wakes or summary.stopped != "idle":
            self.store.event(
                "run_finished", None, None, wakes=summary.wakes, usd=round(summary.usd, 4), stopped=summary.stopped
            )
        return summary

    @staticmethod
    def _collect(summary: RunSummary, turns: int, fatal: str | None) -> None:
        summary.turns += turns
        if fatal and summary.stopped != "fatal":
            summary.stopped, summary.error = "fatal", fatal

    def _run_one(self, wake: Wake) -> tuple[int, str | None]:
        """Run one wake. Returns (model calls made, fatal error or None)."""
        try:
            turns = self.run_wake(wake)
            self.store.finish_wake(wake.id)
            return turns, None
        except ModelError as e:
            self.store.event("model_error", wake.agent, wake.task_id, error=str(e))
            if e.fatal:
                self.store.finish_wake(wake.id, "pending")  # retried on the next run
                return 0, str(e)
            self.store.finish_wake(wake.id, "error")
            if e.escalate:
                self._give_up(wake.agent, wake.task_id, str(e))
            else:
                self._pause(wake.agent, wake.task_id, f"model error: {e}")
            return 0, None
        except KeyboardInterrupt:
            self.store.finish_wake(wake.id, "pending")
            raise
        except Exception as e:  # keep the wake so nothing is lost, then stop
            self.store.finish_wake(wake.id, "pending")
            self.store.event("internal_error", wake.agent, wake.task_id, error=f"{type(e).__name__}: {e}")
            return 0, f"{type(e).__name__}: {e}"

    # -- one wake ---------------------------------------------------------------

    def _relevant(self, wake: Wake) -> bool:
        t = self.office.get(wake.task_id)
        if t.status in ("done", "cancelled", "failed"):
            return False
        if t.status == "blocked":
            return any(r.get("reason") == "human_replied" for r in wake.reasons)
        return True

    def run_wake(self, wake: Wake) -> int:
        name = wake.agent
        if name not in self.company.agents:
            self.store.event("skipped", name, wake.task_id, why="agent no longer exists")
            return 0
        if not self._relevant(wake):
            self.store.event("skipped", name, wake.task_id, why="task already closed or blocked")
            return 0
        reason = self.over_budget(name)
        if reason:
            self._pause(name, wake.task_id, f"budget: {reason}")
            return 0

        agent = self.company.agents[name]
        task = self.office.get(wake.task_id)
        if task.assignee == name and task.status == "todo":
            task = self.store.update_task(task.id, status="in_progress")

        self.store.event("wake", name, task.id, reasons=[r.get("reason") for r in wake.reasons])
        since = self.store.last_event_id()
        if agent.runtime == "claude-code":
            from .external import run_claude_code

            turns = run_claude_code(self.office, agent, wake, self._budget_left(name))
            self._after_run(name, task.id, wake, since)
            return turns

        box = Toolbox(self.office, name, task)
        system = system_prompt(self.company, agent)
        messages: list = [{"role": "user", "content": briefing(self.office, agent, wake)}]
        tools = tools_for(agent)
        turns = 0
        overrides = self.company.extra.get("pricing")

        while turns < agent.max_turns:
            reason = self.over_budget(name)
            if reason:
                self._pause(name, task.id, f"budget: {reason}")
                break
            turn = self.model.respond(agent=agent, system=system, messages=messages, tools=tools)
            turns += 1
            usd = pricing.cost(turn.model, turn.usage, overrides)
            self.store.record_spend(name, task.id, turn.model, turn.usage, usd)
            if turn.thinking:
                self.store.event("thinking", name, task.id, text=turn.thinking[:2000])
            for sc in turn.server_calls:
                inp = sc.input if isinstance(sc.input, dict) else {}
                if sc.name.startswith("mcp:"):
                    self.store.event("mcp_call", name, task.id, tool=sc.name[4:], input=_brief_input(inp))
                else:
                    self.store.event(sc.name, name, task.id, query=inp.get("query"), url=inp.get("url"))
            if turn.text:
                self.store.event("said", name, task.id, text=turn.text[:4000])
            messages.append({"role": "assistant", "content": turn.content})

            if turn.stop_reason == "refusal":
                self._give_up(name, task.id, f"model declined: {turn.refusal}")
                break
            if turn.stop_reason == "pause_turn":
                continue
            if not turn.tool_calls:
                break

            results = []
            for call in turn.tool_calls:
                if turn.stop_reason == "max_tokens":
                    out, is_err = (
                        (
                            "Your response hit the output limit before this tool call finished, so it was "
                            "not run. Re-issue it with smaller input (e.g. write large files in parts)."
                        ),
                        True,
                    )
                else:
                    self.store.event("tool", name, task.id, tool=call.name, input=_brief_input(call.input))
                    out, is_err = box.call(call.name, call.input)
                    if is_err:
                        self.store.event("tool_error", name, task.id, tool=call.name, error=out[:500])
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": out, "is_error": is_err})
            messages.append({"role": "user", "content": results})
        else:
            self.store.event("turn_limit", name, task.id, turns=turns)

        self._after_run(name, task.id, wake, since)
        return turns

    # -- outcomes -----------------------------------------------------------------

    def _role(self, name: str, t) -> str | None:
        """The part ``name`` currently plays on task ``t``, if any."""
        if t.assignee == name and t.status in ("todo", "in_progress"):
            return "assignee"
        if t.reviewer == name and t.status == "in_review":
            return "reviewer"
        return None

    def _pause(self, name: str, task_id: int, why: str) -> None:
        """A recoverable stop (budget, API error). Pauses the task only if ``name`` is working on it."""
        t = self.office.get(task_id)
        if self._role(name, t):
            self.office.block(task_id, why, name, wake=name)
        else:
            self.store.event("skipped", name, task_id, why=why)

    def _give_up(self, name: str, task_id: int, why: str) -> None:
        """A stop that needs someone else to decide: escalate to the manager, or to the human at the top."""
        t = self.office.get(task_id)
        role = self._role(name, t)
        if role is None:
            self.store.event("skipped", name, task_id, why=why)
        elif role == "assignee" and t.parent_id is not None:
            self.office._escalate(t, name, why, "")
        else:
            self.office.escalate_to_human(t, name, why)

    def _nudges(self, name: str, task_id: int) -> int:
        """Nudges ``name`` got on this task since the last sign of real progress."""
        n = 0
        for e in reversed(self.store.events(task_id=task_id, limit=300)):
            if e["kind"] in (
                "human_replied",
                "task_updated",
                "checks_run",
                "reviewed",
                "resumed",
                "task_created",
                "reopened",
            ):
                break
            if e["kind"] == "nudged" and e["agent"] == name:
                n += 1
        return n

    def _after_run(self, name: str, task_id: int, wake: Wake | None = None, since: int = 0) -> None:
        """Keep work moving after a run: answer askers, and catch runs that ended without progress."""
        # Whoever asked this agent something in a comment can now look for the answer.
        for r in wake.reasons if wake else []:
            asker = r.get("by")
            if r.get("reason") == "comment" and asker in self.company.agents and asker != name:
                at = self.office.get(task_id)
                if self._role(asker, at) and not self.store.has_pending_wake(asker, task_id):
                    self.store.wake(asker, task_id, "comment_seen", by=name)

        t = self.office.get(task_id)
        role = self._role(name, t)
        if role == "assignee" and self.store.has_open_children(t.id):
            role = None  # waiting on its own subtasks is fine
        if role is None or self.store.has_pending_wake(name, t.id):
            return
        asked = [
            e
            for e in self.store.events(task_id=t.id, limit=50, kinds=("comment",))
            if e["id"] > since and e["agent"] == name and e["data"].get("notify")
        ]
        if asked:
            self.store.event("waiting", name, t.id, on=sorted({n for e in asked for n in e["data"]["notify"]}))
            return  # waiting for an answer; the person asked is woken and we'll be woken after
        if self._nudges(name, t.id) < MAX_NUDGES:
            self.store.event("nudged", name, t.id)
            self.store.wake(name, t.id, "nudge")
            return
        why = "stalled: runs keep ending without progress"
        if t.parent_id is not None and role == "assignee":
            self.office._escalate(t, name, why, "")
        else:
            self.office.escalate_to_human(t, name, why)


def _brief_input(v) -> dict:
    if not isinstance(v, dict):
        return {"input": str(v)[:200]}
    out = {}
    for k, val in v.items():
        s = val if isinstance(val, str) else repr(val)
        out[k] = s if len(s) <= 200 else s[:200] + f"… ({len(s)} chars)"
    return out
