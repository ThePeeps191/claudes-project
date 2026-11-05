# Changelog

## Unreleased

- Performance: 2.5–3× less overhead per wake (indexed queries, WAL `synchronous=NORMAL`, O(1) duplicate detection, one-query spend totals); `benchmarks/scheduler.py`.
- Briefs include a workspace file tree and the last failed check output; manager briefs summarize subtasks instead of listing every one; agents are told to batch independent tool calls.
- `tinyco cost`: spend by agent, model, goal and task, with prompt-cache hit rates.
- `tinyco run --watch`, `[[routines]]` (recurring goals, `every`/`at`) and `tinyco routines`.
- `budget_period` (day/week/month) so budgets reset for long-running companies.
- Shared company notes (`note(..., shared=true)`) in every agent's briefing; notes capped at 600 characters.
- `tinyco reopen ID ["guidance"]` and `tinyco goal -f spec.md`.
- Fixes from a second independent review: routines keep their schedule after late fires (no weekday drift or double fires), routine reviewers/checks validated at load, reopen resets stall counting and needs an in-progress parent, atomic and concurrency-safe database migration with fresh index names, period-aware spend in briefs/status/cost/dashboard, watch honours stop mid-burst and wakes at budget-period boundaries and for API-error retries.
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
