<h1 align="center">tinyco</h1>

<p align="center">
  <b>Run a tiny company of AI agents.</b><br>
  One <code>pip install</code>, one TOML file, an org chart that actually ships, and nothing billed while it's idle.
</p>

<p align="center">
  <a href="#try-it-in-10-seconds">Try it</a> ·
  <a href="#how-it-works">How it works</a> ·
  <a href="#companytoml">Config</a> ·
  <a href="#keep-it-running-watch-mode-and-routines">Watch mode</a> ·
  <a href="#bring-your-own-agent-claude-code">Claude Code agents</a> ·
  <a href="#the-dashboard">Dashboard</a> ·
  <a href="#safety">Safety</a> ·
  <a href="#faq">FAQ</a>
</p>

<p align="center"><img src="docs/terminal.png" alt="tinyco demo: a CEO sets positioning, commissions competitor research, sends it back for an unsourced price, then runs messaging and a landing page, and hands the launch to the human for approval" width="860"></p>

You hire a few AI agents, give them titles and a boss, and hand the company a goal like *"launch our invoicing app to freelance designers"*. The CEO splits it into workstreams and delegates them in order: research, then messaging, then the landing page. Researchers search the web and cite sources. The marketer writes the positioning and the launch email, and the designer builds the page. Every task carries a **definition of done**: acceptance criteria that a reviewer judges one by one, plus shell checks wherever something can be verified mechanically. Work that doesn't meet them goes back. When there's nothing to do, nothing runs and nothing is billed.

```
pip install git+https://github.com/ThePeeps191/tinyco
tinyco init                    # a launch company: CEO, researcher, marketer, designer
tinyco goal "Launch our invoicing app to freelance designers" \
    -a "Competitor research with a sourced price for every competitor" \
    -a "One-sentence positioning and a launch email under 200 words" \
    -a "A landing page that states the price and has a signup form" \
    --review human
tinyco run
```

## Try it in 10 seconds

No API key needed:

```
pip install git+https://github.com/ThePeeps191/tinyco
tinyco demo
```

`tinyco demo` runs a four-person launch company with the model swapped for a recording. Everything else is real: files get written, check commands run, and the same company rules apply as in a live run. The CEO writes down shared positioning, commissions competitor research and sends it back because one price has no source. Then it runs messaging and a landing page in order and hands the finished launch to *you*: `cd tinyco-demo && tinyco inbox`, then `tinyco approve 1`. (The competitors in the demo are invented; a real run researches real ones.) For a software team instead, run `tinyco demo --code`.

## Run a real company

```
export ANTHROPIC_API_KEY=sk-ant-...
mkdir acme && cd acme
tinyco init                     # company.toml: CEO, market researcher (web), marketing lead, web designer
tinyco goal "Launch our invoicing app to freelance designers" -f brief.md \
    -a "Competitor research with sourced prices" -a "A landing page and a launch email" --review human
tinyco run                      # works until the company is idle, then exits
```

`tinyco run` streams what every agent does. When it returns, either the goal is waiting for your sign-off or something else needs you:

```
tinyco status                   # task tree, spend per agent, anything waiting on you
tinyco inbox                    # questions and reviews for the human
tinyco reply 4 "Target agencies, not freelancers"   # answer an agent; it picks up on the next run
tinyco approve 1                # sign off on the goal (or: tinyco reject 1 "what to change")
```

The deliverables land in `./workspace`: research, copy, pages, plans, code. Templates:

| `tinyco init -t …` | Org | For |
|---|---|---|
| `launch` (default) | CEO → market researcher (web), marketing lead, web designer | taking a product to market |
| `studio` | editor → researcher (web), writer, social lead | publishing on a schedule (pairs with routines) |
| `software` | CEO → engineer, reviewer | building software with tests as checks |
| `research` | editor → researcher, fact-checker | sourced reports |
| `solo` | one agent | trying things out |

More org shapes are in [`examples/`](examples/): a game studio, a Claude Code team, a research desk and an API shop.

### What a goal looks like

Goals are business outcomes, and "done" is whatever you'd check yourself:

```
tinyco goal "Find 20 agencies that could resell our product" \
    -a "20 agencies with name, site, size and why they fit, each with a source" \
    -a "A two-sentence personalized opener for each" --review human

tinyco goal "Write a pricing page that tests $12 vs $19" -f pricing-notes.md \
    -a "Two variants with identical copy except the price" -a "A rationale grounded in the research" \
    --check "test -f site/pricing-a.html -a -f site/pricing-b.html" --review human
```

Acceptance criteria are judged one by one. A reviewer, the human or an agent, must give a pass or fail with a reason for every criterion, and the work only closes when all of them pass. Rejections go back to the assignee with the failing criteria spelled out. Shell checks are optional, for anything a command can verify: tests, a file exists, a page contains the price.

