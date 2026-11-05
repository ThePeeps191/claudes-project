"""Remote MCP servers for agents (Claude API MCP connector, and Claude Code)."""

from __future__ import annotations

import json

import pytest

from tinyco.config import ConfigError, parse
from tinyco.llm import ModelError, mcp_servers, normalize
from tinyco.tools import TOOLS, tools_for

NOTION = {
    "name": "notion",
    "url": "https://mcp.notion.example/mcp",
    "token_env": "NOTION_TOKEN",
    "tools": ["search", "create-page"],
}


def _agent(tmp_path, **extra):
    return parse({"agents": {"marketer": {"mcp": [NOTION], **extra}}}, tmp_path).agents["marketer"]


def test_config_validation(tmp_path):
    a = _agent(tmp_path)
    assert a.mcp == [NOTION]
    bad = [
        [{"name": "x y", "url": "https://a.example"}],
        [{"name": "crm", "url": "http://insecure.example"}],
        [{"name": "crm", "url": "https://a.example", "token_env": "not a var"}],
        [{"name": "crm", "url": "https://a.example"}, {"name": "crm", "url": "https://b.example"}],
        [{"name": "tinyco", "url": "https://a.example"}],
        [{"name": "crm", "url": "https://a.example", "tools": "search"}],
        {"name": "crm"},
    ]
    for mcp in bad:
        with pytest.raises(ConfigError):
            parse({"agents": {"a": {"mcp": mcp}}}, tmp_path)
    with pytest.raises(ConfigError, match="API agents"):
        parse({"company": {"shell": "allow"}, "agents": {"a": {"runtime": "claude-code", "mcp": [NOTION]}}}, tmp_path)


def test_toolsets_with_allowlist(tmp_path):
    tools = tools_for(_agent(tmp_path))
    assert tools[: len(TOOLS)] == TOOLS
    assert tools[-1] == {
        "type": "mcp_toolset",
        "mcp_server_name": "notion",
        "default_config": {"enabled": False},
        "configs": {"search": {"enabled": True}, "create-page": {"enabled": True}},
    }
    open_server = parse(
        {"agents": {"a": {"mcp": [{"name": "crm", "url": "https://crm.example/mcp"}]}}}, tmp_path
    ).agents["a"]
    assert tools_for(open_server)[-1] == {"type": "mcp_toolset", "mcp_server_name": "crm"}


def test_token_comes_from_the_environment(tmp_path, monkeypatch):
    a = _agent(tmp_path)
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    with pytest.raises(ModelError) as e:
        mcp_servers(a)
    assert e.value.fatal and "$NOTION_TOKEN" in str(e.value)
    monkeypatch.setenv("NOTION_TOKEN", "secret-123")
    assert mcp_servers(a) == [
        {"type": "url", "url": NOTION["url"], "name": "notion", "authorization_token": "secret-123"}
    ]


def test_mcp_tool_use_blocks_are_surfaced():
    turn = normalize(
        [
            {
                "type": "mcp_tool_use",
                "id": "mcptoolu_1",
                "name": "create-page",
                "server_name": "notion",
                "input": {"title": "Launch plan"},
            },
            {"type": "mcp_tool_result", "tool_use_id": "mcptoolu_1", "content": []},
        ],
        "end_turn",
        "m",
        {},
    )
    assert [(c.name, c.input) for c in turn.server_calls] == [("mcp:notion.create-page", {"title": "Launch plan"})]


def test_wire_request_through_real_sdk(tmp_path, monkeypatch):
    import anthropic
    import httpx2 as httpx

    from tinyco.llm import ClaudeModel

    monkeypatch.setenv("NOTION_TOKEN", "secret-123")
    bodies, headers = [], []
    sse = "".join(
        f"event: {e['type']}\ndata: {json.dumps(e)}\n\n"
        for e in [
            {
                "type": "message_start",
                "message": {
                    "id": "m",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-opus-5-5",
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
            },
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 1},
            },
            {"type": "message_stop"},
        ]
    ).encode()

    def handler(request):
        bodies.append(json.loads(request.content))
        headers.append(request.headers)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse)

    client = anthropic.Anthropic(
        api_key="k", http_client=httpx.Client(transport=httpx.MockTransport(handler)), max_retries=0
    )
    agent = _agent(tmp_path)
    ClaudeModel(client).respond(
        agent=agent, system="s", messages=[{"role": "user", "content": "x"}], tools=tools_for(agent)
    )
    body = bodies[0]
    assert body["mcp_servers"] == [
        {"type": "url", "url": NOTION["url"], "name": "notion", "authorization_token": "secret-123"}
    ]
    toolset = body["tools"][-1]
    assert toolset["type"] == "mcp_toolset" and "eager_input_streaming" not in toolset
    beta = headers[0].get("anthropic-beta", "")
    assert "mcp-client-2025-11-20" in beta and "server-side-fallback-2026-07-01" in beta


def test_claude_code_gets_servers_without_writing_tokens(tmp_path, monkeypatch):
    import stat
    import sys
    from pathlib import Path

    from tinyco.office import Office
    from tinyco.runner import Runner
    from tinyco.store import Store

    fake = Path(__file__).with_name("fake_claude.py")
    exe = tmp_path / "bin" / "claude"
    exe.parent.mkdir()
    exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{fake}" "$@"\n')
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "log.json"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.setenv("CRM_TOKEN", "super-secret")
    root = tmp_path / "co"
    root.mkdir()
    (root / "company.toml").write_text(
        '[company]\nshell = "allow"\n[agents.dev]\nruntime = "claude-code"\ncommand = "' + str(exe) + '"\n'
        '[[agents.dev.mcp]]\nname = "crm"\nurl = "https://crm.example/mcp"\ntoken_env = "CRM_TOKEN"\n'
    )
    from tinyco.config import load

    company = load(root)
    office = Office(company, Store(company.state_dir / "state.db"))
    office.start_goal("Write hello.txt", checks=["true"])
    Runner(office, object()).run()
    record = json.loads(log.read_text())
    cfg = json.loads(record["mcp_config"])["mcpServers"]
    assert cfg["crm"] == {
        "type": "http",
        "url": "https://crm.example/mcp",
        "headers": {"Authorization": "Bearer ${CRM_TOKEN}"},
    }
    assert "super-secret" not in record["mcp_config"]
    assert "mcp__crm__*" in record["args"]


def test_system_prompt_names_connected_services(tmp_path):
    from tinyco.briefing import system_prompt

    c = parse({"agents": {"marketer": {"mcp": [NOTION]}}}, tmp_path)
    assert "connected to these services through MCP tools: notion" in system_prompt(c, c.agents["marketer"])
