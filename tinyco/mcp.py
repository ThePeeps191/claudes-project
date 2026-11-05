"""A minimal MCP server exposing tinyco's org tools over stdio.

External agent runtimes (for example Claude Code) launch this for a single
wake: ``tinyco mcp-serve --agent engineer --task 7``. The agent then calls
``submit``, ``create_task``, ``review`` and the rest exactly as built-in
agents do, under the same rules. File editing and shell access come from the
external runtime's own tools.

Protocol: JSON-RPC 2.0, one message per line on stdin/stdout.
"""

from __future__ import annotations

import json
import sys
from typing import IO, Any

from . import __version__
from .office import Office
from .tools import TOOLS, Toolbox

ORG_TOOLS = ("create_task", "submit", "review", "update_task", "comment", "ask_human", "view_task", "note")
DEFAULT_PROTOCOL = "2025-06-18"


def tool_list() -> list[dict]:
    by_name = {t["name"]: t for t in TOOLS}
    return [
        {"name": n, "description": by_name[n]["description"], "inputSchema": by_name[n]["input_schema"]}
        for n in ORG_TOOLS
    ]


class Server:
    def __init__(self, office: Office, agent: str, task_id: int):
        self.box = Toolbox(office, agent, office.get(task_id))
        self.office = office
        self.agent = agent

    def handle(self, msg: Any) -> dict | None:
        """Handle one JSON-RPC message; returns the response, or None for notifications."""
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
            return _error(msg.get("id") if isinstance(msg, dict) else None, -32600, "invalid request")
        method, mid, params = msg["method"], msg.get("id"), msg.get("params") or {}
        if mid is None:  # notification (e.g. notifications/initialized)
            return None
        if method == "initialize":
            return _result(
                mid,
                {
                    "protocolVersion": params.get("protocolVersion") or DEFAULT_PROTOCOL,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "tinyco", "version": __version__},
                    "instructions": "Org tools for working inside a tinyco company: delegate, submit, review, ask the human.",
                },
            )
        if method == "ping":
            return _result(mid, {})
        if method == "tools/list":
            return _result(mid, {"tools": tool_list()})
        if method == "tools/call":
            name = params.get("name")
            if name not in ORG_TOOLS:
                return _error(mid, -32602, f"unknown tool {name!r}")
            args = params.get("arguments") or {}
            self.office.store.event("tool", self.agent, self.box.task.id, tool=name, via="mcp")
            text, is_error = self.box.call(name, args)
            if is_error:
                self.office.store.event("tool_error", self.agent, self.box.task.id, tool=name, error=text[:500])
            return _result(mid, {"content": [{"type": "text", "text": text}], "isError": is_error})
        return _error(mid, -32601, f"method not found: {method}")


def _result(mid, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _error(mid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def serve_stdio(server: Server, stdin: IO[str] | None = None, stdout: IO[str] | None = None) -> None:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            resp: dict | None = _error(None, -32700, "parse error")
        else:
            if isinstance(msg, list):  # batch
                out = [r for r in (server.handle(m) for m in msg) if r is not None]
                resp = out or None  # type: ignore[assignment]
            else:
                resp = server.handle(msg)
        if resp is not None:
            stdout.write(json.dumps(resp) + "\n")
            stdout.flush()


def launch_spec(root, agent: str, task_id: int, allow_shell: bool) -> dict:
    """How to start this server for one wake: an MCP stdio server entry (command, args, env)."""
    import os
    from pathlib import Path

    pkg_parent = str(Path(__file__).resolve().parent.parent)
    path = os.pathsep.join(p for p in (pkg_parent, os.environ.get("PYTHONPATH", "")) if p)
    args = ["-m", "tinyco", "-C", str(root), "mcp-serve", "--agent", agent, "--task", str(task_id)]
    if allow_shell:
        args.append("--allow-shell")
    return {"command": sys.executable, "args": args, "env": {"PYTHONPATH": path}}
