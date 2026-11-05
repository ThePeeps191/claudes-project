from __future__ import annotations

import itertools
from collections.abc import Callable
from pathlib import Path

import pytest

from tinyco.config import parse
from tinyco.llm import Turn, normalize
from tinyco.office import Office
from tinyco.store import Store

STARTUP = {
    "company": {"name": "TestCo", "mission": "Test things.", "budget_usd": 5.0, "shell": "allow", "max_attempts": 3},
    "agents": {
        "ceo": {"title": "CEO"},
        "engineer": {"title": "Engineer", "reports_to": "ceo", "effort": "medium"},
        "reviewer": {"title": "Reviewer", "reports_to": "ceo"},
    },
}

_ids = itertools.count(1)


def call(name: str, **args) -> dict:
    return {"type": "tool_use", "id": f"toolu_{next(_ids)}", "name": name, "input": args}


def say(text: str) -> dict:
    return {"type": "text", "text": text}


class ScriptedModel:
    """Plays back scripted turns per agent. A turn is a list of blocks or a callable(messages) -> blocks."""

    def __init__(self, script: dict[str, list], usage: dict | None = None, stop_reasons: dict | None = None):
        self.script = {k: list(v) for k, v in script.items()}
        self.usage = usage or {"input": 1000, "output": 200, "cache_read": 0, "cache_write": 0}
        self.calls: list[dict] = []
        self.stop_reasons = stop_reasons or {}

    def respond(self, *, agent, system, messages, tools) -> Turn:
        self.calls.append({"agent": agent.name, "system": system, "messages": list(messages), "tools": tools})
        queue = self.script.get(agent.name) or []
        blocks: list | Callable = queue.pop(0) if queue else [say("Nothing more to do.")]
        if callable(blocks):
            blocks = blocks(messages)
        stop = self.stop_reasons.get((agent.name, len([c for c in self.calls if c["agent"] == agent.name])))
        if stop is None:
            stop = "tool_use" if any(b["type"] == "tool_use" for b in blocks) else "end_turn"
        return normalize(blocks, stop, agent.model, dict(self.usage), "declined" if stop == "refusal" else None)

    def remaining(self) -> dict[str, int]:
        return {k: len(v) for k, v in self.script.items() if v}


@pytest.fixture
def make_office(tmp_path: Path):
    def _make(data: dict | None = None, approve=None) -> Office:
        company = parse(data or STARTUP, tmp_path)
        store = Store(":memory:")
        return Office(company, store, approve)

    return _make


@pytest.fixture
def office(make_office) -> Office:
    return make_office()
