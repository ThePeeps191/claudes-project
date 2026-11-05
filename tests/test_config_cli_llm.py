"""Config parsing, the CLI, and the Claude API adapter (with a fake client)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tinyco import pricing
from tinyco.cli import main
from tinyco.config import ConfigError, load, parse
from tinyco.llm import ClaudeModel
from tinyco.templates import TEMPLATES


def test_templates_parse(tmp_path):
    import tomllib

    for name, text in TEMPLATES.items():
        c = parse(tomllib.loads(text.replace("{name}", "X")), tmp_path)
        assert c.top.reports_to is None, name


@pytest.mark.parametrize(
    "agents, match",
    [
        ({}, "at least one agent"),
        ({"a": {}, "b": {}}, "exactly one agent"),
        ({"a": {}, "b": {"reports_to": "zz"}}, "not a defined agent"),
        ({"a": {"reports_to": "b"}, "b": {"reports_to": "a"}, "c": {}}, "cycle"),
        ({"human": {}}, "reserved"),
        ({"a": {"effort": "turbo"}}, "effort"),
    ],
)
def test_invalid_configs(tmp_path, agents, match):
    with pytest.raises(ConfigError, match=match):
        parse({"agents": agents}, tmp_path)


def test_org_helpers(tmp_path):
    c = parse(
        {
            "agents": {
                "ceo": {},
                "cto": {"reports_to": "ceo"},
                "dev": {"reports_to": "cto"},
                "ops": {"reports_to": "ceo"},
            }
        },
        tmp_path,
    )
    assert c.subtree("ceo") == {"cto", "dev", "ops"}
    assert c.subtree("cto") == {"dev"}
    assert c.chain_of_command("dev") == ["cto", "ceo"]
    assert "    - dev (Dev)" in c.org_chart()


def test_cli_init_goal_status_inbox(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["init", "--name", "Acme"]) == 0
    assert (tmp_path / "company.toml").exists()
    assert (tmp_path / "workspace").is_dir()
    assert main(["init"]) == 1  # refuses to overwrite
    assert main(["goal", "Build a thing", "--check", "true", "--review", "human"]) == 0
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "T1" in out and "Build a thing" in out and "queued: 1 wake-up(s) for ceo" in out
    assert main(["inbox"]) == 0
    assert "Inbox empty" in capsys.readouterr().out
    assert main(["task", "T1"]) == 0
    assert "`true`" in capsys.readouterr().out
    assert main(["org"]) == 0
    assert "engineer" in capsys.readouterr().out
    assert main(["log"]) == 0
    assert main(["cancel", "1"]) == 0
    assert "Cancelled T1" in capsys.readouterr().out


def test_cli_finds_company_from_subdirectory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    main(["init"])
    sub = tmp_path / "workspace" / "deep"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    assert load().root == tmp_path


def test_cli_without_company(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["status"]) == 1
    assert "tinyco init" in capsys.readouterr().err


def test_pricing():
    usage = {"input": 1_000_000, "output": 1_000_000, "cache_read": 1_000_000, "cache_write": 1_000_000}
    assert pricing.cost("claude-opus-5-5", usage) == pytest.approx(4 + 20 + 0.2 + 5)
    assert pricing.cost("unknown-model", {"input": 1_000_000}) == pytest.approx(4)
    assert pricing.cost("m", {"output": 1_000_000}, {"m": {"input": 1, "output": 2}}) == pytest.approx(2)


class _FakeStream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.msg


class _FakeClient:
    def __init__(self, msg):
        self.params = None
        self.msg = msg
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **params):
        self.params = params
        return _FakeStream(self.msg)


def test_claude_model_request_shape(tmp_path):
    msg = SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking="plan it"),
            SimpleNamespace(type="text", text="On it."),
            SimpleNamespace(type="tool_use", id="tu1", name="note", input={"text": "x"}),
        ],
        stop_reason="tool_use",
        model="claude-opus-5-5",
        usage=SimpleNamespace(
            input_tokens=10, output_tokens=5, cache_read_input_tokens=100, cache_creation_input_tokens=7
        ),
    )
    client = _FakeClient(msg)
    agent = parse({"agents": {"a": {"effort": "medium"}}}, tmp_path).agents["a"]
    turn = ClaudeModel(client).respond(
        agent=agent,
        system="sys",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"name": "note", "description": "d", "input_schema": {"type": "object"}}],
    )
    p = client.params
    assert p["model"] == "claude-opus-5-5"
    assert p["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert p["output_config"] == {"effort": "medium"}
    assert p["fallbacks"] == "default" and p["betas"] == ["server-side-fallback-2026-07-01"]
    assert p["cache_control"] == {"type": "ephemeral"}
    assert p["tools"][0]["eager_input_streaming"] is True
    assert "temperature" not in p
    assert turn.text == "On it." and turn.thinking == "plan it"
    assert turn.tool_calls[0].name == "note"
    assert turn.usage == {"input": 10, "output": 5, "cache_read": 100, "cache_write": 7, "web_searches": 0}
    assert turn.content is not None and len(turn.content) == 3


def test_claude_model_skips_fallbacks_for_models_without_them(tmp_path):
    msg = SimpleNamespace(
        content=[],
        stop_reason="end_turn",
        model="claude-haiku-5-5",
        usage=SimpleNamespace(
            input_tokens=1, output_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0
        ),
    )
    client = _FakeClient(msg)
    agent = parse({"agents": {"a": {"model": "claude-haiku-5-5"}}}, tmp_path).agents["a"]
    ClaudeModel(client).respond(agent=agent, system="s", messages=[], tools=[])
    assert "fallbacks" not in client.params and "betas" not in client.params


def test_package_has_no_stray_paths():
    root = Path(__file__).resolve().parents[1]
    assert (root / "tinyco" / "__init__.py").exists()


def test_pricing_override_from_company_toml(tmp_path):
    import tomllib

    text = """
