"""Measure tinyco's own overhead (scheduling, SQLite, briefings, checks) with a zero-latency model.

    python benchmarks/scheduler.py --tasks 300 --engineers 10 [--profile]

A CEO delegates N subtasks round-robin to E engineers; each engineer submits
its task (check: `true`); the CEO submits the goal once everything is done.
Model calls are instant, so the time measured is tinyco's.
"""

from __future__ import annotations

import argparse
import cProfile
import pstats
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tinyco.config import parse  # noqa: E402
from tinyco.llm import normalize  # noqa: E402
from tinyco.office import Office  # noqa: E402
from tinyco.runner import Runner  # noqa: E402
from tinyco.scripted import call, say  # noqa: E402
from tinyco.store import Store  # noqa: E402


class Bench:
    """A model that reacts to the briefing instead of following a fixed script."""

    def __init__(self, n: int, engineers: int):
        self.n, self.engineers, self.calls = n, engineers, 0
        self.ceo_delegated = False

    def respond(self, *, agent, system, messages, tools):
        self.calls += 1
        last = messages[-1]
        if last["role"] == "user" and isinstance(last["content"], list):
            return normalize([say("ok")], "end_turn", agent.model, {"input": 0, "output": 0})
        brief = messages[0]["content"]
        task_id = int(re.search(r"## T(\d+):", brief).group(1))
        if agent.name == "ceo":
            if not self.ceo_delegated:
                self.ceo_delegated = True
                blocks = [
                    call(
                        "create_task",
                        parent_id=1,
                        title=f"Part {i}",
                        description="do it",
                        assignee=f"eng{i % self.engineers}",
                        checks=["true"],
                    )
                    for i in range(self.n)
                ]
            elif re.search(rf"Subtasks \({self.n}: {self.n} done\)", brief):
                blocks = [call("submit", task_id=1, summary="all parts done")]
            else:
                return normalize([say("waiting")], "end_turn", agent.model, {"input": 0, "output": 0})
        else:
            blocks = [call("submit", task_id=task_id, summary="done")]
        return normalize(blocks, "tool_use", agent.model, {"input": 0, "output": 0})


def run(n: int, engineers: int, parallel: int, git: bool) -> dict:
    root = Path(tempfile.mkdtemp(prefix="tinyco-bench-"))
    agents = {"ceo": {"budget_usd": 1000}}
    for i in range(engineers):
        agents[f"eng{i}"] = {"reports_to": "ceo"}
    company = parse(
        {"company": {"budget_usd": 1000, "shell": "allow", "git": git, "parallel": parallel}, "agents": agents}, root
    )
    office = Office(company, Store(company.state_dir / "state.db"))
    office.start_goal("Benchmark goal", checks=["true"])
    model = Bench(n, engineers)
    t0 = time.perf_counter()
    summary = Runner(office, model).run(parallel=parallel)
    took = time.perf_counter() - t0
    done = sum(1 for t in office.store.tasks() if t.status == "done")
    return {
        "tasks": n,
        "engineers": engineers,
        "parallel": parallel,
        "seconds": round(took, 3),
        "wakes": summary.wakes,
        "model_calls": model.calls,
        "done": done,
        "ms_per_wake": round(1000 * took / max(summary.wakes, 1), 2),
        "stopped": summary.stopped,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--tasks", type=int, default=300)
    p.add_argument("--engineers", type=int, default=10)
    p.add_argument("--parallel", type=int, default=1)
    p.add_argument("--git", action="store_true")
    p.add_argument("--profile", action="store_true")
    a = p.parse_args()
    if a.profile:
        prof = cProfile.Profile()
        prof.enable()
    print(run(a.tasks, a.engineers, a.parallel, a.git))
    if a.profile:
        prof.disable()
        pstats.Stats(prof).sort_stats("cumulative").print_stats(25)


if __name__ == "__main__":
    main()
