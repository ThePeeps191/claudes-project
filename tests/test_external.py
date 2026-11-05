"""runtime = "claude-code": the full path through a stand-in `claude` executable."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from tinyco.runner import Runner

FAKE = Path(__file__).with_name("fake_claude.py")


class NoApi:
    def respond(self, **kw):
        raise AssertionError("API agents should not be called")


@pytest.fixture
def fake_claude(tmp_path):
    exe = tmp_path / "bin" / "claude"
    exe.parent.mkdir()
    exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n')
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return exe


def _company(make_office, fake_claude, shell="allow"):
    """A company whose state lives on disk, so the MCP server subprocess sees the same tasks."""
    from tinyco.config import parse
    from tinyco.office import Office
    from tinyco.store import Store

    data = {
        "company": {"name": "CC", "budget_usd": 5, "shell": shell},
        "agents": {
            "dev": {
                "runtime": "claude-code",
                "command": str(fake_claude),
                "budget_usd": 2,
                "model": "claude-sonnet-5-5",
                "max_turns": 25,
            }
        },
    }
    root = fake_claude.parent.parent / "company"
    root.mkdir(exist_ok=True)
    (root / "company.toml").write_text(_toml(data))
    company = parse(data, root)
    return Office(company, Store(company.state_dir / "state.db"))


def _toml(data) -> str:
    lines = ["[company]"] + [f"{k} = {json.dumps(v)}" for k, v in data["company"].items()]
    for name, a in data["agents"].items():
        lines.append(f"[agents.{name}]")
        lines += [f"{k} = {json.dumps(v)}" for k, v in a.items()]
    return "\n".join(lines) + "\n"


def test_claude_code_agent_does_the_work_through_mcp(make_office, fake_claude, tmp_path, monkeypatch):
    log = tmp_path / "args.json"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    office = _company(make_office, fake_claude)
    office.start_goal("Write hello.txt", checks=["grep -q 'claude code' hello.txt"])
    summary = Runner(office, NoApi()).run()

    assert summary.stopped == "idle", summary.error
    t = office.get(1)
    assert t.status == "done" and t.summary == "wrote hello.txt"
    assert office.store.spent(agent="dev") == pytest.approx(0.0123)
    said = office.store.events(kinds=("said",))[-1]["data"]["text"]
    assert said.startswith("Wrote hello.txt")

    call = json.loads(log.read_text())
    args = call["args"]
    assert Path(call["cwd"]) == office.company.workspace
    for f in (
        "-p",
        "--output-format",
        "--mcp-config",
        "--strict-mcp-config",
        "--append-system-prompt",
        "--model",
        "--max-turns",
        "--max-budget-usd",
        "--permission-mode",
    ):
        assert f in args, f
    assert args[args.index("--model") + 1] == "claude-sonnet-5-5"
    assert args[args.index("--max-turns") + 1] == "25"
    assert float(args[args.index("--max-budget-usd") + 1]) == pytest.approx(2.0)
    assert "mcp__tinyco__*" in args
    assert "tinyco" in args[args.index("--append-system-prompt") + 1]


def test_claude_code_requires_shell_allow(make_office, fake_claude):
    office = _company(make_office, fake_claude, shell="ask")
    office.start_goal("Anything")
    summary = Runner(office, NoApi()).run()
    assert summary.stopped == "fatal" and 'shell = "allow"' in summary.error
    assert len(office.store.pending_wakes()) == 1  # nothing lost; fix the config and run again


def test_missing_claude_binary_is_fatal(make_office, fake_claude):
    office = _company(make_office, fake_claude)
    office.company.agents["dev"].command = "definitely-not-installed-claude"
    office.start_goal("Anything")
    summary = Runner(office, NoApi()).run()
    assert summary.stopped == "fatal" and "not found" in summary.error
    assert len(office.store.pending_wakes()) == 1


def test_unparseable_output_blocks_task(make_office, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "garbage")
    office = _company(make_office, fake_claude)
    office.start_goal("Anything")
    Runner(office, NoApi()).run()
    t = office.get(1)
    assert t.status == "blocked" and "without a JSON result" in t.blocked_on


def test_runtime_is_validated(tmp_path):
    from tinyco.config import ConfigError, parse

    with pytest.raises(ConfigError, match="runtime"):
        parse({"agents": {"a": {"runtime": "gpt"}}}, tmp_path)
    assert os.name  # keep import used on all platforms
