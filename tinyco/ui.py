"""Terminal rendering: the live event feed and the board."""

from __future__ import annotations

import os
import sys
import time

from .office import Office
from .store import Task

PALETTE = ["36", "35", "33", "32", "34", "91", "96", "95"]
STATUS_MARK = {
    "todo": "○",
    "in_progress": "◐",
    "in_review": "◑",
    "blocked": "■",
    "done": "●",
    "failed": "✕",
    "cancelled": "–",
}


def use_color(stream=sys.stdout) -> bool:
    return stream.isatty() and not os.environ.get("NO_COLOR") and os.environ.get("TERM") != "dumb"


class Style:
    def __init__(self, enabled: bool):
        self.enabled = enabled
        self._agents: dict[str, str] = {}

    def _c(self, code: str, s: str) -> str:
        return f"\033[{code}m{s}\033[0m" if self.enabled else s

    def dim(self, s: str) -> str:
        return self._c("2", s)

    def bold(self, s: str) -> str:
        return self._c("1", s)

    def green(self, s: str) -> str:
        return self._c("32", s)

    def red(self, s: str) -> str:
        return self._c("31", s)

    def yellow(self, s: str) -> str:
        return self._c("33", s)

    def agent(self, name: str | None) -> str:
        if not name:
            return ""
        if name == "human":
            return self._c("1", "human")
        code = self._agents.setdefault(name, PALETTE[len(self._agents) % len(PALETTE)])
        return self._c("1;" + code, name)

    def status(self, status: str) -> str:
        mark = f"{STATUS_MARK.get(status, '?')} {status}"
        if status == "done":
            return self.green(mark)
        if status in ("failed", "blocked"):
            return self.red(mark)
        if status in ("in_review", "in_progress"):
            return self.yellow(mark)
        return self.dim(mark)


def _one_line(s: str, n: int = 160) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


