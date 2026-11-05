"""What an agent sees: a stable system prompt plus a compact briefing per wake.

Each wake starts a fresh conversation. Instead of re-reading the whole board,
the agent gets exactly what it needs: why it was woken, the task and the chain
of goals above it, subtask status, recent activity, and its own notes.
"""

from __future__ import annotations

import time

from .config import AgentSpec, Company
from .office import COMPANY_NOTES, HUMAN, Office
from .store import Task, Wake

SYSTEM = """\
You are {name}, the {title} at {company}, a company run by AI agents.
{mission_line}
Org chart:
{org}

{position}

{instructions}

How work happens here:
- Work is organized as tasks (T1, T2, ...). You're woken when something needs your attention, and each wake-up starts fresh, so the briefing and your notes are your memory. Use `note` to save anything you'll want next time.
- A task is finished only when its checks pass and its reviewer (if any) approves. Call `submit` to run the checks. If they fail you'll see the output; fix the work and submit again. Repeated failures get escalated to whoever assigned the task.
- When you delegate with `create_task`, write a clear description and give it checks that exit 0 only when the work is right (usually tests). Vague tasks get vague work. After delegating, end your turn: you'll be woken when subtasks finish, and nobody is billed while you wait.
- Do the work in front of you and then end your turn. Don't wait or poll.
- When several tool calls don't depend on each other (reading a few files, writing several files), make them together in one turn rather than one per turn.
- All files live in a shared workspace; paths are relative to its root. If something only a human can decide is blocking you, use `ask_human`.
"""

REASON_TEXT = {
    "assigned": "You've been assigned this task.",
    "updated": "This task was changed by {by}.",
    "subtask_done": "Subtask T{subtask} is done.",
    "subtask_failed": "Subtask T{subtask} failed ({why}) and was escalated to you. Decide what to do: reassign it, rewrite it, cancel it, or do it yourself.",
    "subtask_cancelled": "Subtask T{subtask} was cancelled.",
    "review_requested": "{by} submitted this task and its checks passed. You're the reviewer: inspect the work and call `review`.",
    "changes_requested": "{by} reviewed this and requested changes.",
    "human_replied": "The human replied to your question.",
    "comment": "{by} commented and asked you to read it.",
    "resumed": "Work on this task was paused ({why}) and has now resumed.",
    "comment_seen": "{by} has read your comment. Check the recent activity for their answer, then carry on.",
    "reopened": "The human reopened this task with a clean slate.",
    "nudge": "Your last run ended without finishing, delegating, or blocking this task. Continue it now, and submit when it's done.",
}


def system_prompt(company: Company, agent: AgentSpec) -> str:
    manager = agent.reports_to
    reports = company.reports(agent.name)
    pos = []
    pos.append(f"You report to {manager}." if manager else "You lead the company and report to the human owner.")
    if reports:
        pos.append(f"Your direct reports: {', '.join(reports)}.")
    else:
        pos.append("You have no reports; you do hands-on work yourself.")
    return SYSTEM.format(
        name=agent.name,
        title=agent.title,
        company=company.name,
        mission_line=f"Mission: {company.mission}\n" if company.mission else "",
        org=company.org_chart(),
        position=" ".join(pos),
        instructions=agent.instructions or "",
    ).replace("\n\n\n", "\n\n")


def _ago(ts: float) -> str:
    s = int(time.time() - ts)
    if s < 90:
        return f"{s}s ago"
    if s < 5400:
        return f"{s // 60}m ago"
    return f"{s // 3600}h ago"


def _status_line(t: Task) -> str:
    extra = ""
    if t.status == "blocked" and t.blocked_on:
        extra = f" — waiting on {t.blocked_on}"
    elif t.status == "in_review":
        extra = f" — review by {t.reviewer}"
    return f"{t.ref} [{t.status}] {t.title} (assignee: {t.assignee}){extra}"


