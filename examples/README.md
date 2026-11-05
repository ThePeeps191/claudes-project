# Example companies

Each directory holds a `company.toml`. To start one, copy it somewhere and give it a goal:

```
cp -r examples/game-studio ~/my-studio && cd ~/my-studio
tinyco goal "A terminal snake game with a high-score file" --check "python -m pytest -q"
tinyco run
```

| Example | Org | Good for |
|---|---|---|
| [`game-studio`](game-studio/company.toml) | director → programmer, playtester | small terminal games with tests |
| [`claude-code-team`](claude-code-team/company.toml) | API tech lead → two Claude Code engineers, reviewer | using your Claude Code login for the hands-on work |
| [`research-desk`](research-desk/company.toml) | editor → two researchers (web), fact-checker | sourced written answers |
| [`api-shop`](api-shop/company.toml) | CTO → backend, docs writer, QA | small HTTP APIs with docs and tests |