[company]
name = "P"
[company.pricing."my-model"]
input = 1.0
output = 2.0
[agents.a]
model = "my-model"
"""
    c = parse(tomllib.loads(text), tmp_path)
    overrides = c.extra.get("pricing")
    assert pricing.cost("my-model", {"input": 1_000_000, "output": 1_000_000}, overrides) == pytest.approx(3.0)


def test_web_tools_for_web_agents(tmp_path):
    from tinyco.tools import TOOLS, tools_for

    c = parse(
        {
            "agents": {
                "a": {"web": True},
                "b": {"reports_to": "a"},
                "h": {"reports_to": "a", "web": True, "model": "claude-haiku-5-5"},
            }
        },
        tmp_path,
    )
    assert tools_for(c.agents["b"]) is TOOLS
    web = [t["type"] for t in tools_for(c.agents["a"]) if "type" in t]
    assert web == ["web_search_20260209", "web_fetch_20260209"]
    assert [t["type"] for t in tools_for(c.agents["h"]) if "type" in t] == ["web_search_20250305", "web_fetch_20250910"]


def test_server_tools_do_not_get_eager_streaming(tmp_path):
    from tinyco.tools import tools_for

    msg = SimpleNamespace(
        content=[],
        stop_reason="end_turn",
        model="claude-opus-5-5",
        usage=SimpleNamespace(
            input_tokens=1,
            output_tokens=1,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
            server_tool_use=SimpleNamespace(web_search_requests=3),
        ),
    )
    client = _FakeClient(msg)
    agent = parse({"agents": {"a": {"web": True}}}, tmp_path).agents["a"]
    turn = ClaudeModel(client).respond(agent=agent, system="s", messages=[], tools=tools_for(agent))
    for t in client.params["tools"]:
        assert ("eager_input_streaming" in t) == ("input_schema" in t)
    assert turn.usage["web_searches"] == 3
    assert pricing.cost("claude-opus-5-5", {"web_searches": 3}) == pytest.approx(0.03)


def test_web_searches_show_up_in_events(office):
    from tinyco.runner import Runner
    from tinyco.scripted import ScriptedModel, say

    office.start_goal("Look something up")
    search = {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search", "input": {"query": "tinyco"}}
    result = {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1", "content": []}
    model = ScriptedModel({"ceo": [[search, result, say("Found nothing.")]]})
    Runner(office, model).run(max_wakes=1)
    ev = office.store.events(kinds=("web_search",))
    assert ev and ev[0]["data"]["query"] == "tinyco"


def test_cli_cost_report(tmp_path, monkeypatch, capsys):
    from tinyco.config import load
    from tinyco.office import Office
    from tinyco.store import Store

    monkeypatch.chdir(tmp_path)
    main(["init"])
    assert main(["cost"]) == 0
    assert "No model spend yet" in capsys.readouterr().out
    company = load(tmp_path)
    office = Office(company, Store(company.state_dir / "state.db"))
    office.start_goal("Build it")
    usage = {"input": 1000, "output": 500, "cache_read": 9000, "cache_write": 0}
    office.store.record_spend("ceo", 1, "claude-opus-5-5", usage, pricing.cost("claude-opus-5-5", usage))
    assert main(["cost"]) == 0
    out = capsys.readouterr().out
    assert "By agent" in out and "ceo" in out and "90%" in out and "Build it" in out
