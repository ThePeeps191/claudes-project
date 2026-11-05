"""SQLite state: tasks, wakes, events, spend and agent notes.

Everything the company knows lives in one file (``.tinyco/state.db``) that you
can open with any SQLite browser.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY,
    parent_id   INTEGER REFERENCES tasks(id),
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    assignee    TEXT NOT NULL,
    creator     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'todo',
    checks      TEXT NOT NULL DEFAULT '[]',
    reviewer    TEXT,
    attempts    INTEGER NOT NULL DEFAULT 0,
    summary     TEXT,
    blocked_on  TEXT,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS wakes (
    id         INTEGER PRIMARY KEY,
    agent      TEXT NOT NULL,
    task_id    INTEGER NOT NULL REFERENCES tasks(id),
    reasons    TEXT NOT NULL DEFAULT '[]',
    status     TEXT NOT NULL DEFAULT 'pending',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY,
    ts      REAL NOT NULL,
    kind    TEXT NOT NULL,
    agent   TEXT,
    task_id INTEGER,
    data    TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS spend (
    id          INTEGER PRIMARY KEY,
    ts          REAL NOT NULL,
    agent       TEXT NOT NULL,
    task_id     INTEGER,
    model       TEXT NOT NULL,
    input       INTEGER NOT NULL,
    output      INTEGER NOT NULL,
    cache_read  INTEGER NOT NULL,
    cache_write INTEGER NOT NULL,
    usd         REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
    id    INTEGER PRIMARY KEY,
    ts    REAL NOT NULL,
    agent TEXT NOT NULL,
    text  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_id);
CREATE INDEX IF NOT EXISTS idx_wakes_status ON wakes(status);
CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id);
"""

OPEN = ("todo", "in_progress", "in_review", "blocked")
CLOSED = ("done", "failed", "cancelled")


@dataclass
class Task:
    id: int
    parent_id: int | None
    title: str
    description: str
    assignee: str
    creator: str
    status: str
    checks: list[str]
    reviewer: str | None
    attempts: int
    summary: str | None
    blocked_on: str | None
    created_at: float
    updated_at: float

    @property
    def ref(self) -> str:
        return f"T{self.id}"

    @property
    def is_open(self) -> bool:
        return self.status in OPEN


@dataclass
class Wake:
    id: int
    agent: str
    task_id: int
    reasons: list[dict]
    status: str
    created_at: float


def normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