_ACTIVITY_KINDS = (
    "comment",
    "checks_run",
    "reviewed",
    "asked_human",
    "human_replied",
    "escalated",
    "task_updated",
    "blocked",
    "task_done",
    "task_failed",
    "task_cancelled",
)


def _activity(office: Office, t: Task, limit: int = 12) -> list[str]:
    out = []
    for e in office.store.events(task_id=t.id, limit=limit, kinds=_ACTIVITY_KINDS):
        d, who = e["data"], e["agent"] or "?"
        k = e["kind"]
        if k == "comment":
            line = f"{who}: {d.get('text', '')}"
        elif k == "checks_run":
            line = f"{who} submitted; checks {'passed' if d.get('passed') else 'FAILED'}"
        elif k == "reviewed":
            line = f"{who} {'approved' if d.get('approved') else 'requested changes'}" + (
                f": {d['notes']}" if d.get("notes") else ""
            )
        elif k == "asked_human":
            line = f"{who} asked the human: {d.get('question', '')}"
        elif k == "human_replied":
            line = f"human replied: {d.get('text', '')}"
        elif k == "escalated":
            line = f"escalated ({d.get('why', '')})"
        elif k == "task_updated":
            line = f"{who} changed {', '.join(sorted(d))}"
        else:
            line = k.replace("_", " ") + (f" ({d['why']})" if d.get("why") else "")
        out.append(f"- {_ago(e['ts'])} · {line}")
    return out


SHOW_OPEN = 15  # open subtasks listed in full
SHOW_CLOSED = 8  # most recently closed subtasks listed (with summaries)


def _subtasks(office: Office, t: Task) -> list[str]:
    """Subtask status for a brief, bounded in size however many subtasks there are."""
    counts = office.store.child_counts(t.id)
    total = sum(counts.values())
    if not total:
        return []
    tally = ", ".join(f"{n} {status.replace('_', ' ')}" for status, n in sorted(counts.items()))
    out = ["", f"Subtasks ({total}: {tally}):"]
    open_ = office.store.children(t.id, status=("todo", "in_progress", "in_review", "blocked"))
    out += [f"- {_status_line(k)}" for k in open_[:SHOW_OPEN]]
    if len(open_) > SHOW_OPEN:
        out.append(f"- … and {len(open_) - SHOW_OPEN} more open (use view_task on any of them)")
    closed = office.store.recent_children(t.id, ("done", "failed", "cancelled"), SHOW_CLOSED)
    for k in closed:
        line = f"- {_status_line(k)}"
        if k.summary and k.status == "done":
            line += f"\n  summary: {k.summary[:300]}"
        out.append(line)
    n_closed = sum(n for s, n in counts.items() if s in ("done", "failed", "cancelled"))
    if n_closed > len(closed):
        out.append(f"- … and {n_closed - len(closed)} earlier closed subtasks")
    return out


def _last_failure(office: Office, t: Task) -> list[str]:
    """If the latest submission's checks failed, show what failed so nobody has to re-run them to find out."""
    if t.status not in ("todo", "in_progress", "blocked"):
        return []
    last = office.store.events(task_id=t.id, limit=1, kinds=("checks_run",))
    if not last or last[-1]["data"].get("passed"):
        return []
    out = ["", "The last submission's checks failed:"]
    for r in last[-1]["data"].get("results", []):
        if r.get("exit") != 0:
            out.append(f"$ {r.get('command')}  (exit {r.get('exit')})")
            if r.get("output"):
                out.append(r["output"])
    return out


SKIP_TREE = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".tox",
    ".idea",
    ".vscode",
}


