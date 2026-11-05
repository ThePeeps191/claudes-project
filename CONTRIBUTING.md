# Contributing

Thanks for helping. tinyco aims to stay small: one package, standard library plus the `anthropic` SDK, and an org model you can hold in your head.

## Setup

```
git clone https://github.com/ThePeeps191/claudes-project && cd claudes-project
pip install -e ".[dev]"
pytest -q
ruff check . && ruff format --check .
```

No API key is needed for the tests. Whole companies run against `tinyco.scripted.ScriptedModel`, which plays back scripted agent turns while everything else (files, commands, checks, the company rules) is real.

## Where things live

| File | What it does |
|---|---|
| `tinyco/office.py` | The rules: delegation, submit/checks, review, escalation, human replies |
| `tinyco/runner.py` | The wake queue and the per-wake agent loop, budgets, parallelism |
| `tinyco/briefing.py` | System prompt and per-wake briefing |
| `tinyco/tools.py` | Tool schemas and their implementations |
| `tinyco/llm.py` | Claude API backend |
| `tinyco/external.py`, `tinyco/mcp.py` | Claude Code runtime and the org-tools MCP server |
| `tinyco/store.py` | SQLite schema and queries |
| `tinyco/dashboard.py`, `tinyco/static/index.html` | Web dashboard |

## Guidelines

- A behaviour change comes with a test, ideally a scripted company run (see `tests/test_company.py`).
- Keep the tool list and system prompt stable across calls; prompt caching depends on it.
- New dependencies need a strong reason.
- Prompts are written for capable models: say what matters and why, and skip the shouting.