class Store:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL") if str(path) != ":memory:" else None
        self.db.executescript(SCHEMA)
        self.listeners: list = []

    # -- events ---------------------------------------------------------------

    def event(self, kind: str, agent: str | None = None, task_id: int | None = None, **data: Any) -> None:
        ts = time.time()
        self.db.execute(
            "INSERT INTO events (ts, kind, agent, task_id, data) VALUES (?, ?, ?, ?, ?)",
            (ts, kind, agent, task_id, json.dumps(data, default=str)),
        )
        for fn in self.listeners:
            fn({"ts": ts, "kind": kind, "agent": agent, "task_id": task_id, "data": data})

    def events(self, task_id: int | None = None, limit: int = 50, kinds: tuple[str, ...] | None = None) -> list[dict]:
        sql, args = "SELECT * FROM events", []
        where = []
        if task_id is not None:
            where.append("task_id = ?")
            args.append(task_id)
        if kinds:
            where.append(f"kind IN ({','.join('?' * len(kinds))})")
            args.extend(kinds)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        rows = self.db.execute(sql, args).fetchall()
        return [
            {
                "id": r["id"],
                "ts": r["ts"],
                "kind": r["kind"],
                "agent": r["agent"],
                "task_id": r["task_id"],
                "data": json.loads(r["data"]),
            }
            for r in reversed(rows)
        ]

    # -- tasks ----------------------------------------------------------------

    def _task(self, row: sqlite3.Row | None) -> Task | None:
        if row is None:
            return None
        d = dict(row)
        d["checks"] = json.loads(d["checks"])
        return Task(**d)

    def create_task(
        self,
        title: str,
        assignee: str,
        creator: str,
        description: str = "",
        parent_id: int | None = None,
        checks: list[str] | None = None,
        reviewer: str | None = None,
    ) -> Task:
        now = time.time()
        cur = self.db.execute(
            "INSERT INTO tasks (parent_id, title, description, assignee, creator, checks, reviewer, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (parent_id, title, description, assignee, creator, json.dumps(checks or []), reviewer, now, now),
        )
        return self.task(cur.lastrowid)  # type: ignore[return-value]

    def task(self, task_id: int) -> Task | None:
        return self._task(self.db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone())

    def tasks(self, status: tuple[str, ...] | None = None) -> list[Task]:
        if status:
            rows = self.db.execute(
                f"SELECT * FROM tasks WHERE status IN ({','.join('?' * len(status))}) ORDER BY id", status
            ).fetchall()
        else:
            rows = self.db.execute("SELECT * FROM tasks ORDER BY id").fetchall()
        return [self._task(r) for r in rows]  # type: ignore[misc]

    def children(self, task_id: int) -> list[Task]:
        rows = self.db.execute("SELECT * FROM tasks WHERE parent_id = ? ORDER BY id", (task_id,)).fetchall()
        return [self._task(r) for r in rows]  # type: ignore[misc]

    def ancestry(self, task_id: int) -> list[Task]:
        """Root-first chain of tasks ending at ``task_id``."""
        chain: list[Task] = []
        cur = self.task(task_id)
        while cur is not None:
            chain.append(cur)
            cur = self.task(cur.parent_id) if cur.parent_id is not None else None
        return list(reversed(chain))

    def find_open_sibling(self, parent_id: int | None, title: str) -> Task | None:
        key = normalize_title(title)
        siblings = self.children(parent_id) if parent_id is not None else self.roots()
        for t in siblings:
            if t.is_open and normalize_title(t.title) == key:
                return t
        return None

    def roots(self) -> list[Task]:
        rows = self.db.execute("SELECT * FROM tasks WHERE parent_id IS NULL ORDER BY id").fetchall()
        return [self._task(r) for r in rows]  # type: ignore[misc]

    def update_task(self, task_id: int, **fields: Any) -> Task:
        if "checks" in fields:
            fields["checks"] = json.dumps(fields["checks"])
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(f"UPDATE tasks SET {cols} WHERE id = ?", (*fields.values(), task_id))
        return self.task(task_id)  # type: ignore[return-value]

    # -- wakes ----------------------------------------------------------------

    def wake(self, agent: str, task_id: int, reason: str, **data: Any) -> None:
        """Queue a wake-up, merging into an existing pending wake for the same agent+task."""
        entry = {"reason": reason, **data}
        row = self.db.execute(
            "SELECT id, reasons FROM wakes WHERE agent = ? AND task_id = ? AND status = 'pending'",
            (agent, task_id),
        ).fetchone()
        if row is not None:
            reasons = json.loads(row["reasons"])
            reasons.append(entry)
            self.db.execute("UPDATE wakes SET reasons = ? WHERE id = ?", (json.dumps(reasons), row["id"]))
            return
        self.db.execute(
            "INSERT INTO wakes (agent, task_id, reasons, created_at) VALUES (?, ?, ?, ?)",
            (agent, task_id, json.dumps([entry]), time.time()),
        )

    def next_wake(self) -> Wake | None:
        row = self.db.execute("SELECT * FROM wakes WHERE status = 'pending' ORDER BY id LIMIT 1").fetchone()
        if row is None:
            return None
        self.db.execute("UPDATE wakes SET status = 'running' WHERE id = ?", (row["id"],))
        d = dict(row)
        d["reasons"] = json.loads(d["reasons"])
        d["status"] = "running"
        return Wake(**d)

    def finish_wake(self, wake_id: int, status: str = "done") -> None:
        self.db.execute("UPDATE wakes SET status = ? WHERE id = ?", (status, wake_id))

    def requeue_running(self) -> int:
        """Recover wakes left 'running' by a crashed or interrupted process."""
        return self.db.execute("UPDATE wakes SET status = 'pending' WHERE status = 'running'").rowcount

    def pending_wakes(self) -> list[Wake]:
        rows = self.db.execute("SELECT * FROM wakes WHERE status = 'pending' ORDER BY id").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["reasons"] = json.loads(d["reasons"])
            out.append(Wake(**d))
        return out

    def has_pending_wake(self, agent: str, task_id: int) -> bool:
        return (
            self.db.execute(
                "SELECT 1 FROM wakes WHERE agent = ? AND task_id = ? AND status = 'pending'", (agent, task_id)
            ).fetchone()
            is not None
        )

    # -- spend ----------------------------------------------------------------

    def record_spend(self, agent: str, task_id: int | None, model: str, usage: dict, usd: float) -> None:
        self.db.execute(
            "INSERT INTO spend (ts, agent, task_id, model, input, output, cache_read, cache_write, usd)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                time.time(),
                agent,
                task_id,
                model,
                usage.get("input", 0),
                usage.get("output", 0),
                usage.get("cache_read", 0),
                usage.get("cache_write", 0),
                usd,
            ),
        )

    def spent(self, agent: str | None = None, task_id: int | None = None) -> float:
        sql, args = "SELECT COALESCE(SUM(usd), 0) FROM spend", []
        if agent is not None:
            sql += " WHERE agent = ?"
            args.append(agent)
        elif task_id is not None:
            sql += " WHERE task_id = ?"
            args.append(task_id)
        return float(self.db.execute(sql, args).fetchone()[0])

    def spend_by_agent(self) -> dict[str, dict]:
        rows = self.db.execute(
            "SELECT agent, SUM(usd) usd, SUM(input) input, SUM(output) output, SUM(cache_read) cache_read,"
            " COUNT(*) calls FROM spend GROUP BY agent"
        ).fetchall()
        return {r["agent"]: dict(r) for r in rows}

    def subtree_spend(self, task_id: int) -> float:
        total = self.spent(task_id=task_id)
        for c in self.children(task_id):
            total += self.subtree_spend(c.id)
        return total

    # -- notes ----------------------------------------------------------------

    def add_note(self, agent: str, text: str) -> None:
        self.db.execute("INSERT INTO notes (ts, agent, text) VALUES (?, ?, ?)", (time.time(), agent, text))

    def notes(self, agent: str, limit: int = 20) -> list[str]:
        rows = self.db.execute(
            "SELECT text FROM notes WHERE agent = ? ORDER BY id DESC LIMIT ?", (agent, limit)
        ).fetchall()
        return [r["text"] for r in reversed(rows)]
