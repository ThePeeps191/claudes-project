"""Company configuration: the org chart, budgets and policies in ``company.toml``."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_MODEL = "claude-opus-5-5"
EFFORTS = ("low", "medium", "high", "xhigh", "max")
SHELL_POLICIES = ("ask", "allow", "deny")
RUNTIMES = ("api", "claude-code")
CONFIG_NAME = "company.toml"
STATE_DIR = ".tinyco"


class ConfigError(ValueError):
    pass


@dataclass
class AgentSpec:
    name: str
    title: str
    instructions: str = ""
    reports_to: str | None = None
    model: str = DEFAULT_MODEL
    effort: str = "high"
    budget_usd: float | None = None
    max_turns: int = 40
    runtime: str = "api"  # api | claude-code
    command: str = "claude"  # executable for external runtimes
    web: bool = False  # server-side web search + fetch (API runtime)


@dataclass
class Company:
    name: str
    mission: str
    agents: dict[str, AgentSpec]
    root: Path
    budget_usd: float = 10.0
    workspace_dir: str = "workspace"
    shell: str = "ask"
    check_timeout: int = 300
    max_attempts: int = 3
    git: bool = True
    parallel: int = 1
    extra: dict = field(default_factory=dict)

    @property
    def workspace(self) -> Path:
        return (self.root / self.workspace_dir).resolve()

    @property
    def state_dir(self) -> Path:
        return self.root / STATE_DIR

    @property
    def top(self) -> AgentSpec:
        return next(a for a in self.agents.values() if a.reports_to is None)

    def reports(self, name: str) -> list[str]:
        return [a.name for a in self.agents.values() if a.reports_to == name]

    def subtree(self, name: str) -> set[str]:
        """Everyone who reports to ``name``, directly or indirectly."""
        out: set[str] = set()
        stack = self.reports(name)
        while stack:
            n = stack.pop()
            if n not in out:
                out.add(n)
                stack.extend(self.reports(n))
        return out

    def chain_of_command(self, name: str) -> list[str]:
        chain, cur = [], self.agents[name].reports_to
        while cur is not None:
            chain.append(cur)
            cur = self.agents[cur].reports_to
        return chain

    def org_chart(self) -> str:
        lines: list[str] = []

        def walk(n: str, depth: int) -> None:
            a = self.agents[n]
            lines.append(f"{'  ' * depth}- {a.name} ({a.title})")
            for r in self.reports(n):
                walk(r, depth + 1)

        walk(self.top.name, 0)
        return "\n".join(lines)


def find_root(start: Path | None = None) -> Path:
    """Walk up from ``start`` until a directory containing company.toml is found."""
    cur = (start or Path.cwd()).resolve()
    for p in (cur, *cur.parents):
        if (p / CONFIG_NAME).is_file():
            return p
    raise ConfigError(f"no {CONFIG_NAME} found here or in any parent directory (run `tinyco init`)")


def load(root: Path | None = None) -> Company:
    root = find_root(root)
    path = root / CONFIG_NAME
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from None
    return parse(data, root)


def parse(data: dict, root: Path) -> Company:
    c = data.get("company") or {}
    agents_raw = data.get("agents") or {}
    if not agents_raw:
        raise ConfigError("company.toml must define at least one agent under [agents.<name>]")

    agents: dict[str, AgentSpec] = {}
    for name, a in agents_raw.items():
        if not name.isidentifier() or name == "human":
            raise ConfigError(f"invalid agent name {name!r}: use letters, digits and underscores ('human' is reserved)")
        effort = a.get("effort", "high")
        if effort not in EFFORTS:
            raise ConfigError(f"agents.{name}.effort must be one of {', '.join(EFFORTS)}")
        runtime = a.get("runtime", "api")
        if runtime not in RUNTIMES:
            raise ConfigError(f"agents.{name}.runtime must be one of {', '.join(RUNTIMES)}")
        budget = a.get("budget_usd")
        agents[name] = AgentSpec(
            name=name,
            title=a.get("title", name.replace("_", " ").title()),
            instructions=(a.get("instructions") or "").strip(),
            reports_to=a.get("reports_to"),
            model=a.get("model", DEFAULT_MODEL),
            effort=effort,
            budget_usd=float(budget) if budget is not None else None,
            max_turns=int(a.get("max_turns", 40)),
            runtime=runtime,
            command=a.get("command", "claude"),
            web=bool(a.get("web", False)),
        )

    roots = [a.name for a in agents.values() if a.reports_to is None]
    if len(roots) != 1:
        raise ConfigError(
            f"exactly one agent must have no `reports_to` (the top of the org); found {len(roots)}: {roots}"
        )
    for a in agents.values():
        if a.reports_to is not None and a.reports_to not in agents:
            raise ConfigError(f"agents.{a.name}.reports_to = {a.reports_to!r} is not a defined agent")
    for a in agents.values():  # cycle check
        seen, cur = {a.name}, a.reports_to
        while cur is not None:
            if cur in seen:
                raise ConfigError(f"reporting cycle involving {a.name!r}")
            seen.add(cur)
            cur = agents[cur].reports_to

    shell = c.get("shell", "ask")
    if shell not in SHELL_POLICIES:
        raise ConfigError(f"company.shell must be one of {', '.join(SHELL_POLICIES)}")

    return Company(
        name=c.get("name", root.name),
        mission=(c.get("mission") or "").strip(),
        agents=agents,
        root=root,
        budget_usd=float(c.get("budget_usd", 10.0)),
        workspace_dir=c.get("workspace", "workspace"),
        shell=shell,
        check_timeout=int(c.get("check_timeout", 300)),
        max_attempts=int(c.get("max_attempts", 3)),
        git=bool(c.get("git", True)),
        parallel=max(1, int(c.get("parallel", 1))),
        extra={
            k: v
            for k, v in c.items()
            if k
            not in {
                "name",
                "mission",
                "budget_usd",
                "workspace",
                "shell",
                "check_timeout",
                "max_attempts",
                "git",
                "parallel",
            }
        },
    )
