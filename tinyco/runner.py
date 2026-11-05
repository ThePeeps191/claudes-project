"""Running agents: one bounded session per wake, driven by an event queue.

There are no heartbeats. An agent runs only when something happened that
needs it (it was assigned work, a subtask finished, a review was requested,
a human replied). When the queue is empty the company is idle and costs
nothing; ``run`` returns.
"""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field

from . import pricing
from .briefing import briefing, system_prompt
from .llm import Model, ModelError
from .office import HUMAN, Office
from .store import Wake
from .tools import Toolbox, tools_for

MAX_NUDGES = 2


@dataclass
class RunSummary:
    wakes: int = 0
    turns: int = 0
    usd: float = 0.0
    stopped: str = "idle"  # idle | budget | fatal | max_wakes
    error: str | None = None
    goals: list = field(default_factory=list)


class Runner:
    def __init__(self, office: Office, model: Model):
        self.office = office
        self.store = office.store
        self.company = office.company
        self.model = model

    # -- budgets ----------------------------------------------------------------

    def over_budget(self, agent: str) -> str | None:
        spec = self.company.agents[agent]
        if self.store.spent() >= self.company.budget_usd:
            return f"company budget of ${self.company.budget_usd:.2f} is used up"
        if spec.budget_usd is not None and self.store.spent(agent=agent) >= spec.budget_usd:
            return f"{agent}'s budget of ${spec.budget_usd:.2f} is used up"
        return None

    def _budget_left(self, agent: str) -> float | None:
        left = self.company.budget_usd - self.store.spent()
        spec = self.company.agents[agent]
        if spec.budget_usd is not None:
            left = min(left, spec.budget_usd - self.store.spent(agent=agent))
        return left

    # -- the queue --------------------------------------------------------------

    def run(self, max_wakes: int | None = None, parallel: int = 1) -> RunSummary:
        """Work through the wake queue until it's empty (or a limit is hit).

        With ``parallel > 1``, up to that many agents work at once. One agent never
        runs two wakes at the same time.
        """
        summary = RunSummary()
        start_spend = self.store.spent()
        recovered = self.store.requeue_running()
        if recovered:
            self.store.event("recovered", None, None, wakes=recovered)
        if self.store.spent() < self.company.budget_usd:
            self.office.resume_paused()

        parallel = max(1, int(parallel))
        busy: set[str] = set()
        inflight: dict[Future, Wake] = {}
        pool = ThreadPoolExecutor(max_workers=parallel, thread_name_prefix="tinyco") if parallel > 1 else None
        try:
            while True:
                while summary.stopped == "idle" and len(inflight) < parallel:
                    if max_wakes is not None and summary.wakes >= max_wakes:
                        summary.stopped = "max_wakes"
                        break
                    if self.store.spent() >= self.company.budget_usd:
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
            self.office.block(wake.task_id, f"model error: {e}", wake.agent)
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
            self.office.block(wake.task_id, f"budget: {reason}", name)
            return 0

        agent = self.company.agents[name]
        task = self.office.get(wake.task_id)
        if task.assignee == name and task.status == "todo":
            task = self.store.update_task(task.id, status="in_progress")

        self.store.event("wake", name, task.id, reasons=[r.get("reason") for r in wake.reasons])
        if agent.runtime == "claude-code":
            from .external import run_claude_code

            turns = run_claude_code(self.office, agent, wake, self._budget_left(name))
            self._after_run(name, task.id)
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
                self.office.block(task.id, f"budget: {reason}", name)
                break
            turn = self.model.respond(agent=agent, system=system, messages=messages, tools=tools)
            turns += 1
            usd = pricing.cost(turn.model, turn.usage, overrides)
            self.store.record_spend(name, task.id, turn.model, turn.usage, usd)
            if turn.thinking:
                self.store.event("thinking", name, task.id, text=turn.thinking[:2000])
            for sc in turn.server_calls:
                inp = sc.input if isinstance(sc.input, dict) else {}
                self.store.event(sc.name, name, task.id, query=inp.get("query"), url=inp.get("url"))
            if turn.text:
                self.store.event("said", name, task.id, text=turn.text[:4000])
            messages.append({"role": "assistant", "content": turn.content})

            if turn.stop_reason == "refusal":
                self.office.block(task.id, f"model declined: {turn.refusal}", name)
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

        self._after_run(name, task.id)
        return turns

    def _after_run(self, name: str, task_id: int) -> None:
        """Catch runs that ended without moving their task forward."""
        t = self.office.get(task_id)
        owes = (
            t.assignee == name
            and t.status in ("todo", "in_progress")
            and not any(c.is_open for c in self.store.children(t.id))
        )
        owes = owes or (t.reviewer == name and t.status == "in_review")
        if not owes or self.store.has_pending_wake(name, t.id):
            return
        nudges = len(self.store.events(task_id=t.id, limit=100, kinds=("nudged",)))
        if nudges < MAX_NUDGES:
            self.store.event("nudged", name, t.id)
            self.store.wake(name, t.id, "nudge")
            return
        why = "stalled: runs keep ending without progress"
        if t.parent_id is not None and t.assignee == name:
            self.office._escalate(t, name, why, "")
        else:
            self.store.update_task(t.id, status="blocked", blocked_on=f"{HUMAN}: {why}")
            self.store.event("escalated", name, t.id, why=why, to=HUMAN)


def _brief_input(v) -> dict:
    if not isinstance(v, dict):
        return {"input": str(v)[:200]}
    out = {}
    for k, val in v.items():
        s = val if isinstance(val, str) else repr(val)
        out[k] = s if len(s) <= 200 else s[:200] + f"… ({len(s)} chars)"
    return out
