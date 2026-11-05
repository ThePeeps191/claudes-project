"""The rules of the company: delegation, submission, review, escalation, wakes.

Both agents (through tools) and humans (through the CLI) act through
``Office``, so the same rules apply to everyone. Each operation returns a
short plain-text result that is shown to the caller.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from .config import Company
from .store import CLOSED, Store, Task

HUMAN = "human"
OUTPUT_TAIL = 4000

# approve(kind, detail) -> bool. kind is "shell" or "check".
Approver = Callable[[str, str], bool]


class OfficeError(Exception):
    """An operation was not allowed; the message is shown to the caller."""


@dataclass
class CheckResult:
    command: str
    exit_code: int | None
    output: str

    @property
    def passed(self) -> bool:
        return self.exit_code == 0


def tail(text: str, limit: int = OUTPUT_TAIL) -> str:
    text = text.rstrip()
    if len(text) <= limit:
        return text
    return f"[... {len(text) - limit} earlier characters omitted ...]\n" + text[-limit:]


def child_env() -> dict[str, str]:
    """Environment for commands agents run: inherit, minus the company's own API credentials."""
    env = dict(os.environ)
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        env.pop(k, None)
    return env


class Office:
    def __init__(self, company: Company, store: Store, approve: Approver | None = None):
        self.company = company
        self.store = store
        self._approve = approve
        self.company.workspace.mkdir(parents=True, exist_ok=True)

    # -- helpers ----------------------------------------------------------------

    def get(self, task_id: int) -> Task:
        t = self.store.task(int(task_id))
        if t is None:
            raise OfficeError(f"T{task_id} does not exist")
        return t

    def approve(self, kind: str, detail: str) -> bool:
        policy = self.company.shell
        if policy == "allow":
            return True
        if policy == "deny" or self._approve is None:
            return False
        return self._approve(kind, detail)

    def _check_agent(self, name: str, allow_human: bool = False) -> None:
        if name == HUMAN and allow_human:
            return
        if name not in self.company.agents:
            known = ", ".join(self.company.agents)
            raise OfficeError(f"unknown agent {name!r} (agents: {known})")

    def can_assign(self, by: str, assignee: str) -> bool:
        if by == HUMAN:
            return True
        return assignee == by or assignee in self.company.subtree(by)

    def owner_of_parent(self, task: Task) -> str | None:
        if task.parent_id is None:
            return None
        return self.get(task.parent_id).assignee

    # -- goals & tasks ----------------------------------------------------------

    def start_goal(
        self, title: str, description: str = "", checks: list[str] | None = None, reviewer: str | None = None
    ) -> Task:
        if reviewer is not None:
            self._check_agent(reviewer, allow_human=True)
        top = self.company.top.name
        t = self.store.create_task(title, top, HUMAN, description, None, checks or [], reviewer)
        self.store.event("goal_created", HUMAN, t.id, title=title, assignee=top, checks=t.checks)
        self.store.wake(top, t.id, "assigned", by=HUMAN)
        return t

    def create_task(
        self,
        by: str,
        parent_id: int,
        title: str,
        assignee: str,
        description: str = "",
        checks: list[str] | None = None,
        reviewer: str | None = None,
    ) -> str:
        parent = self.get(parent_id)
        if by != HUMAN and parent.assignee != by:
            raise OfficeError(f"you can only break down tasks you own; {parent.ref} belongs to {parent.assignee}")
        if not parent.is_open:
            raise OfficeError(f"{parent.ref} is {parent.status}; you can't add subtasks to it")
        self._check_agent(assignee)
        if not self.can_assign(by, assignee):
            allowed = sorted(self.company.subtree(by) | {by})
            raise OfficeError(f"{assignee} doesn't report to you; you can assign to: {', '.join(allowed)}")
        checks = [c.strip() for c in (checks or []) if c and c.strip()]
        if reviewer is not None:
            self._check_agent(reviewer, allow_human=True)
            if reviewer == assignee:
                raise OfficeError("the reviewer must be someone other than the assignee")
        if not checks and reviewer is None:
            raise OfficeError(
                "every task needs a definition of done: give at least one check command "
                "(exits 0 only when the work is right, e.g. a test) or a reviewer"
            )
        depth = len(self.store.ancestry(parent.id))
        if depth >= 6:
            raise OfficeError("tasks can't be nested more than 6 levels deep; do this work yourself")
        dup = self.store.find_open_sibling(parent.id, title)
        if dup is not None:
            return f"{dup.ref} already exists with this title (assigned to {dup.assignee}, {dup.status}); not creating a duplicate."
        t = self.store.create_task(title, assignee, by, description, parent.id, checks, reviewer)
        self.store.event(
            "task_created", by, t.id, title=title, assignee=assignee, parent=parent.id, checks=checks, reviewer=reviewer
        )
        if assignee != by:
            self.store.wake(assignee, t.id, "assigned", by=by)
        if parent.status == "todo":
            self.store.update_task(parent.id, status="in_progress")
        note = "" if assignee != by else " It's assigned to you; work on it now or in a later run."
        return f"Created {t.ref} for {assignee}.{note} You'll be woken when it's finished."

    def update_task(
        self,
        by: str,
        task_id: int,
        assignee: str | None = None,
        description: str | None = None,
        checks: list[str] | None = None,
        reviewer: str | None = None,
        cancel: bool = False,
    ) -> str:
        t = self.get(task_id)
        manager = self.owner_of_parent(t)
        if by not in (HUMAN, t.creator, manager):
            raise OfficeError(f"only {t.creator} (who created {t.ref}) can change it")
        if t.status in ("done", "cancelled"):
            raise OfficeError(f"{t.ref} is already {t.status}")
        if cancel:
            self.store.update_task(t.id, status="cancelled", blocked_on=None)
            self.store.event("task_cancelled", by, t.id)
            self._cancel_open_children(t.id, by)
            return f"Cancelled {t.ref}."
        fields: dict = {}
        if description is not None:
            fields["description"] = description
        if checks is not None:
            fields["checks"] = [c.strip() for c in checks if c.strip()]
        if reviewer is not None:
            self._check_agent(reviewer, allow_human=True)
            fields["reviewer"] = reviewer
        new_assignee = t.assignee
        if assignee is not None:
            self._check_agent(assignee)
            if not self.can_assign(by, assignee):
                raise OfficeError(f"{assignee} doesn't report to you")
            new_assignee = assignee
            fields["assignee"] = assignee
        if not fields:
            raise OfficeError("nothing to change")
        reopen = t.status in ("failed", "blocked") or assignee is not None
        if reopen:
            fields.update(status="todo", attempts=0, blocked_on=None)
        self.store.update_task(t.id, **fields)
        self.store.event("task_updated", by, t.id, **{k: v for k, v in fields.items() if k != "status"})
        if reopen or t.is_open:
            self.store.wake(new_assignee, t.id, "updated", by=by, changes=sorted(fields))
        return f"Updated {t.ref}" + (f" and handed it to {new_assignee}." if reopen else ".")

    def _cancel_open_children(self, task_id: int, by: str) -> None:
        for c in self.store.children(task_id):
            if c.is_open:
                self.store.update_task(c.id, status="cancelled", blocked_on=None)
                self.store.event("task_cancelled", by, c.id, cascade=True)
                self._cancel_open_children(c.id, by)

    # -- doing the work ---------------------------------------------------------

    def run_checks(self, task: Task) -> list[CheckResult]:
        results = []
        trusted = task.creator == HUMAN
        for cmd in task.checks:
            if not trusted and not self.approve("check", cmd):
                results.append(
                    CheckResult(cmd, None, "not run: shell commands are not approved (company.shell policy)")
                )
                continue
            try:
                p = subprocess.run(
                    cmd,
                    shell=True,
                    cwd=self.company.workspace,
                    capture_output=True,
                    text=True,
                    timeout=self.company.check_timeout,
                    env=child_env(),
                    stdin=subprocess.DEVNULL,
                )
                results.append(CheckResult(cmd, p.returncode, tail((p.stdout or "") + (p.stderr or ""))))
            except subprocess.TimeoutExpired:
                results.append(CheckResult(cmd, None, f"timed out after {self.company.check_timeout}s"))
        return results

    def submit(self, by: str, task_id: int, summary: str) -> str:
        t = self.get(task_id)
        if t.assignee != by:
            raise OfficeError(f"{t.ref} is assigned to {t.assignee}, not you")
        if t.status not in ("todo", "in_progress"):
            raise OfficeError(f"{t.ref} is {t.status}; it can't be submitted now")
        open_children = [c for c in self.store.children(t.id) if c.is_open]
        if open_children:
            refs = ", ".join(f"{c.ref} ({c.assignee}, {c.status})" for c in open_children)
            raise OfficeError(f"{t.ref} still has open subtasks: {refs}. Wait for them or cancel them first.")

        results = self.run_checks(t)
        passed = all(r.passed for r in results)
        self.store.event(
            "checks_run",
            by,
            t.id,
            passed=passed,
            results=[{"command": r.command, "exit": r.exit_code} for r in results],
        )
        if not passed:
            attempts = t.attempts + 1
            self.store.update_task(t.id, attempts=attempts, status="in_progress")
            report = self._format_checks(results)
            if attempts >= self.company.max_attempts:
                self._escalate(t, by, f"checks failed {attempts} times", report)
                return (
                    f"Checks failed again (attempt {attempts}/{self.company.max_attempts}). {t.ref} has been "
                    f"escalated; stop working on it and end your turn.\n\n{report}"
                )
            return (
                f"Checks failed (attempt {attempts}/{self.company.max_attempts}); {t.ref} is not done. "
                f"Fix the problem and submit again.\n\n{report}"
            )

        self.store.update_task(t.id, summary=summary)
        if t.reviewer:
            self.store.update_task(t.id, status="in_review")
            self.store.event("review_requested", by, t.id, reviewer=t.reviewer)
            if t.reviewer != HUMAN:
                self.store.wake(t.reviewer, t.id, "review_requested", by=by)
            checks_note = f"All {len(results)} checks passed. " if results else ""
            who = "a human" if t.reviewer == HUMAN else t.reviewer
            return f"{checks_note}{t.ref} is now waiting for review by {who}. End your turn."
        self._close(t.id, "done", by)
        n = len(results)
        return f"All {n} check{'s' if n != 1 else ''} passed. {t.ref} is done. End your turn."

    def review(self, by: str, task_id: int, approve: bool, notes: str = "") -> str:
        t = self.get(task_id)
        if t.reviewer != by:
            raise OfficeError(f"you are not the reviewer of {t.ref}")
        if t.status != "in_review":
            raise OfficeError(f"{t.ref} is {t.status}, not waiting for review")
        self.store.event("reviewed", by, t.id, approved=approve, notes=notes)
        if approve:
            self._close(t.id, "done", by)
            return f"Approved {t.ref}; it's done."
        attempts = t.attempts + 1
        self.store.update_task(t.id, status="in_progress", attempts=attempts)
        if attempts >= self.company.max_attempts:
            self._escalate(t, by, f"rejected in review {attempts} times", notes)
            return f"Rejected {t.ref}. It has now failed {attempts} times and was escalated."
        self.store.wake(t.assignee, t.id, "changes_requested", by=by, notes=notes)
        return f"Sent {t.ref} back to {t.assignee} with your notes."

    def comment(self, by: str, task_id: int, text: str, notify: list[str] | None = None) -> str:
        t = self.get(task_id)
        notify = [n for n in (notify or []) if n != by]
        for n in notify:
            self._check_agent(n)
        self.store.event("comment", by, t.id, text=text, notify=notify)
        for n in notify:
            self.store.wake(n, t.id, "comment", by=by, text=text)
        return f"Commented on {t.ref}" + (f" and notified {', '.join(notify)}." if notify else ".")

    def ask_human(self, by: str, task_id: int, question: str) -> str:
        t = self.get(task_id)
        if by not in (t.assignee, t.reviewer):
            raise OfficeError("you can only ask about tasks you're working on or reviewing")
        self.store.update_task(t.id, status="blocked", blocked_on=f"{HUMAN}: {question}")
        self.store.event("asked_human", by, t.id, question=question, resume=t.status)
        return f"{t.ref} is blocked until a human replies. End your turn; you'll be woken with the answer."

    def reply(self, task_id: int, text: str) -> str:
        t = self.get(task_id)
        if t.status != "blocked":
            raise OfficeError(f"{t.ref} isn't waiting on anyone ({t.status})")
        asked = self.store.events(task_id=t.id, limit=1, kinds=("asked_human",))
        if (
            asked
            and (t.blocked_on or "").startswith(HUMAN)
            and "question" in asked[-1]["data"]
            and t.blocked_on == f"{HUMAN}: {asked[-1]['data']['question']}"
        ):
            who = asked[-1]["agent"] or t.assignee
            status = asked[-1]["data"].get("resume", "in_progress")
            if status not in ("todo", "in_progress", "in_review"):
                status = "in_progress"
            self.store.update_task(t.id, status=status, blocked_on=None)
        else:  # escalated to the human (repeated failures, stalls): treat the reply as fresh guidance
            who = t.assignee
            self.store.update_task(t.id, status="in_progress", blocked_on=None, attempts=0)
        self.store.event("human_replied", HUMAN, t.id, text=text)
        self.store.wake(who, t.id, "human_replied", text=text)
        return f"Replied to {t.ref}; {who} will pick it up on the next run."

    def note(self, by: str, text: str) -> str:
        self.store.add_note(by, text.strip())
        return "Saved. You'll see this note in future briefings."

    # -- lifecycle --------------------------------------------------------------

    def _close(self, task_id: int, status: str, by: str) -> None:
        t = self.store.update_task(task_id, status=status, blocked_on=None)
        self.store.event(f"task_{status}", by, t.id, title=t.title)
        if t.parent_id is not None:
            owner = self.get(t.parent_id).assignee
            self.store.wake(owner, t.parent_id, f"subtask_{status}", subtask=t.id, by=by, summary=t.summary or "")
        elif t.creator == HUMAN:
            self.store.event(f"goal_{status}", by, t.id, title=t.title)

    def _escalate(self, t: Task, by: str, why: str, detail: str) -> None:
        if t.parent_id is not None:
            self.store.update_task(t.id, status="failed", blocked_on=None)
            self.store.event("escalated", by, t.id, why=why)
            owner = self.get(t.parent_id).assignee
            self.store.wake(owner, t.parent_id, "subtask_failed", subtask=t.id, why=why, detail=tail(detail, 1500))
        else:
            self.store.update_task(t.id, status="blocked", blocked_on=f"{HUMAN}: {why}")
            self.store.event("escalated", by, t.id, why=why, to=HUMAN)

    def resume_paused(self) -> list[Task]:
        """Reopen tasks paused for budget or API errors (e.g. after raising a budget)."""
        resumed = []
        for t in self.store.tasks(("blocked",)):
            why = t.blocked_on or ""
            if why.startswith(("budget:", "model error:")):
                self.store.update_task(t.id, status="in_progress", blocked_on=None)
                self.store.event("resumed", None, t.id, why=why)
                self.store.wake(t.assignee, t.id, "resumed", why=why)
                resumed.append(t)
        return resumed

    def block(self, task_id: int, why: str, by: str) -> None:
        t = self.get(task_id)
        if t.is_open:
            self.store.update_task(t.id, status="blocked", blocked_on=why)
            self.store.event("blocked", by, t.id, why=why)

    # -- human review -------------------------------------------------------------

    def human_review(self, task_id: int, approve: bool, notes: str = "") -> str:
        return self.review(HUMAN, task_id, approve, notes)

    def inbox(self) -> list[Task]:
        return [
            t
            for t in self.store.tasks(("blocked", "in_review"))
            if (t.status == "in_review" and t.reviewer == HUMAN)
            or (t.status == "blocked" and (t.blocked_on or "").startswith(HUMAN))
        ]

    @staticmethod
    def _format_checks(results: list[CheckResult]) -> str:
        parts = []
        for r in results:
            mark = "PASS" if r.passed else "FAIL"
            code = "timeout/not run" if r.exit_code is None else f"exit {r.exit_code}"
            parts.append(f"[{mark}] $ {r.command}  ({code})")
            if not r.passed and r.output:
                parts.append(r.output)
        return "\n".join(parts) if parts else "(no checks)"


CLOSED_STATES = CLOSED
