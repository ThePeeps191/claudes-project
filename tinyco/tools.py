"""The tools every agent gets: org tools (delegate, submit, review, ...) and workspace tools.

The tool list is identical for every agent and every run, which keeps the
request prefix stable so prompt caching works across the whole company.
"""

from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path
from typing import Any

from .office import Office, OfficeError, child_env, tail
from .store import Task

MAX_RESULT = 30_000
MAX_READ_LINES = 2000
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache", "dist", "build"}


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


_STR = {"type": "string"}
_INT = {"type": "integer"}
_STRS = {"type": "array", "items": {"type": "string"}}

TOOLS: list[dict] = [
    {
        "name": "create_task",
        "description": (
            "Delegate part of a task you own to yourself or someone who reports to you. Every task needs a "
            "definition of done: shell check commands that exit 0 only when the work is right (run from the "
            "workspace root), and/or a reviewer. The assignee is woken automatically and you are woken when "
            "the subtask is done or fails."
        ),
        "input_schema": _obj(
            {
                "parent_id": {**_INT, "description": "The task you own that this is part of."},
                "title": {**_STR, "description": "Short imperative title."},
                "description": {
                    **_STR,
                    "description": "What to do, relevant context, constraints, and where files should go.",
                },
                "assignee": {**_STR, "description": "Agent name."},
                "checks": {
                    **_STRS,
                    "description": "Shell commands that must all exit 0, e.g. ['python -m pytest -q tests/test_parser.py'].",
                },
                "reviewer": {
                    **_STR,
                    "description": "Optional agent (or 'human') who must approve the work after checks pass.",
                },
            },
            ["parent_id", "title", "description", "assignee"],
        ),
    },
    {
        "name": "submit",
        "description": (
            "Submit a task you're assigned as finished. This runs the task's checks; it is only done if they all "
            "pass (and the reviewer approves, if there is one). If checks fail you get their output and can fix "
            "the work and submit again."
        ),
        "input_schema": _obj(
            {
                "task_id": _INT,
                "summary": {**_STR, "description": "What you did and where, for whoever reads this next."},
            },
            ["task_id", "summary"],
        ),
    },
    {
        "name": "review",
        "description": "Approve or reject work you were asked to review. Rejecting sends it back with your notes.",
        "input_schema": _obj(
            {
                "task_id": _INT,
                "approve": {"type": "boolean"},
                "notes": {**_STR, "description": "What's wrong and what to change (required when rejecting)."},
            },
            ["task_id", "approve", "notes"],
        ),
    },
    {
        "name": "update_task",
        "description": (
            "Change a task you created: reassign it, rewrite its description or checks, or cancel it. Reassigning "
            "or editing a failed/blocked task reopens it."
        ),
        "input_schema": _obj(
            {
                "task_id": _INT,
                "assignee": _STR,
                "description": _STR,
                "checks": _STRS,
                "reviewer": _STR,
                "cancel": {"type": "boolean"},
            },
            ["task_id"],
        ),
    },
    {
        "name": "comment",
        "description": "Leave a comment on a task. Agents listed in notify are woken to read it.",
        "input_schema": _obj({"task_id": _INT, "text": _STR, "notify": _STRS}, ["task_id", "text"]),
    },
    {
        "name": "ask_human",
        "description": (
            "Ask the human owner a question that only they can answer (a decision, a credential, a preference). "
            "The task is blocked until they reply. Use sparingly."
        ),
        "input_schema": _obj({"task_id": _INT, "question": _STR}, ["task_id", "question"]),
    },
    {
        "name": "view_task",
        "description": "Show a task's details, subtasks, and recent activity.",
        "input_schema": _obj({"task_id": _INT}, ["task_id"]),
    },
    {
        "name": "note",
        "description": (
            "Save a short note to your own memory. Each wake-up starts fresh; your recent notes are included in "
            "every briefing. Good for decisions, conventions, and where things live."
        ),
        "input_schema": _obj({"text": _STR}, ["text"]),
    },
    {
        "name": "list_files",
        "description": "List files in the workspace (recursively), optionally under a directory or matching a glob.",
        "input_schema": _obj(
            {
                "path": {**_STR, "description": "Directory relative to the workspace root."},
                "pattern": {**_STR, "description": "Glob such as '*.py'."},
            },
            [],
        ),
    },
    {
        "name": "read_file",
        "description": "Read a text file from the workspace. Returns numbered lines.",
        "input_schema": _obj(
            {"path": _STR, "offset": {**_INT, "description": "1-based first line."}, "limit": _INT},
            ["path"],
        ),
    },
    {
        "name": "write_file",
        "description": "Create or overwrite a file in the workspace. Parent directories are created.",
        "input_schema": _obj({"path": _STR, "content": _STR}, ["path", "content"]),
    },
    {
        "name": "edit_file",
        "description": "Replace an exact string in a workspace file. old must appear exactly once unless replace_all is true.",
        "input_schema": _obj(
            {"path": _STR, "old": _STR, "new": _STR, "replace_all": {"type": "boolean"}},
            ["path", "old", "new"],
        ),
    },
    {
        "name": "run",
        "description": "Run a shell command from the workspace root and return its exit code and output.",
        "input_schema": _obj(
            {"command": _STR, "timeout": {**_INT, "description": "Seconds (default 120, max 600)."}},
            ["command"],
        ),
    },
]