class Feed:
    """Prints store events as they happen."""

    QUIET = {"tool", "file_read", "recovered"}

    def __init__(self, style: Style, verbose: bool = False, out=sys.stdout):
        self.s = style
        self.verbose = verbose
        self.out = out

    def __call__(self, e: dict) -> None:
        line = self.render(e)
        if line:
            print(line, file=self.out, flush=True)

    def render(self, e: dict) -> str | None:
        s, k, d = self.s, e["kind"], e["data"]
        who = s.agent(e["agent"])
        t = f"T{e['task_id']}" if e.get("task_id") else ""
        stamp = s.dim(time.strftime("%H:%M:%S", time.localtime(e["ts"])))
        msg: str | None = None
        if k == "goal_created":
            msg = f"{s.bold('◆ goal')} {t} {d.get('title', '')} → {s.agent(d.get('assignee'))}"
        elif k == "wake":
            msg = f"▸ {who} wakes on {t} {s.dim('(' + ', '.join(d.get('reasons', [])) + ')')}"
        elif k == "thinking":
            msg = s.dim(f"  … {_one_line(d.get('text', ''), 200)}") if self.verbose else None
        elif k == "said":
            msg = s.dim(f"  “{_one_line(d.get('text', ''), 200)}”")
        elif k == "task_created":
            checks = d.get("checks") or []
            extra = f"checks: {', '.join(checks)}" if checks else ""
            if d.get("reviewer"):
                extra += (" · " if extra else "") + f"review: {d['reviewer']}"
            msg = f"  ＋ {who} → {s.agent(d.get('assignee'))}  {t} {d.get('title', '')}" + (
                f"  {s.dim('[' + extra + ']')}" if extra else ""
            )
        elif k == "file_written":
            msg = f"  ✎ {who} {'created' if d.get('created') else 'edited'} {d.get('path')}"
        elif k == "command_done":
            ok = d.get("exit") == 0
            msg = f"  $ {_one_line(d.get('command', ''), 100)}  " + (
                s.green("✓") if ok else s.red(f"✗ exit {d.get('exit')}")
            )
        elif k == "checks_run":
            res = d.get("results", [])
            n_ok = sum(1 for r in res if r.get("exit") == 0)
            if d.get("passed"):
                msg = f"  {s.green('✔ checks passed')} {s.dim(f'({n_ok}/{len(res)})')} on {t}"
            else:
                msg = f"  {s.red('✘ checks failed')} {s.dim(f'({n_ok}/{len(res)})')} on {t}"
        elif k == "review_requested":
            msg = f"  ⧗ {t} waiting for review by {s.agent(d.get('reviewer'))}"
        elif k == "reviewed":
            if d.get("approved"):
                msg = f"  {s.green('✔')} {who} approved {t}"
            else:
                msg = f"  {s.red('↺')} {who} requested changes on {t}: {s.dim(_one_line(d.get('notes', ''), 120))}"
        elif k == "task_done":
            msg = f"{s.green('✓')} {t} done {s.dim(d.get('title', ''))}"
        elif k == "goal_done":
            msg = s.bold(s.green(f"★ goal {t} complete: {d.get('title', '')}"))
        elif k in ("task_failed", "escalated"):
            msg = f"{s.red('⚠')} {t} escalated{' to ' + d['to'] if d.get('to') else ''}: {d.get('why', '')}"
        elif k == "task_cancelled":
            msg = s.dim(f"  – {t} cancelled")
        elif k == "comment":
            msg = f"  💬 {who} on {t}: {_one_line(d.get('text', ''), 140)}"
        elif k == "asked_human":
            hint = s.dim('(answer with: tinyco reply {} "...")'.format(e["task_id"]))
            msg = f"{s.yellow('?')} {who} asks you about {t}: {d.get('question', '')}  {hint}"
        elif k == "blocked":
            msg = f"{s.red('■')} {t} blocked: {d.get('why', '')}"
        elif k == "nudged":
            msg = s.dim(f"  ↻ {e['agent']} ended a run without finishing {t}; re-queued")
        elif k == "tool_error":
            msg = s.dim(f"  ! {d.get('tool')}: {_one_line(d.get('error', ''), 140)}") if self.verbose else None
        elif k == "model_error":
            msg = f"{s.red('!')} model error for {who} on {t}: {d.get('error')}"
        elif k == "budget_exhausted":
            msg = s.red(f"■ company budget (${d.get('budget')}) used up; stopping")
        elif k == "turn_limit":
            msg = s.dim(f"  ⏱ {e['agent']} hit its turn limit on {t}")
        elif k == "skipped":
            msg = s.dim(f"  · skipped wake for {e['agent']} on {t}: {d.get('why')}") if self.verbose else None
        elif k == "resumed":
            msg = f"  ▶ {t} resumed"
        elif k == "tool" and self.verbose:
            msg = s.dim(f"  · {who} {d.get('tool')} {_one_line(str(d.get('input', '')), 120)}")
        if msg is None:
            return None
        return f"{stamp} {msg}"


def board(office: Office, style: Style) -> str:
    s = style
    store = office.store
    lines: list[str] = []

    def walk(t: Task, depth: int) -> None:
        cost = store.subtree_spend(t.id)
        cost_s = s.dim(f"${cost:.2f}") if cost else ""
        who = s.agent(t.assignee)
        extra = ""
        if t.status == "blocked" and t.blocked_on:
            extra = s.red(f"  ← {t.blocked_on}")
        elif t.status == "in_review":
            extra = s.dim(f"  (review: {t.reviewer})")
        if t.attempts:
            extra += s.dim(f"  attempts: {t.attempts}")
        lines.append(f"{'   ' * depth}{s.status(t.status):<24} {s.bold(t.ref)} {t.title}  {who} {cost_s}{extra}")
        for c in store.children(t.id):
            walk(c, depth + 1)

    roots = store.roots()
    if not roots:
        return s.dim('No goals yet. Add one with: tinyco goal "..."')
    for r in roots:
        walk(r, 0)
    return "\n".join(lines)
