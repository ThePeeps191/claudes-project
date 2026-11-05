"""A local web dashboard: ``tinyco dashboard``.

Standard library only. It reads the same SQLite file as ``tinyco run`` (WAL
mode allows concurrent readers), so you can watch a run live in the browser
while it executes in a terminal. Human actions (new goal, reply, approve,
reject) go through the same ``Office`` rules as the CLI.

The server binds to 127.0.0.1. Write requests must carry an ``X-Tinyco``
header, which a cross-site form or image request can't set, and must be
JSON.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import parse_qs, urlparse

from .config import Company
from .office import HUMAN, Office, OfficeError
from .store import Store


def snapshot(office: Office) -> dict:
    company, store = office.company, office.store
    spend = store.spend_by_agent()
    running = {
        w["agent"]: w["task_id"]
        for w in store.db.execute("SELECT agent, task_id FROM wakes WHERE status = 'running'").fetchall()
    }
    queued: dict[str, int] = {}
    for w in store.pending_wakes():
        queued[w.agent] = queued.get(w.agent, 0) + 1

    agents = []
    for a in company.agents.values():
        s = spend.get(a.name, {})
        agents.append(
            {
                "name": a.name,
                "title": a.title,
                "reports_to": a.reports_to,
                "model": a.model,
                "effort": a.effort,
                "budget": a.budget_usd,
                "spent": round(s.get("usd", 0.0) or 0.0, 4),
                "calls": s.get("calls", 0) or 0,
                "working_on": running.get(a.name),
                "queued": queued.get(a.name, 0),
            }
        )
    tasks = []
    for t in store.tasks():
        tasks.append(
            {
                "id": t.id,
                "parent_id": t.parent_id,
                "title": t.title,
                "description": t.description,
                "assignee": t.assignee,
                "creator": t.creator,
                "status": t.status,
                "checks": t.checks,
                "reviewer": t.reviewer,
                "attempts": t.attempts,
                "summary": t.summary,
                "blocked_on": t.blocked_on,
                "created_at": t.created_at,
                "updated_at": t.updated_at,
                "spent": round(store.spent(task_id=t.id), 4),
            }
        )
    inbox = [
        {
            "id": t.id,
            "title": t.title,
            "kind": "review" if t.status == "in_review" else "question",
            "text": t.summary if t.status == "in_review" else (t.blocked_on or "").removeprefix(f"{HUMAN}: "),
        }
        for t in office.inbox()
    ]
    return {
        "company": {
            "name": company.name,
            "mission": company.mission,
            "budget": company.budget_usd,
            "spent": round(store.spent(), 4),
            "shell": company.shell,
            "max_attempts": company.max_attempts,
        },
        "agents": agents,
        "tasks": tasks,
        "inbox": inbox,
        "running": bool(running),
        "pending": sum(queued.values()),
    }


def make_handler(office: Office, lock: threading.Lock):
    page = resources.files("tinyco").joinpath("static/index.html").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        server_version = "tinyco"

        def log_message(self, *args):  # quiet
            pass

        def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if ctype.startswith("text/html"):
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'",
                )
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj, default=str).encode())

        def _local(self) -> bool:
            """Only answer requests addressed to this machine (defeats DNS rebinding)."""
            port = self.server.server_address[1]
            allowed = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
            if (self.headers.get("Host") or "").lower() not in allowed:
                self._json(403, {"error": "unexpected Host header; open the dashboard via 127.0.0.1"})
                return False
            origin = self.headers.get("Origin")
            if origin is not None and origin.lower() not in {f"http://{h}" for h in allowed}:
                self._json(403, {"error": "cross-origin request refused"})
                return False
            return True

        def do_GET(self) -> None:  # noqa: N802
            if not self._local():
                return
            url = urlparse(self.path)
            if url.path in ("/", "/index.html"):
                return self._send(200, page, "text/html; charset=utf-8")
            if url.path == "/api/state":
                with lock:
                    return self._json(200, snapshot(office))
            if url.path == "/api/events":
                q = parse_qs(url.query)
                try:
                    after = int(q.get("after", ["0"])[0] or 0)
                    task = q.get("task", [None])[0]
                    task_id = int(task) if task else None
                    if (task_id is not None and not 0 < task_id < 2**63) or not -1 < after < 2**63:
                        raise ValueError
                except ValueError:
                    return self._json(400, {"error": "bad after/task parameter"})
                sql, args = "SELECT * FROM events WHERE id > ?", [after]
                if task_id is not None:
                    sql += " AND task_id = ?"
                    args.append(task_id)
                sql += " ORDER BY id DESC LIMIT 300"
                with lock:
                    rows = office.store.db.execute(sql, args).fetchall()
                events = [
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
                return self._json(200, events)
            self._json(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if not self._local():
                return
            if self.headers.get("X-Tinyco") != "1" or "json" not in (self.headers.get("Content-Type") or ""):
                return self._json(403, {"error": "missing X-Tinyco header"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(min(n, 1_000_000)) or b"{}")
            except (ValueError, json.JSONDecodeError):
                return self._json(400, {"error": "invalid JSON"})
            path = urlparse(self.path).path
            try:
                with lock:
                    if path == "/api/goal":
                        checks = [c for c in body.get("checks", []) if isinstance(c, str) and c.strip()]
                        t = office.start_goal(
                            str(body["title"]), str(body.get("description", "")), checks, body.get("reviewer") or None
                        )
                        msg = f"Created {t.ref}. Start the company with: tinyco run"
                    elif path == "/api/reply":
                        msg = office.reply(int(body["task_id"]), str(body["text"]))
                    elif path == "/api/approve":
                        msg = office.human_review(int(body["task_id"]), True, str(body.get("notes", "")))
                    elif path == "/api/reject":
                        msg = office.human_review(int(body["task_id"]), False, str(body["notes"]))
                    elif path == "/api/cancel":
                        msg = office.update_task(HUMAN, int(body["task_id"]), cancel=True)
                    else:
                        return self._json(404, {"error": "not found"})
            except OfficeError as e:
                return self._json(409, {"error": str(e)})
            except (KeyError, TypeError, ValueError, OverflowError) as e:
                return self._json(400, {"error": f"bad request: {e}"})
            self._json(200, {"ok": True, "message": msg})

    return Handler


def serve(company: Company, host: str = "127.0.0.1", port: int = 8321) -> ThreadingHTTPServer:
    store = Store(company.state_dir / "state.db")
    office = Office(company, store)
    server = ThreadingHTTPServer((host, port), make_handler(office, threading.Lock()))
    server.daemon_threads = True
    return server
