from __future__ import annotations

from pathlib import Path

import pytest

from tinyco.config import parse
from tinyco.office import Office
from tinyco.scripted import ScriptedModel, call, say  # noqa: F401
from tinyco.store import Store

STARTUP = {
    "company": {"name": "TestCo", "mission": "Test things.", "budget_usd": 5.0, "shell": "allow", "max_attempts": 3},
    "agents": {
        "ceo": {"title": "CEO"},
        "engineer": {"title": "Engineer", "reports_to": "ceo", "effort": "medium"},
        "reviewer": {"title": "Reviewer", "reports_to": "ceo"},
    },
}

__all__ = ["STARTUP", "ScriptedModel", "call", "say"]


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
