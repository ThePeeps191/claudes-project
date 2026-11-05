"""Agents backed by an external runtime instead of the Claude API directly.

``runtime = "claude-code"`` runs each wake as a headless Claude Code session:

    claude -p <briefing> --output-format json
           --append-system-prompt <role + company rules>
           --mcp-config <tinyco org tools> --strict-mcp-config
           --model <model> --max-turns <n> --max-budget-usd <remaining>
           --permission-mode bypassPermissions

Claude Code does the hands-on work with its own file and shell tools and uses
tinyco's org tools (submit, create_task, review, ...) through the MCP server in
``tinyco.mcp``, so the company rules are the same as for API agents. Spend is
taken from Claude Code's ``total_cost_usd`` (its own estimate) and counts
against tinyco budgets.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .briefing import briefing, system_prompt
from .config import AgentSpec
from .llm import ModelError
from .mcp import launch_spec
from .office import Office
from .store import Wake

EXTERNAL_NOTE = """
You're working through Claude Code. Use your normal file and shell tools for the work itself.
The company's org tools (submit, create_task, review, update_task, comment, ask_human, view_task,
note) come from the `tinyco` MCP server. A task only counts as finished once you call its submit
tool and the checks pass.
"""

TIMEOUT = 3600


def run_claude_code(office: Office, agent: AgentSpec, wake: Wake, budget_left: float | None) -> int:
    """Run one wake through Claude Code. Returns the number of turns it reported."""
    company, store, task = office.company, office.store, office.get(wake.task_id)
    if company.shell != "allow":
        raise ModelError(
            f'{agent.name} runs on Claude Code, which needs company.shell = "allow" (or `tinyco run --yes`): '
            "headless Claude Code can't stop to ask you about commands. Use a container or VM.",
            fatal=True,
        )
    exe = shutil.which(agent.command)
    if exe is None:
        raise ModelError(
            f"`{agent.command}` not found on PATH (install Claude Code, or set agents.{agent.name}.command)", fatal=True
        )

    spec = launch_spec(company.root, agent.name, task.id, allow_shell=True)
    with tempfile.TemporaryDirectory(prefix="tinyco-") as tmp:
        cfg = Path(tmp) / "mcp.json"
        cfg.write_text(json.dumps({"mcpServers": {"tinyco": spec}}), encoding="utf-8")
        cmd = [
            exe,
            "-p",
            briefing(office, agent, wake),
            "--output-format",
            "json",
            "--append-system-prompt",
            system_prompt(company, agent) + EXTERNAL_NOTE,
            "--mcp-config",
            str(cfg),
            "--strict-mcp-config",
            "--allowedTools",
            "mcp__tinyco__*",
            "--model",
            agent.model,
            "--max-turns",
            str(agent.max_turns),
            "--permission-mode",
            "bypassPermissions",
            "--no-session-persistence",
        ]
        if budget_left is not None:
            cmd += ["--max-budget-usd", f"{max(budget_left, 0.01):.2f}"]
        store.event("external_start", agent.name, task.id, runtime="claude-code", model=agent.model)
        try:
            p = subprocess.run(
                cmd,
                cwd=company.workspace,
                capture_output=True,
                text=True,
                timeout=TIMEOUT,
                stdin=subprocess.DEVNULL,
                env=os.environ.copy(),
            )
        except subprocess.TimeoutExpired as e:
            _charge_unknown(office, agent, task.id, budget_left)
            raise ModelError(f"Claude Code run timed out after {TIMEOUT}s", escalate=True) from e

    result = _parse(p.stdout)
    if result is None:
        _charge_unknown(office, agent, task.id, budget_left)
        detail = (p.stderr or p.stdout or "").strip()[-800:]
        raise ModelError(f"Claude Code exited {p.returncode} without a JSON result: {detail}", escalate=True)

    usd = float(result.get("total_cost_usd") or 0.0)
    u = result.get("usage") or {}
    usage = {
        "input": int(u.get("input_tokens") or 0),
        "output": int(u.get("output_tokens") or 0),
        "cache_read": int(u.get("cache_read_input_tokens") or 0),
        "cache_write": int(u.get("cache_creation_input_tokens") or 0),
    }
    store.record_spend(agent.name, task.id, f"claude-code:{agent.model}", usage, usd)
    text = str(result.get("result") or "").strip()
    if text:
        store.event("said", agent.name, task.id, text=text[:4000])
    turns = int(result.get("num_turns") or 0)
    subtype = result.get("subtype", "")
    if subtype == "error_max_turns":
        store.event("turn_limit", agent.name, task.id, turns=turns)
    elif subtype == "error_max_budget_usd":
        office.block(task.id, f"budget: {agent.name}'s budget ran out during a Claude Code run", agent.name)
    elif result.get("is_error"):
        raise ModelError(f"Claude Code run failed ({subtype or 'error'}): {text[:500]}")
    return max(turns, 1)


def _charge_unknown(office: Office, agent: AgentSpec, task_id: int, cap: float | None) -> None:
    """A run that died without reporting its cost may have spent up to its --max-budget-usd cap: count that."""
    if cap and cap > 0:
        office.store.record_spend(agent.name, task_id, f"claude-code:{agent.model}:estimate", {}, cap)
        office.store.event(
            "spend_estimated",
            agent.name,
            task_id,
            usd=round(cap, 4),
            why="Claude Code exited without reporting its cost; counted its full budget cap",
        )


def _parse(stdout: str) -> dict | None:
    """Claude Code prints one JSON object; tolerate stray lines around it."""
    stdout = (stdout or "").strip()
    try:
        obj = json.loads(stdout)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and ("result" in obj or "subtype" in obj):
                return obj
    return None
