# Changelog

## Unreleased

- Performance: 2.5–3× less overhead per wake (indexed queries, WAL `synchronous=NORMAL`, O(1) duplicate detection, one-query spend totals); `benchmarks/scheduler.py`.
- Briefs include a workspace file tree and the last failed check output; manager briefs summarize subtasks instead of listing every one; agents are told to batch independent tool calls.
- `tinyco cost`: spend by agent, model, goal and task, with prompt-cache hit rates.
- Repository renamed to `tinyco`.

## 0.1.0 (unreleased)

- Event-driven company of agents: org chart in `company.toml`, coalesced wake queue, no heartbeats.
- Tasks need a definition of done (checks and/or a reviewer); `submit` runs the checks; repeated failures escalate up the org.
- Budgets per agent and per company, checked before every model call.
- Claude API backend with streaming, adaptive thinking, prompt caching and server-side refusal fallbacks.
- `runtime = "claude-code"`: agents that run as headless Claude Code sessions with tinyco's org tools over MCP.
- `web = true`: server-side web search and fetch.
- Parallel wakes (`tinyco run -j N`), per-agent git commits in the workspace.
- `tinyco dashboard` (local web UI), `tinyco export` (shareable HTML snapshot) and `tinyco demo` (scripted run, no API key).
- Hardening from an independent review: agents can't write git metadata or tinyco state; git runs without hooks/fsmonitor; dashboard checks Host/Origin; cancels, refusals and timeouts always reach a manager or the human; submissions discarded if the task changed mid-check; nudges reset on progress and agents waiting on a question aren't nudged; single-run lock and crash recovery; input bounds in tools and CLI.