def workspace_tree(root, limit: int = 80) -> list[str]:
    """A compact, bounded listing of the workspace (directories first, hidden noise skipped)."""
    import os

    lines: list[str] = []
    more = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_TREE)
        rel = os.path.relpath(dirpath, root)
        prefix = "" if rel == "." else rel + "/"
        for f in sorted(filenames):
            if len(lines) < limit:
                lines.append(prefix + f)
            else:
                more += 1
        if more > 5000:  # don't walk huge trees on every wake
            lines.append("… and many more files (use list_files)")
            return lines
    if more:
        lines.append(f"… and {more} more files (use list_files)")
    return lines


def describe_task(office: Office, t: Task) -> str:
    lines = [f"## {t.ref}: {t.title}", f"Status: {t.status} · assignee: {t.assignee} · created by: {t.creator}"]
    if t.reviewer:
        lines.append(f"Reviewer: {t.reviewer}")
    if t.attempts:
        lines.append(f"Failed attempts so far: {t.attempts} of {office.company.max_attempts}")
    chain = office.store.ancestry(t.id)[:-1]
    if chain:
        lines.append("Part of: " + " → ".join(f"{c.ref} {c.title}" for c in chain))
    if t.description:
        lines += ["", t.description.strip()]
    lines += ["", "Checks (all must exit 0, run from the workspace root):"]
    lines += [f"- `{c}`" for c in t.checks] if t.checks else ["- (none; the reviewer decides)"]
    if t.summary:
        lines += ["", f"Latest submission summary: {t.summary}"]
    lines += _last_failure(office, t)
    lines += _subtasks(office, t)
    act = _activity(office, t)
    if act:
        lines += ["", "Recent activity:"] + act
    return "\n".join(lines)


def _reason(r: dict) -> str:
    template = REASON_TEXT.get(r.get("reason", ""), "Something changed on this task: {reason}.")
    fields = {"by": "someone", "subtask": "?", "why": "", **r}
    text = template.format(**fields)
    if r.get("reason") == "changes_requested" and r.get("notes"):
        text += f"\n  Reviewer notes: {r['notes']}"
    if r.get("reason") in ("human_replied", "reopened") and r.get("text"):
        text += f"\n  Reply: {r['text']}"
    if r.get("reason") == "comment" and r.get("text"):
        text += f"\n  Comment: {r['text']}"
    if r.get("reason") == "subtask_done" and r.get("summary"):
        text += f"\n  Their summary: {r['summary']}"
    if r.get("reason") == "subtask_failed" and r.get("detail"):
        text += f"\n  Last output:\n{r['detail']}"
    return text


def briefing(office: Office, agent: AgentSpec, wake: Wake) -> str:
    t = office.get(wake.task_id)
    parts = ["# Why you're awake"]
    parts += [f"- {_reason(r)}" for r in wake.reasons]
    parts += ["", describe_task(office, t)]

    if t.assignee == agent.name or t.reviewer == agent.name:
        files = workspace_tree(office.company.workspace)
        parts += ["", "Workspace files:" if files else "Workspace files: (empty)"]
        parts += [f"- {f}" for f in files]

    other = office.store.open_tasks_for(agent.name, exclude=t.id, limit=10)
    if other:
        parts += ["", "Your other open tasks (you'll be woken for these separately):"]
        parts += [f"- {_status_line(x)}" for x in other]

    shared = office.store.notes(COMPANY_NOTES, limit=15)
    if shared:
        parts += ["", "Company notes (shared by the team):"] + [f"- {n}" for n in shared]
    notes = office.store.notes(agent.name)
    if notes:
        parts += ["", "Your notes:"] + [f"- {n}" for n in notes]

    spent = office.store.spent(agent=agent.name)
    budget = agent.budget_usd
    parts += ["", f"Your spend so far: ${spent:.2f}" + (f" of ${budget:.2f}" if budget else "") + "."]
    role = "assignee" if t.assignee == agent.name else ("reviewer" if t.reviewer == agent.name else "participant")
    if role == "assignee" and t.creator == HUMAN and t.parent_id is None:
        parts.append("This is a top-level goal from the human owner.")
    return "\n".join(parts)