TOOL_NAMES = {t["name"] for t in TOOLS}

# Models that support the dynamic-filtering web tool versions; others get the basic ones.
_NEW_WEB = (
    "claude-opus-5-5",
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-5-5",
    "claude-sonnet-5",
    "claude-sonnet-4-6",
    "claude-fable-5-1",
    "claude-fable-5",
)


def web_tools(model: str) -> list[dict]:
    if model in _NEW_WEB:
        return [
            {"type": "web_search_20260209", "name": "web_search"},
            {"type": "web_fetch_20260209", "name": "web_fetch"},
        ]
    return [{"type": "web_search_20250305", "name": "web_search"}, {"type": "web_fetch_20250910", "name": "web_fetch"}]


def tools_for(agent) -> list[dict]:
    """The tool list for an agent: the same for every call it makes (prompt-cache friendly)."""
    return TOOLS + web_tools(agent.model) if agent.web else TOOLS


_SCHEMAS = {t["name"]: t["input_schema"] for t in TOOLS}


def validate(schema: dict, value: Any, path: str = "input") -> str | None:
    """Minimal JSON-schema check for the shapes used above. Returns an error or None."""
    t = schema.get("type")
    if t == "object":
        if not isinstance(value, dict):
            return f"{path} must be an object"
        for k in schema.get("required", []):
            if k not in value:
                return f"{path} is missing required field {k!r}"
        props = schema.get("properties", {})
        for k, v in value.items():
            if k not in props:
                if schema.get("additionalProperties") is False:
                    return f"{path} has unknown field {k!r}"
                continue
            err = validate(props[k], v, f"{path}.{k}")
            if err:
                return err
        return None
    if t == "string":
        return None if isinstance(value, str) else f"{path} must be a string"
    if t == "integer":
        ok = isinstance(value, int) and not isinstance(value, bool)
        if not ok and isinstance(value, str) and value.lstrip("Tt").isdigit():
            return None  # tolerate "T3" / "3"; coerced in _int()
        return None if ok else f"{path} must be an integer"
    if t == "boolean":
        return None if isinstance(value, bool) else f"{path} must be true or false"
    if t == "array":
        if not isinstance(value, list):
            return f"{path} must be an array"
        for i, item in enumerate(value):
            err = validate(schema.get("items", {}), item, f"{path}[{i}]")
            if err:
                return err
        return None
    return None


def _int(v: Any) -> int:
    if isinstance(v, str):
        v = v.lstrip("Tt")
    return int(v)


