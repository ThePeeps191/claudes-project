# Changelog

## 0.1.0 (unreleased)

- Event-driven company of agents: org chart in `company.toml`, coalesced wake queue, no heartbeats.
- Tasks need a definition of done (checks and/or a reviewer); `submit` runs the checks; repeated failures escalate up the org.
- Budgets per agent and per company, checked before every model call.
- Claude API backend with streaming, adaptive thinking, prompt caching and server-side refusal fallbacks.
- `runtime = "claude-code"`: agents that run as headless Claude Code sessions with tinyco's org tools over MCP.
- `web = true`: server-side web search and fetch.
- Parallel wakes (`tinyco run -j N`), per-agent git commits in the workspace.
- `tinyco dashboard` (local web UI) and `tinyco demo` (scripted run, no API key).