## Why tinyco

[Paperclip](https://github.com/paperclipai/paperclip) showed how many people want to run AI agents as a company, with roles, reporting lines, budgets and tickets. tinyco is the small, opinionated take on that idea. Each design choice below targets a failure that shows up again and again in multi-agent setups:

| Problem | What tinyco does |
|---|---|
| Agents are woken on a timer to look for work, and idle teams keep spending tokens | **No heartbeats.** Agents wake only for events: assigned, subtask finished, review requested, human replied. An idle company makes zero model calls, and `tinyco run` exits. |
| Agents report work as done when it isn't | **Every task needs a definition of done**: acceptance criteria a reviewer must judge one by one, and/or checks that `submit` runs itself. Claims don't close tasks. |
| Every wake-up starts cold and re-reads the whole board | **Briefings.** Each wake gets a short, targeted brief: why you're awake, the task, the chain of goals above it, subtask status, recent activity, and the agent's own notes. |
| Finished work doesn't wake the manager, or bounces around forever | Finishing a subtask **wakes its owner**. Duplicate wake-ups are merged. After `max_attempts` failures the task **escalates up the org chart** with the last output attached. |
| Runs that silently do nothing | A run that ends without finishing, delegating or asking is **nudged**, then escalated. |
| Budgets that only stop agents after they've overspent | Spend is checked **before every model call**, per agent and per company. Paused work resumes when you raise the budget. |
| Six weeks of infrastructure | **One Python package** and one SQLite file. No server, no database to run, no Node. |

## How it works

```mermaid
flowchart LR
    H([human]) -- "goal + criteria" --> CEO
    CEO -- "create_task + criteria" --> W[researcher / marketer / designer]
    W -- submit --> C{checks pass?}
    C -- "no: output back" --> W
    C -- yes --> REV{"reviewer judges<br/>each criterion"}
    REV -- "any fails: verdicts back" --> W
    REV -- all pass --> D([done])
    D -- wakes --> CEO
    CEO -- "submit goal" --> H
```

- **Org chart.** Agents are defined in `company.toml`, each with a title, a manager, a model, an effort level and a budget. You can only delegate *down* the chart.
- **Tasks.** Each task has an assignee, a description and a definition of done: acceptance criteria, which a reviewer judges one by one (by default whoever delegated the task), and/or checks, which are shell commands run from the workspace root. An agent can't create a task without one.
- **Wakes.** A small queue in SQLite. Each wake is one bounded session: the agent gets its brief and its tools, works, and ends its turn. Nothing polls.
- **Tools.** Org tools (`create_task`, `submit`, `review`, `update_task`, `comment`, `ask_human`, `view_task`, `note`) and workspace tools (`list_files`, `read_file`, `write_file`, `edit_file`, `run`). Every agent gets the same tool list on every call, so prompt caching works across the whole company.
- **Memory.** Agents save short notes that appear in their later briefings. Notes marked `shared` go into every agent's briefing, which is how a lead sets conventions such as layout, tools and interfaces once for the whole team. Task comments, summaries and check results carry the rest.
- **Git history.** If the workspace is a git repo (`tinyco init` makes one), every finished task is committed with the agent who did it as the author, so `git log` reads like a team's history.
- **Humans.** Agents can `ask_human`. Goals can require your sign-off (`--review human`), and you can see each criterion when you approve. Escalations that reach the top land in your inbox.

Everything lives in `.tinyco/state.db`, which you can open in any SQLite browser.

## company.toml

```toml
[company]
name = "Acme"
mission = "Ship small, well-tested software that does exactly what was asked."
budget_usd = 10.0         # hard cap for the whole company
budget_period = "total"   # total | day | week | month: when budgets reset
workspace = "workspace"   # where agents read, write and run things
shell = "ask"             # ask | allow | deny
max_attempts = 3          # failed submissions before a task escalates
check_timeout = 300       # seconds per check command
parallel = 1              # agents that may work at the same time
git = true                # commit the workspace when a task is done, authored by the agent

[agents.ceo]
title = "CEO"
effort = "high"           # low | medium | high | xhigh | max
budget_usd = 3.0
instructions = """
Turn each goal into 2-5 concrete subtasks with tests as checks...
"""

[agents.engineer]
title = "Software Engineer"
reports_to = "ceo"
model = "claude-opus-5-5" # any Claude model id
effort = "medium"
web = false               # server-side web search + fetch
runtime = "api"           # api | claude-code
budget_usd = 5.0
max_turns = 40            # model calls per wake-up
```

Any shape of org works, as long as exactly one agent has no `reports_to`. Deeper hierarchies, several specialists, research desks: the `research` template is an editor, a researcher and a fact-checker producing Markdown reports.

Prices for budget accounting live in `tinyco/pricing.py`. Override them per model under `[company.pricing."<model-id>"]` with `input`, `output` and `cache_read` in USD per million tokens.

## Keep it running: watch mode and routines

```
tinyco run --watch
```

In watch mode, `tinyco run` keeps running once the queue is empty. Goals and replies you add from another terminal or the dashboard are picked up within a couple of seconds. While it waits, all it does is check a local SQLite queue: no model calls, no tokens.

Recurring work goes in `company.toml` as routines:

```toml
[[routines]]
name = "Dependency check"
every = "1d"              # 15m, 6h, 1d, 1w
at = "09:00"              # optional local time (whole-day intervals)
goal = "Check for outdated dependencies, upgrade the safe ones, and keep the tests green"
checks = ["python -m pytest -q"]
review = "human"          # optional
```

A routine fires by creating an ordinary goal. If the goal it created last time is still open, it skips that slot instead of stacking up duplicates. `tinyco routines` shows when each one runs next.

For a company that runs for weeks, make budgets reset with `budget_period = "day"`, `"week"` or `"month"` under `[company]`. Company and agent budgets then cap spend within the current period. Work paused when the cap was hit resumes when the next period starts.

## Bring your own agent: Claude Code

Any agent can be a [Claude Code](https://code.claude.com) session instead of a direct API agent:

```toml
[company]
shell = "allow"            # headless Claude Code can't stop to ask, so run this in a container/VM

[agents.engineer]
title = "Software Engineer"
reports_to = "ceo"
runtime = "claude-code"    # each wake runs `claude -p` in the workspace
model = "claude-sonnet-5-5"
budget_usd = 5.0
```

Each wake runs `claude -p <briefing> --output-format json` in the workspace. Claude Code does the hands-on work with its own tools (editing, shell, search) and gets tinyco's org tools (`submit`, `create_task`, `review`, `ask_human`, ...) from a small built-in MCP server (`--mcp-config ... --strict-mcp-config`). The rules don't change: checks still decide when a task is done, and failures still escalate. The agent's limits are passed through as `--max-turns` and `--max-budget-usd`, and the run's `total_cost_usd` (Claude Code's own estimate) counts toward tinyco budgets. Claude Code uses whatever login it already has. Mixed companies work too, for example an API CEO directing Claude Code engineers.

## Web access

Set `web = true` on an agent to give it Claude's server-side web search and web fetch tools. The `research` template turns it on for the researcher and the fact-checker. Each search adds $0.01 to the agent's spend, at the list price of $10 per 1,000 searches.

## The dashboard

```
tinyco dashboard
```

<p align="center"><img src="docs/dashboard.png" alt="tinyco dashboard showing the org chart with per-agent spend, the task tree, and a live activity feed" width="860"></p>

A local web page (standard library only, bound to 127.0.0.1) that follows the company live while `tinyco run` works in a terminal. It shows the org chart with each agent's spend and current task, the task tree with cost per subtree, a live activity feed, and a drawer for each task with its checks, submissions and history. You can create goals, answer questions, and approve or reject work from it.

### Share a run

```
tinyco export -o run.html
```

`tinyco export` writes the dashboard as a single self-contained HTML file with the whole run embedded: org chart, task tree, every event, and cost. It's read-only, has no server behind it, and opens from disk or any static host. It contains your task descriptions, commands and agent messages, so read it before you share it.

## CLI

| Command | |
|---|---|
| `tinyco init [-t launch\|studio\|software\|research\|solo]` | create `company.toml` |
| `tinyco goal "..." [-a CRITERION]... [--check CMD]... [--review human\|AGENT] [-d DETAILS] [-f SPEC.md]` | give the company a goal |
| `tinyco run [-j N] [--watch] [--yes] [--budget USD] [--max-wakes N] [-v]` | work until idle (`-j`: agents working at once; `--watch`: stay up, run routines) |
| `tinyco routines` | list routines and when they run next |
| `tinyco status` | task tree, spend, queue, inbox |
| `tinyco inbox` / `reply ID TEXT` / `approve ID` / `reject ID NOTES` | the human's side |
| `tinyco task ID` / `log [--task ID]` / `org` | inspect |
| `tinyco cancel ID` | cancel a task and its open subtasks |
| `tinyco reopen ID ["guidance"]` | send a failed, blocked or finished task back to its assignee with a clean slate |
| `tinyco dashboard [--port N]` | live web dashboard |
| `tinyco export [-o FILE]` | self-contained HTML snapshot of the run, for sharing |
| `tinyco cost` | spend by agent, model, goal and task, with prompt-cache hit rates |
| `tinyco demo [PATH] [--fast] [--code]` | scripted product launch (or software build), no API key |

`-C DIR` before any command points at a company in another directory. Otherwise tinyco searches upward for `company.toml`.

## Safety

Agents write files and run commands, so treat a run like letting a contractor use your terminal.

- `shell = "ask"` (the default) asks you before every command and every agent-written check. `allow` runs them without asking; use it in a container or VM, or pass `tinyco run --yes` for one run. `deny` turns commands off entirely.
- Checks you write yourself (`tinyco goal --check`) always run. Checks written by agents follow the shell policy.
- File tools are confined to the workspace directory. Agents can't write git metadata (`.git/`, `.gitattributes`, `.gitmodules`) or tinyco's own files, and tinyco runs git with hooks and fsmonitor disabled, so the workspace can't make git run code.
- Commands run without `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` in their environment.
- Budgets are hard caps checked before each model call.
- The dashboard only answers requests addressed to `127.0.0.1`/`localhost` (which blocks DNS rebinding), and write requests need a custom header, which blocks cross-site forms.
- One `tinyco run` per company at a time, enforced with a lock file.
- Agents with `runtime = "claude-code"` run Claude Code with `--permission-mode bypassPermissions`, so they only run when `shell = "allow"` (or `tinyco run --yes`). Run those companies in a container or VM.

## Cost

The biggest lever is the event-driven design: agents that have nothing to do aren't called. Beyond that:

- **Prompt caching** is on. The system prompt and tool list are identical across calls and each session's history is append-only, so most of the input on later turns is billed at the cached rate.
- **Effort per agent.** Leads at `high`, hands-on roles at `medium` is a sensible start. Lower effort means fewer and terser tool calls.
- **Bounded sessions.** Each wake is capped at `max_turns` calls, and each agent starts from a brief instead of the whole history.

- **Briefs that replace lookups.** Every wake brief includes a compact file tree of the workspace and, after a failed submission, the failing checks' output, so agents don't spend model calls rediscovering either. Managers get a bounded summary of their subtasks (counts, open ones, recent completions with summaries) rather than one line per subtask, however big the tree grows.
- **Batched tool calls.** Agents are told to make independent tool calls in the same turn, which means fewer round trips that re-send the conversation.

Your actual spend depends on the goal and the models. `tinyco cost` breaks it down by agent, model, goal and task, and shows how much of each agent's prompt was served from the cache.

## Performance

tinyco's own overhead per wake (scheduling, SQLite, the brief, running a trivial check) is a few milliseconds. Model calls take seconds, so they're what you're waiting on. Measured with `benchmarks/scheduler.py`: a CEO delegates N subtasks to 10 engineers, using a zero-latency scripted model so only tinyco's own time is counted.

| subtasks | wakes | total | per wake |
|---:|---:|---:|---:|
| 50 | 52 | 0.15 s | 3.0 ms |
| 300 | 302 | 1.1 s | 3.7 ms |
| 1000 | 1002 | 4.7 s | 4.7 ms |

Fifty finished subtasks still wake the CEO only once, because wakes for the same agent and task are merged. Most of what remains is starting a process for each check command. Run `python benchmarks/scheduler.py --tasks 300 --profile` to measure on your machine.

## FAQ

**Which models?** Claude. API agents use the official `anthropic` SDK, with streaming, adaptive thinking, prompt caching and server-side refusal fallbacks. Any Claude model id works per agent. Agents with `runtime = "claude-code"` run through Claude Code instead.

**Do agents run in parallel?** Yes, with `tinyco run -j 3` or `parallel = 3` under `[company]`. Different agents work at the same time, and a single agent never handles two wake-ups at once. The default is 1: runs are easier to follow, and agents can't edit the same files at the same moment. With parallel runs, spend can pass a budget by up to one model call per running agent before the cap stops further calls.

**Can an agent game its own checks?** An engineer could, in principle, edit the tests it's checked against. Give important tasks a reviewer, write top-level checks yourself (`tinyco goal --check`), and keep goal-level tests outside what the engineer is told to change.

**Where's my data?** In `company.toml`, `.tinyco/state.db` and `workspace/`, all in your directory. Apart from calls to the Claude API, nothing leaves your machine.

**What happens if I Ctrl+C?** The current wake goes back on the queue. `tinyco run` picks up where it stopped.

## Status and roadmap

tinyco is alpha. The core loop, the rules and the dashboard are covered by an offline test suite, including a test that drives the real `anthropic` SDK over a mock transport to check the exact requests. Next:

- [ ] More runtimes on the same MCP bridge (other coding-agent CLIs)
- [ ] A git branch per task, merged when it's done
- [ ] More templates

Ideas and PRs welcome.

## Development

```
git clone https://github.com/ThePeeps191/tinyco && cd tinyco
pip install -e ".[dev]"
pytest -q && ruff check .
```

The test suite runs whole companies against `tinyco.scripted.ScriptedModel`, so no API key is needed.

## License

MIT
