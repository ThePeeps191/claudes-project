"""Stand-in for the `claude` CLI used by tests/test_external.py.

Parses the flags tinyco passes, starts the MCP server from --mcp-config exactly
as Claude Code would, does a little work, submits through MCP, and prints a
result object shaped like `claude -p --output-format json`.
"""

import json
import os
import re
import subprocess
import sys

args = sys.argv[1:]


def flag(name):
    return args[args.index(name) + 1] if name in args else None


log = os.environ.get("FAKE_CLAUDE_LOG")
if log:
    with open(log, "w") as f:
        json.dump({"args": args, "cwd": os.getcwd()}, f)

mode = os.environ.get("FAKE_CLAUDE_MODE", "work")
if mode == "garbage":
    print("this is not json")
    sys.exit(1)

cfg = json.load(open(flag("--mcp-config")))["mcpServers"]["tinyco"]
proc = subprocess.Popen(
    [cfg["command"], *cfg["args"]],
    env={**os.environ, **cfg.get("env", {})},
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    text=True,
)


def rpc(mid, method, params=None):
    msg = {"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}}
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()
    return json.loads(proc.stdout.readline())


rpc(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "fake-claude"}})
proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
tools = [t["name"] for t in rpc(2, "tools/list")["result"]["tools"]]
task_id = int(re.search(r"## T(\d+):", flag("-p")).group(1))

with open("hello.txt", "w") as f:
    f.write("hello from claude code\n")
res = rpc(3, "tools/call", {"name": "submit", "arguments": {"task_id": task_id, "summary": "wrote hello.txt"}})
proc.stdin.close()
proc.wait(timeout=30)

print(
    json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "num_turns": 3,
            "session_id": "s1",
            "result": "Wrote hello.txt and submitted. " + res["result"]["content"][0]["text"][:60],
            "total_cost_usd": 0.0123,
            "usage": {
                "input_tokens": 1200,
                "output_tokens": 300,
                "cache_read_input_tokens": 5000,
                "cache_creation_input_tokens": 100,
            },
            "tools_seen": tools,
        }
    )
)
