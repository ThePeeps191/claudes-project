"""The org-tools MCP server, in-process and as a real subprocess."""

from __future__ import annotations

import io
import json
import os
import subprocess

from tinyco.mcp import ORG_TOOLS, Server, launch_spec, serve_stdio


def _rpc(mid, method, params=None):
    msg = {"jsonrpc": "2.0", "id": mid, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def test_server_handshake_and_tools(office):
    office.start_goal("Goal", checks=["true"])
    srv = Server(office, "ceo", 1)
    init = srv.handle(
        _rpc(
            1,
            "initialize",
            {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}},
        )
    )
    assert init["result"]["protocolVersion"] == "2025-06-18"
    assert init["result"]["capabilities"]["tools"] is not None
    assert srv.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    tools = srv.handle(_rpc(2, "tools/list"))["result"]["tools"]
    assert [t["name"] for t in tools] == list(ORG_TOOLS)
    assert all("inputSchema" in t and t["inputSchema"]["type"] == "object" for t in tools)
    assert srv.handle(_rpc(3, "ping"))["result"] == {}
    res = srv.handle(_rpc(4, "tools/call", {"name": "submit", "arguments": {"task_id": 1, "summary": "did it"}}))
    assert res["result"]["isError"] is False and "is done" in res["result"]["content"][0]["text"]
    assert office.get(1).status == "done"
    bad = srv.handle(_rpc(5, "tools/call", {"name": "run", "arguments": {"command": "ls"}}))
    assert bad["error"]["code"] == -32602  # workspace tools are the external runtime's job
    err = srv.handle(_rpc(6, "tools/call", {"name": "submit", "arguments": {"task_id": 1}}))
    assert err["result"]["isError"] is True
    assert srv.handle(_rpc(7, "nope"))["error"]["code"] == -32601


def test_serve_stdio_handles_garbage_and_batches(office):
    office.start_goal("Goal")
    srv = Server(office, "ceo", 1)
    inp = io.StringIO("not json\n\n" + json.dumps([_rpc(1, "ping"), {"jsonrpc": "2.0", "method": "x/y"}]) + "\n")
    out = io.StringIO()
    serve_stdio(srv, inp, out)
    lines = [json.loads(x) for x in out.getvalue().splitlines()]
    assert lines[0]["error"]["code"] == -32700
    assert lines[1] == [{"jsonrpc": "2.0", "id": 1, "result": {}}]


def test_mcp_serve_subprocess_shares_state(tmp_path, monkeypatch):
    from tinyco.cli import main
    from tinyco.config import load
    from tinyco.office import Office
    from tinyco.store import Store

    monkeypatch.chdir(tmp_path)
    assert main(["init", "-t", "solo"]) == 0
    assert main(["goal", "Write hello.txt", "--check", "test -f hello.txt"]) == 0
    (tmp_path / "workspace" / "hello.txt").write_text("hi")

    msgs = [
        _rpc(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}}),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        _rpc(2, "tools/list"),
        _rpc(3, "tools/call", {"name": "note", "arguments": {"text": "hello lives at the root"}}),
        _rpc(4, "tools/call", {"name": "submit", "arguments": {"task_id": 1, "summary": "wrote hello.txt"}}),
    ]
    spec = launch_spec(tmp_path, "engineer", 1, allow_shell=False)
    p = subprocess.run(
        [spec["command"], *spec["args"]],
        env={**os.environ, **spec["env"]},
        input="\n".join(json.dumps(m) for m in msgs) + "\n",
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert p.returncode == 0, p.stderr
    out = [json.loads(line) for line in p.stdout.splitlines()]
    assert [r["id"] for r in out] == [1, 2, 3, 4]  # nothing but JSON-RPC on stdout
    assert "is done" in out[3]["result"]["content"][0]["text"]

    company = load(tmp_path)
    office = Office(company, Store(company.state_dir / "state.db"))
    assert office.get(1).status == "done"
    assert office.store.notes("engineer") == ["hello lives at the root"]
