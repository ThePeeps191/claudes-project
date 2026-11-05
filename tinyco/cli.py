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
    print("Next:")
    print('  tinyco goal "Build a CLI that converts CSV files to JSON" --check "python -m pytest -q"')
    print("  tinyco run")
    return 0


def cmd_goal(args) -> int:
    company, store, office = open_company(args)
    t = office.start_goal(args.title, args.description or "", args.check, args.review)
    style = Style(use_color())
    print(f"{style.bold(t.ref)} {t.title} → {style.agent(t.assignee)}")
    if not t.checks and not t.reviewer:
        print(style.dim("tip: add --check 'cmd' (e.g. a test command) so 'done' means something you can verify"))
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
    if not store.pending_wakes() and not any(
        t.status == "blocked" and (t.blocked_on or "").startswith(("budget:", "model error:")) for t in store.tasks()
    ):
        print(style.dim('Nothing to do: no pending work. Add a goal with: tinyco goal "..."'))
        return 0
    started = time.time()
    try:
        summary = Runner(office, ClaudeModel()).run(max_wakes=args.max_wakes, parallel=args.jobs or company.parallel)
    except KeyboardInterrupt:
        print(style.yellow("\ninterrupted; pending work is saved. Resume with: tinyco run"))
        return 130
    took = time.time() - started
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


def cmd_status(args) -> int:
    company, store, office = open_company(args)
    style = Style(use_color())
    print(style.bold(company.name) + (style.dim(f" — {company.mission}") if company.mission else ""))
    print()
    print(board(office, style))
    print()
    spend = store.spend_by_agent()
    total = store.spent()
    parts = []
    for name, a in company.agents.items():
        used = spend.get(name, {}).get("usd", 0.0) or 0.0
        cap = f"/${a.budget_usd:.2f}" if a.budget_usd is not None else ""
        parts.append(f"{style.agent(name)} ${used:.2f}{cap}")
    print("spend: " + " · ".join(parts) + f"  {style.dim(f'(total ${total:.2f} of ${company.budget_usd:.2f})')}")
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
            print(style.dim(f'   tinyco approve {t.id}   |   tinyco reject {t.id} "what to change"'))
        else:
            print(f"{style.bold(t.ref)} {t.title}  {style.yellow((t.blocked_on or '').removeprefix('human: '))}")
            print(style.dim(f'   tinyco reply {t.id} "your answer"'))
    return 0


def _tid(v: str) -> int:
    return int(str(v).lstrip("Tt"))


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
        office, _ = run_demo(target, delay=0 if args.fast else 0.35, force=args.force, feed=Feed(style))
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
    print("  ls workspace/                     # the code the agents wrote")
    print()
    print('Run a real company: tinyco init && tinyco goal "..." && tinyco run')
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
    s.add_argument("--template", "-t", choices=sorted(TEMPLATES), default="startup")
    s.add_argument("--name", help="company name")
    s.add_argument("--force", action="store_true", help="overwrite an existing company.toml")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("goal", help="give the company a goal")
    s.add_argument("title")
    s.add_argument("--description", "-d", help="more detail for the goal")
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
    s.add_argument("id")
    s.set_defaults(fn=cmd_task)

    s = sub.add_parser("inbox", help="questions and reviews waiting on you")
    s.set_defaults(fn=cmd_inbox)

    s = sub.add_parser("reply", help="answer an agent's question")
    s.add_argument("id")
    s.add_argument("text")
    s.set_defaults(fn=cmd_reply)

    s = sub.add_parser("approve", help="approve work waiting on your review")
    s.add_argument("id")
    s.add_argument("--notes", default="")
    s.set_defaults(fn=cmd_approve)

    s = sub.add_parser("reject", help="send work back with notes")
    s.add_argument("id")
    s.add_argument("notes")
    s.set_defaults(fn=cmd_reject)

    s = sub.add_parser("cancel", help="cancel a task and its open subtasks")
    s.add_argument("id")
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
    s.set_defaults(fn=cmd_demo)
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