def clip(text: str, limit: int = MAX_RESULT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[... output truncated: {len(text) - limit} more characters ...]"


class Toolbox:
    """Executes tool calls for one agent during one run."""

    def __init__(self, office: Office, agent: str, task: Task):
        self.office = office
        self.agent = agent
        self.task = task
        self.ws = office.company.workspace

    def call(self, name: str, args: Any) -> tuple[str, bool]:
        """Run a tool. Returns (result text, is_error)."""
        if name not in TOOL_NAMES:
            return f"unknown tool {name!r}", True
        err = validate(_SCHEMAS[name], args)
        if err:
            return f"invalid input: {err}. Re-issue the call with valid arguments.", True
        try:
            out = getattr(self, f"t_{name}")(**args)
            return clip(out), False
        except OfficeError as e:
            return str(e), True
        except (OSError, ValueError, UnicodeDecodeError) as e:
            return f"{type(e).__name__}: {e}", True

    # -- org tools ----------------------------------------------------------------

    def t_create_task(self, parent_id, title, description, assignee, checks=None, reviewer=None) -> str:
        return self.office.create_task(self.agent, _int(parent_id), title, assignee, description, checks, reviewer)

    def t_submit(self, task_id, summary) -> str:
        return self.office.submit(self.agent, _int(task_id), summary)

    def t_review(self, task_id, approve, notes) -> str:
        if not approve and not notes.strip():
            raise OfficeError("explain what needs to change when rejecting")
        return self.office.review(self.agent, _int(task_id), approve, notes)

    def t_update_task(self, task_id, assignee=None, description=None, checks=None, reviewer=None, cancel=False) -> str:
        return self.office.update_task(self.agent, _int(task_id), assignee, description, checks, reviewer, cancel)

    def t_comment(self, task_id, text, notify=None) -> str:
        return self.office.comment(self.agent, _int(task_id), text, notify)

    def t_ask_human(self, task_id, question) -> str:
        return self.office.ask_human(self.agent, _int(task_id), question)

    def t_view_task(self, task_id) -> str:
        from .briefing import describe_task

        return describe_task(self.office, self.office.get(_int(task_id)))

    def t_note(self, text) -> str:
        return self.office.note(self.agent, text)

    # -- workspace tools ----------------------------------------------------------

    def _path(self, rel: str) -> Path:
        p = (self.ws / rel).resolve()
        if p != self.ws and self.ws not in p.parents:
            raise ValueError(f"{rel!r} is outside the workspace")
        return p

    def t_list_files(self, path: str = ".", pattern: str | None = None) -> str:
        base = self._path(path)
        if not base.exists():
            return f"{path} does not exist"
        files = []
        for p in sorted(base.rglob("*")):
            rel = p.relative_to(self.ws)
            if any(part in SKIP_DIRS for part in rel.parts) or not p.is_file():
                continue
            if pattern and not fnmatch.fnmatch(p.name, pattern) and not fnmatch.fnmatch(str(rel), pattern):
                continue
            files.append(f"{rel}  ({p.stat().st_size} bytes)")
            if len(files) >= 500:
                files.append("[... more files omitted ...]")
                break
        return "\n".join(files) if files else "(no files)"

    def t_read_file(self, path: str, offset: int = 1, limit: int = MAX_READ_LINES) -> str:
        p = self._path(path)
        if not p.is_file():
            return f"{path} does not exist"
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(1, int(offset))
        chunk = lines[start - 1 : start - 1 + int(limit)]
        out = "\n".join(f"{i:>5}  {line}" for i, line in enumerate(chunk, start))
        if start - 1 + len(chunk) < len(lines):
            out += f"\n[... {len(lines) - (start - 1 + len(chunk))} more lines; use offset to read on ...]"
        return out or "(empty file)"

    def t_write_file(self, path: str, content: str) -> str:
        p = self._path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        existed = p.exists()
        p.write_text(content, encoding="utf-8")
        self.office.store.event(
            "file_written",
            self.agent,
            self.task.id,
            path=str(p.relative_to(self.ws)),
            bytes=len(content.encode()),
            created=not existed,
        )
        return f"{'Updated' if existed else 'Created'} {path} ({len(content.splitlines())} lines)."

    def t_edit_file(self, path: str, old: str, new: str, replace_all: bool = False) -> str:
        p = self._path(path)
        if not p.is_file():
            return f"{path} does not exist"
        text = p.read_text(encoding="utf-8")
        n = text.count(old)
        if n == 0:
            raise ValueError("old string not found; read the file and copy the exact text")
        if n > 1 and not replace_all:
            raise ValueError(f"old string appears {n} times; include more context or set replace_all")
        p.write_text(text.replace(old, new) if replace_all else text.replace(old, new, 1), encoding="utf-8")
        self.office.store.event(
            "file_written",
            self.agent,
            self.task.id,
            path=str(p.relative_to(self.ws)),
            bytes=len(new.encode()),
            created=False,
        )
        return f"Edited {path} ({n if replace_all else 1} replacement{'s' if replace_all and n > 1 else ''})."

    def t_run(self, command: str, timeout: int = 120) -> str:
        if not self.office.approve("shell", command):
            return (
                "Not run: shell commands are not approved. The company's shell policy is "
                f"{self.office.company.shell!r}; work without running commands or ask the human."
            )
        timeout = max(1, min(int(timeout), 600))
        self.office.store.event("command", self.agent, self.task.id, command=command)
        try:
            p = subprocess.run(
                command,
                shell=True,
                cwd=self.ws,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=child_env(),
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as e:
            out = (e.stdout or "") if isinstance(e.stdout, str) else ""
            return f"timed out after {timeout}s\n{tail(out)}"
        self.office.store.event("command_done", self.agent, self.task.id, command=command, exit=p.returncode)
        output = tail((p.stdout or "") + (p.stderr or ""), 12_000)
        return f"exit {p.returncode}\n{output}" if output else f"exit {p.returncode} (no output)"
