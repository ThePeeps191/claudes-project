"""Command-line interface."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import __version__
from .config import CONFIG_NAME, Company, ConfigError, load
from .office import Office, OfficeError
from .store import Store
from .templates import TEMPLATES
from .ui import Feed, Style, board, use_color


def open_company(args) -> tuple[Company, Store, Office]:
    company = load(Path(args.dir) if getattr(args, "dir", None) else None)
    store = Store(company.state_dir / "state.db")
    return company, store, Office(company, store)


def make_approver(style: Style):
    import threading

    always: set[str] = set()
    lock = threading.Lock()  # agents running in parallel take turns asking

    def approve(kind: str, detail: str) -> bool:
        if not sys.stdin.isatty():
            return False
        with lock:
            return _ask(kind, detail)

    def _ask(kind: str, detail: str) -> bool:
        if "all" in always:
            return True
        label = "check" if kind == "check" else "command"
        print(f"\n{style.yellow('?')} An agent wants to run a {label}:\n    {style.bold(detail)}")
        while True:
            try:
                ans = input("  run it? [y]es / [n]o / [a]lways for this run: ").strip().lower()
            except EOFError:
                return False
            if ans in ("y", "yes"):
                return True
            if ans in ("n", "no", ""):
                return False
            if ans in ("a", "always"):
                always.add("all")
                return True

    return approve


# -- commands -----------------------------------------------------------------


def cmd_init(args) -> int:
    root = Path(args.dir or ".").resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / CONFIG_NAME
    if path.exists() and not args.force:
        print(f"{path} already exists (use --force to overwrite)", file=sys.stderr)
        return 1
    template = TEMPLATES[args.template]
    path.write_text(template.replace("{name}", args.name or root.name.replace("-", " ").title()), encoding="utf-8")
    (root / "workspace").mkdir(exist_ok=True)
    from . import gitlog

    git_ready = gitlog.init(root / "workspace")
    gi = root / ".gitignore"
    if not gi.exists():
        gi.write_text(".tinyco/\n", encoding="utf-8")
    print(
        f"Created {path.relative_to(Path.cwd()) if path.is_relative_to(Path.cwd()) else path} ({args.template} template)."
    )
    if git_ready:
        print("Initialized a git repo in workspace/: finished tasks are committed with the agent as author.")
    print("Next, give the company a goal and say what done means:")
    if args.template in ("software", "startup", "solo"):
        print('  tinyco goal "Add CSV export to the reports page" --check "python -m pytest -q"')
    else:
        print('  tinyco goal "Launch our invoicing app to freelance designers" \\')
        print('      -a "Competitor research with sourced prices" -a "A landing page and a launch email" \\')
        print("      --review human")
    print("  tinyco run")
    return 0


def cmd_goal(args) -> int:
    company, store, office = open_company(args)
    description = args.description or ""
    if args.file:
        try:
            description = (description + "\n\n" if description else "") + Path(args.file).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            print(f"tinyco: can't read {args.file}: {e}", file=sys.stderr)
            return 1
    try:
        t = office.start_goal(args.title, description, args.check, args.review, args.criteria)
    except OfficeError as e:
        print(f"tinyco: {e}", file=sys.stderr)
        return 1
    style = Style(use_color())
    print(f"{style.bold(t.ref)} {t.title} → {style.agent(t.assignee)}")
    if not t.checks and not t.reviewer:
        print(
            style.dim(
                'tip: say what "done" means with --criteria "..." --review human (or an agent), '
                "and/or --check 'cmd' for things a command can verify"
            )
        )
    print(style.dim("Run the company with: tinyco run"))
    return 0


def cmd_run(args) -> int:
    from .llm import ClaudeModel
    from .runner import Runner

    company, store, office = open_company(args)
    if args.yes:
        company.shell = "allow"
    if args.budget is not None:
        company.budget_usd = args.budget
    style = Style(use_color())
    office._approve = make_approver(style)
    if not args.quiet:
        store.listeners.append(Feed(style, verbose=args.verbose))
    cc = [a.name for a in company.agents.values() if a.runtime == "claude-code"]
    if cc and company.shell != "allow":
        print(
            f"tinyco: {', '.join(cc)} run on Claude Code, which can't stop to ask about commands. "
            'Set shell = "allow" in company.toml (in a container or VM) or run with --yes.',
            file=sys.stderr,
        )
        return 1
    started = time.time()
    runner = Runner(office, ClaudeModel())
    parallel = args.jobs or company.parallel
    if args.watch:
        if args.max_wakes is not None:
            print("tinyco: --max-wakes can't be combined with --watch", file=sys.stderr)
            return 1
        return _watch(runner, company, style, parallel, args.poll)
    try:
        summary = runner.run(max_wakes=args.max_wakes, parallel=parallel)
    except KeyboardInterrupt:
        print(style.yellow("\ninterrupted; pending work is saved. Resume with: tinyco run"))
        return 130
    took = time.time() - started
    if summary.stopped == "locked":
        print(style.red(f"tinyco: {summary.error}"), file=sys.stderr)
        return 1
    if summary.wakes == 0 and summary.stopped == "idle":
        print(style.dim('Nothing to do: no pending work. Add a goal with: tinyco goal "..."'))
        return 0
    print()
    print(board(office, style))
    print()
    line = f"{summary.wakes} wake-ups · {summary.turns} model calls · ${summary.usd:.2f} · {took:.0f}s"
    print(style.dim(line))
    if summary.stopped == "fatal":
        print(style.red(f"stopped: {summary.error}"))
        return 2
    if summary.stopped == "budget":
        print(style.red("stopped: company budget used up (raise budget_usd in company.toml, then tinyco run)"))
        return 3
    inbox = office.inbox()
    if inbox:
        print(style.yellow(f"{len(inbox)} item(s) need you: tinyco inbox"))
    return 0


def _watch(runner, company, style, parallel: int, poll: float) -> int:
    n = len(company.routines)
    print(
        style.dim(
            f"Watching {company.name}: new goals and replies are picked up as they arrive"
            + (f", {n} routine{'s' if n != 1 else ''} scheduled" if n else "")
            + ". Idle costs nothing. Ctrl+C to stop."
        )
    )
    try:
        summary = runner.watch(parallel=parallel, poll=poll)
    except KeyboardInterrupt:
        print(style.yellow("\nstopped watching; pending work is saved."))
        return 0
    if summary.stopped == "locked":
        print(style.red(f"tinyco: {summary.error}"), file=sys.stderr)
        return 1
    if summary.stopped in ("fatal", "budget"):
        print(style.red(f"stopped: {summary.error or 'company budget used up'}"))
        return 2
    return 0


def cmd_routines(args) -> int:
    import datetime as dt

    from .routines import Scheduler

    company, store, office = open_company(args)
    style = Style(use_color())
    if not company.routines:
        print(style.dim("No routines. Add [[routines]] entries to company.toml (see the README)."))
        return 0
    for r in Scheduler(office, company.routines).status():
        every = _human_interval(r["every"]) + (f" at {r['at'][0]:02d}:{r['at'][1]:02d}" if r["at"] else "")
        last = dt.datetime.fromtimestamp(r["last_run"]).strftime("%Y-%m-%d %H:%M") if r["last_run"] else "never"
        nxt = dt.datetime.fromtimestamp(r["next"]).strftime("%Y-%m-%d %H:%M")
        task = f" (last goal T{r['last_task']})" if r["last_task"] else ""
        print(f"{style.bold(r['name'])}: {r['goal']}")
        print(style.dim(f"   every {every} · last run {last}{task} · next {nxt}"))
    print(style.dim("Routines fire while `tinyco run --watch` is running."))
    return 0


def _human_interval(seconds: int) -> str:
    for unit, n in (("w", 604800), ("d", 86400), ("h", 3600), ("m", 60)):
        if seconds % n == 0:
            return f"{seconds // n}{unit}"
    return f"{seconds}s"


def cmd_status(args) -> int:
    company, store, office = open_company(args)
    style = Style(use_color())
    print(style.bold(company.name) + (style.dim(f" — {company.mission}") if company.mission else ""))
    print()
    print(board(office, style))
    print()
    since = company.period_start()
    spend = store.spend_by_agent(since=since)
    total = store.spent(since=since)
    parts = []
    for name, a in company.agents.items():
        used = spend.get(name, {}).get("usd", 0.0) or 0.0
        cap = f"/${a.budget_usd:.2f}" if a.budget_usd is not None else ""
        parts.append(f"{style.agent(name)} ${used:.2f}{cap}")
    period = company.period_label
    print(
        f"spend{period}: " + " · ".join(parts) + f"  {style.dim(f'(total ${total:.2f} of ${company.budget_usd:.2f})')}"
    )
    pending = store.pending_wakes()
    if pending:
        who = ", ".join(sorted({w.agent for w in pending}))
        print(style.dim(f"queued: {len(pending)} wake-up(s) for {who} — run: tinyco run"))
    inbox = office.inbox()
    if inbox:
        print(style.yellow(f"needs you: {len(inbox)} item(s) — tinyco inbox"))
    return 0


def cmd_log(args) -> int:
    company, store, office = open_company(args)
    feed = Feed(Style(use_color()), verbose=args.verbose)
    for e in store.events(task_id=args.task, limit=args.n):
        line = feed.render(e)
        if line:
            print(line)
    return 0


def cmd_task(args) -> int:
    from .briefing import describe_task

    company, store, office = open_company(args)
    try:
        t = office.get(_tid(args.id))
    except OfficeError as e:
        print(e, file=sys.stderr)
        return 1
    print(describe_task(office, t))
    print(f"\nSpend on this task and its subtasks: ${store.subtree_spend(t.id):.2f}")
    return 0


def cmd_inbox(args) -> int:
    company, store, office = open_company(args)
    style = Style(use_color())
    items = office.inbox()
    if not items:
        print(style.dim("Inbox empty: nothing is waiting on you."))
        return 0
    for t in items:
        if t.status == "in_review":
            print(f"{style.bold(t.ref)} {t.title}  {style.yellow('awaiting your review')}")
            if t.summary:
                print(f"   summary: {t.summary}")
            for i, c in enumerate(t.criteria, 1):
                print(f"   {i}. {c}")
            print(style.dim(f'   tinyco approve {t.id}   |   tinyco reject {t.id} "what to change"'))
        else:
            print(f"{style.bold(t.ref)} {t.title}  {style.yellow((t.blocked_on or '').removeprefix('human: '))}")
            print(style.dim(f'   tinyco reply {t.id} "your answer"'))
    return 0


def _tid(v) -> int:
    """Parse a task id like 7 or T7 (argparse type)."""
    s = str(v).strip().lstrip("Tt")
    if not s.isdigit() or not 0 < int(s) < 2**63:
        raise argparse.ArgumentTypeError(f"not a task id: {v!r}")
    return int(s)


def _act(args, fn) -> int:
    company, store, office = open_company(args)
    try:
        print(fn(office))
    except OfficeError as e:
        print(e, file=sys.stderr)
        return 1
    print(Style(use_color()).dim("Continue with: tinyco run"))
    return 0


def cmd_reply(args) -> int:
    return _act(args, lambda o: o.reply(_tid(args.id), args.text))


def cmd_approve(args) -> int:
    return _act(args, lambda o: o.human_review(_tid(args.id), True, args.notes or ""))


def cmd_reject(args) -> int:
    return _act(args, lambda o: o.human_review(_tid(args.id), False, args.notes))


def cmd_reopen(args) -> int:
    return _act(args, lambda o: o.reopen(args.id, args.guidance or ""))


def cmd_cancel(args) -> int:
    return _act(args, lambda o: o.update_task("human", _tid(args.id), cancel=True))


def cmd_org(args) -> int:
    company = load(Path(args.dir) if args.dir else None)
    style = Style(use_color())
    for line in company.org_chart().splitlines():
        name = line.strip()[2:].split(" ")[0]
        a = company.agents[name]
        print(f"{line}  {style.dim(f'{a.model} · effort {a.effort}')}")
    return 0


def cmd_dashboard(args) -> int:
    import webbrowser

    from .dashboard import serve

    company = load(Path(args.dir) if args.dir else None)
    try:
        server = serve(company, port=args.port)
    except OSError as e:
        print(f"tinyco: can't listen on port {args.port}: {e}", file=sys.stderr)
        return 1
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Dashboard for {company.name}: {url}  (Ctrl+C to stop)")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def cmd_demo(args) -> int:
    from .demo import run_demo

    style = Style(use_color())
    target = Path(args.path)
    print(style.bold("tinyco demo") + style.dim(" — a scripted run: the model is a recording, so no API key is needed"))
    print(style.dim("and nothing is billed. Everything else is real: files are written, commands and checks run,"))
    print(style.dim("and the company rules apply exactly as they do with Claude.\n"))
    try:
        office, _ = run_demo(
            target,
            delay=0 if args.fast else 0.35,
            force=args.force,
            feed=Feed(style),
            kind="code" if args.code else "launch",
        )
    except FileExistsError as e:
        print(f"tinyco: {e}", file=sys.stderr)
        return 1
    print()
    print(board(office, style))
    print()
    rel = target if not target.is_absolute() else target
    print("Look around:")
    print(f"  cd {rel} && tinyco status       # the board")
    print("  tinyco dashboard                  # the same company in your browser")
    print("  ls workspace/                     # what the agents made")
    if office.inbox():
        print(f"  tinyco inbox && tinyco approve {office.inbox()[0].id}   # the goal is waiting for you")
    print()
    print('Run a real company: tinyco init && tinyco goal "..." --review human && tinyco run')
    return 0


def cmd_cost(args) -> int:
    company, store, office = open_company(args)
    style = Style(use_color())
    total = store.spent()
    current = store.spent(since=company.period_start())
    if company.budget_period == "total":
        print(style.bold(f"{company.name}: ${total:.2f} of ${company.budget_usd:.2f} budget"))
    else:
        print(
            style.bold(
                f"{company.name}: ${current:.2f} of ${company.budget_usd:.2f}{company.period_label} "
                f"(${total:.2f} all time; the tables below are all-time)"
            )
        )
    if not total:
        print(style.dim("No model spend yet."))
        return 0

    def hit_rate(r: dict) -> str:
        prompt = (r["input"] or 0) + (r["cache_read"] or 0) + (r["cache_write"] or 0)
        return f"{100 * (r['cache_read'] or 0) / prompt:.0f}%" if prompt else "-"

    def table(title: str, rows: list[dict], key: str) -> None:
        print()
        print(style.bold(title))
        print(
            style.dim(
                f"  {key:<28} {'calls':>6} {'input':>10} {'cached':>10} {'output':>9} {'cache hit':>9} {'usd':>9}"
            )
        )
        for r in rows:
            print(
                f"  {str(r['key'])[:28]:<28} {r['calls']:>6} {r['input'] or 0:>10,} {r['cache_read'] or 0:>10,} "
                f"{r['output'] or 0:>9,} {hit_rate(r):>9} {r['usd'] or 0:>9.2f}"
            )

    table("By agent", store.spend_breakdown("agent"), "agent")
    table("By model", store.spend_breakdown("model"), "model")

    roots = store.roots()
    if roots:
        print()
        print(style.bold("By goal"))
        for t in roots:
            print(f"  {t.ref:<6} {t.title[:52]:<52} {t.status:<12} ${store.subtree_spend(t.id):.2f}")
    per_task = sorted(store.spend_by_task().items(), key=lambda kv: -kv[1])[:5]
    if per_task:
        print()
        print(style.bold("Most expensive tasks (own spend, excluding subtasks)"))
        for tid, usd in per_task:
            t = store.task(tid)
            if t:
                print(f"  {t.ref:<6} {t.title[:52]:<52} {t.assignee:<12} ${usd:.2f}")
    print()
    print(style.dim("Cache hit = share of prompt tokens served from the prompt cache (billed at a fraction of input)."))
    return 0


def cmd_export(args) -> int:
    from .export import render

    company, store, office = open_company(args)
    out = Path(args.output or f"tinyco-{company.name.lower().replace(' ', '-')}.html")
    out.write_text(render(office), encoding="utf-8")
    style = Style(use_color())
    print(f"Wrote {out} ({out.stat().st_size // 1024} KB): open it in a browser, or host it anywhere static.")
    print(style.dim("It includes task descriptions, commands and agent messages; review it before sharing."))
    return 0


def cmd_mcp_serve(args) -> int:
    from .mcp import Server, serve_stdio

    company, store, office = open_company(args)
    if args.allow_shell:
        company.shell = "allow"
    try:
        server = Server(office, args.agent, _tid(args.task))
    except OfficeError as e:
        print(f"tinyco: {e}", file=sys.stderr)
        return 1
    if args.agent not in company.agents:
        print(f"tinyco: unknown agent {args.agent!r}", file=sys.stderr)
        return 1
    serve_stdio(server)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tinyco",
        description="Run a tiny company of AI agents: an org chart, tasks with checks, and nothing billed while idle.",
    )
    p.add_argument("--version", action="version", version=f"tinyco {__version__}")
    p.add_argument("-C", "--dir", help="company directory (default: search upward from the current directory)")
    sub = p.add_subparsers(dest="cmd", metavar="command")

    s = sub.add_parser("init", help="create company.toml from a template")
    s.add_argument(
        "--template",
        "-t",
        choices=sorted(TEMPLATES),
        default="launch",
        help="launch (default): CEO, researcher, marketer, designer; studio: content; "
        "software: CEO, engineer, reviewer; solo; research",
    )
    s.add_argument("--name", help="company name")
    s.add_argument("--force", action="store_true", help="overwrite an existing company.toml")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("goal", help="give the company a goal")
    s.add_argument("title")
    s.add_argument("--description", "-d", help="more detail for the goal")
    s.add_argument("--file", "-f", metavar="PATH", help="read the goal's details from a file (e.g. a spec)")
    s.add_argument(
        "--criteria",
        "-a",
        action="append",
        default=[],
        metavar="TEXT",
        help="an acceptance criterion the reviewer must judge (repeatable; needs --review)",
    )
    s.add_argument(
        "--check",
        "-c",
        action="append",
        default=[],
        metavar="CMD",
        help="command that must exit 0 for the goal to count as done (repeatable)",
    )
    s.add_argument("--review", metavar="WHO", help="who must approve the result: 'human' or an agent name")
    s.set_defaults(fn=cmd_goal)

    s = sub.add_parser("run", help="work through pending wake-ups until the company is idle")
    s.add_argument("--yes", "-y", action="store_true", help="allow shell commands without asking (use in a sandbox)")
    s.add_argument("--max-wakes", type=int, help="stop after this many wake-ups")
    s.add_argument(
        "--watch",
        "-w",
        action="store_true",
        help="stay up: pick up new goals and replies as they arrive, and run routines",
    )
    s.add_argument("--poll", type=float, default=2.0, help=argparse.SUPPRESS)
    s.add_argument("--jobs", "-j", type=int, help="how many agents may work at once (default: company.parallel or 1)")
    s.add_argument("--budget", type=float, help="override the company budget (USD) for this run")
    s.add_argument("--verbose", "-v", action="store_true", help="show thinking summaries and every tool call")
    s.add_argument("--quiet", "-q", action="store_true", help="don't stream events")
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser("status", help="show the task board, spend and anything waiting on you")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("log", help="show recent events")
    s.add_argument("-n", type=int, default=60)
    s.add_argument("--task", type=_tid)
    s.add_argument("--verbose", "-v", action="store_true")
    s.set_defaults(fn=cmd_log)

    s = sub.add_parser("task", help="show one task in detail")
    s.add_argument("id", type=_tid)
    s.set_defaults(fn=cmd_task)

    s = sub.add_parser("inbox", help="questions and reviews waiting on you")
    s.set_defaults(fn=cmd_inbox)

    s = sub.add_parser("reply", help="answer an agent's question")
    s.add_argument("id", type=_tid)
    s.add_argument("text")
    s.set_defaults(fn=cmd_reply)

    s = sub.add_parser("approve", help="approve work waiting on your review")
    s.add_argument("id", type=_tid)
    s.add_argument("--notes", default="")
    s.set_defaults(fn=cmd_approve)

    s = sub.add_parser("reject", help="send work back with notes")
    s.add_argument("id", type=_tid)
    s.add_argument("notes")
    s.set_defaults(fn=cmd_reject)

    s = sub.add_parser("reopen", help="send a failed, blocked or finished task back to its assignee")
    s.add_argument("id", type=_tid)
    s.add_argument("guidance", nargs="?", help="what to do differently this time")
    s.set_defaults(fn=cmd_reopen)

    s = sub.add_parser("cancel", help="cancel a task and its open subtasks")
    s.add_argument("id", type=_tid)
    s.set_defaults(fn=cmd_cancel)

    s = sub.add_parser("org", help="show the org chart")
    s.set_defaults(fn=cmd_org)

    s = sub.add_parser("dashboard", help="open a live dashboard in your browser")
    s.add_argument("--port", type=int, default=8321)
    s.add_argument("--no-open", action="store_true", help="don't open a browser")
    s.set_defaults(fn=cmd_dashboard)

    s = sub.add_parser("demo", help="watch a scripted company build something (no API key needed)")
    s.add_argument("path", nargs="?", default="tinyco-demo", help="where to create the demo company")
    s.add_argument("--fast", action="store_true", help="no pauses")
    s.add_argument("--force", action="store_true", help="reuse a non-empty directory")
    s.add_argument("--code", action="store_true", help="the software demo instead of the product launch")
    s.set_defaults(fn=cmd_demo)

    s = sub.add_parser("routines", help="list scheduled routines and when they run next")
    s.set_defaults(fn=cmd_routines)

    s = sub.add_parser("cost", help="spend by agent, model, goal and task, with prompt-cache hit rates")
    s.set_defaults(fn=cmd_cost)

    s = sub.add_parser("export", help="write a self-contained HTML snapshot of the company's run")
    s.add_argument("-o", "--output", help="output file (default: tinyco-<company>.html)")
    s.set_defaults(fn=cmd_export)

    # Internal: the org-tools MCP server that external agent runtimes connect to.
    s = sub.add_parser("mcp-serve")
    s.add_argument("--agent", required=True)
    s.add_argument("--task", required=True, type=_tid)
    s.add_argument("--allow-shell", action="store_true")
    s.set_defaults(fn=cmd_mcp_serve)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "fn", None):
        parser.print_help()
        return 0
    try:
        return args.fn(args)
    except ConfigError as e:
        print(f"tinyco: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
