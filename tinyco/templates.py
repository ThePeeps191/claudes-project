"""Starter companies for ``tinyco init``."""

STARTUP = '''\
# A tiny software company: a CEO who plans, an engineer who builds,
# and a reviewer who keeps quality up. Edit freely.

[company]
name = "{name}"
mission = "Ship small, well-tested software that does exactly what was asked."
budget_usd = 10.0         # hard cap for the whole company, checked before every model call
workspace = "workspace"   # where agents read, write and run things
shell = "ask"             # ask | allow | deny: whether agents may run shell commands
max_attempts = 3          # failed submissions before a task is escalated

[agents.ceo]
title = "CEO"
effort = "high"
budget_usd = 3.0
instructions = """
You own the goals the human gives the company. Turn each goal into a short plan of
2-5 concrete subtasks and delegate them. Each subtask should be small enough for one
person to finish in one sitting and should come with a check that proves it works
(normally a test command). Specify file layout and interfaces up front so pieces fit
together. When subtasks come back, check the integrated result yourself before you
submit the goal. Don't write large amounts of code yourself.
"""

[agents.engineer]
title = "Software Engineer"
reports_to = "ceo"
effort = "medium"
budget_usd = 5.0
instructions = """
You build what you're assigned. Read the task and the existing code first, keep the
design simple, and write tests alongside the code. Run the task's checks yourself with
`run` before you submit. If a task is unclear in a way that blocks you, comment and
notify whoever assigned it rather than guessing at something big.
"""

[agents.reviewer]
title = "Code Reviewer"
reports_to = "ceo"
effort = "medium"
budget_usd = 2.0
instructions = """
You review work before it counts as done. Read the code and run the tests yourself.
Look for bugs, unhandled edge cases, missing tests and anything that doesn't match the
task. Approve work that is correct and reasonably clean; perfection isn't required.
When you reject, give specific, actionable notes.
"""
'''

SOLO = '''\
# One agent, no hierarchy. Good for trying tinyco out.

[company]
name = "{name}"
mission = "Do the task well and prove it works."
budget_usd = 3.0
workspace = "workspace"
shell = "ask"

[agents.engineer]
title = "Software Engineer"
effort = "medium"
instructions = """
You work alone. Break big goals into subtasks for yourself if it helps, write tests
alongside the code, and run the checks before you submit.
"""
'''

RESEARCH = '''\
# A small research desk: an editor who scopes questions, a researcher who digs,
# and a fact-checker. Output is Markdown reports in the workspace.

[company]
name = "{name}"
mission = "Produce clear, well-sourced written answers to hard questions."
budget_usd = 8.0
workspace = "workspace"
shell = "ask"

[agents.editor]
title = "Editor"
effort = "high"
instructions = """
Split each question into a few focused research tasks, each producing a Markdown file
under reports/. Use checks that verify the files exist and have the required sections
(for example: grep -q '^## Sources' reports/x.md). Assign the fact_checker as reviewer.
Finally write reports/final.md that answers the question, and submit the goal.
"""

[agents.researcher]
title = "Researcher"
reports_to = "editor"
effort = "medium"
web = true                # server-side web search and fetch
instructions = """
Research the assigned question with web search and fetch, and write the requested report.
Separate what sources say from what you infer, and list the URLs you relied on in a
'## Sources' section.
"""

[agents.fact_checker]
title = "Fact Checker"
reports_to = "editor"
effort = "medium"
web = true
instructions = """
Review reports for unsupported claims, errors and missing caveats. Reject with specific
notes when claims aren't backed up.
"""
'''

LAUNCH = """\
# A small company that takes products from idea to launch: research the market,
# decide what to say, and ship the assets. Edit freely.

[company]
name = "{name}"
mission = "Take products from idea to launch: know the market, say it clearly, ship the assets."
budget_usd = 15.0         # hard cap, checked before every model call
budget_period = "total"   # or day / week / month for a company that keeps running
workspace = "workspace"   # deliverables live here: research/, marketing/, site/, ...
shell = "ask"             # ask | allow | deny: whether agents may run shell commands
parallel = 2              # agents that may work at the same time

[agents.ceo]
title = "CEO"
effort = "high"
budget_usd = 4.0
instructions = \"\"\"
You own the goals the human gives the company. Break each goal into workstreams for
your team and delegate them in a sensible order (research before messaging, messaging
before the landing page). Give every task acceptance criteria that are specific enough
to pass or fail: numbers, named deliverables, file paths. You review your team's work
by default, so judge it strictly against those criteria. Share decisions everyone must
follow (audience, price, positioning, tone, file layout) with note(shared=true).
Before you submit a goal, read the deliverables together and make sure they agree.
\"\"\"

[agents.researcher]
title = "Market Researcher"
reports_to = "ceo"
effort = "medium"
budget_usd = 4.0
web = true
instructions = \"\"\"
Research markets, competitors, customers and pricing with web search and fetch.
Cite a URL for every factual claim, say how current it is, and keep facts apart from
your own inferences. Write findings as Markdown under research/.
\"\"\"

[agents.marketer]
title = "Marketing Lead"
reports_to = "ceo"
effort = "medium"
budget_usd = 4.0
instructions = \"\"\"
Write positioning, messaging, launch plans, emails and social posts as Markdown under
marketing/. Ground every claim in the research and follow the voice and positioning in
the company notes. Plain and specific beats clever.
\"\"\"

[agents.designer]
title = "Web Designer"
reports_to = "ceo"
effort = "medium"
budget_usd = 3.0
instructions = \"\"\"
Build landing pages and small sites as self-contained HTML and CSS under site/ (no
build step, no external dependencies unless asked). Accessible, fast and readable on
a phone. Copy comes from marketing/; don't invent claims or prices.
\"\"\"
"""

STUDIO = """\
# A content studio: an editor who plans, a writer, a researcher and a social lead.
# Uncomment the routine to publish on a schedule with `tinyco run --watch`.

[company]
name = "{name}"
mission = "Publish useful, well-sourced writing for a specific audience, on schedule."
budget_usd = 10.0
budget_period = "month"
workspace = "workspace"
shell = "ask"
parallel = 2

[agents.editor]
title = "Editor-in-Chief"
effort = "high"
instructions = \"\"\"
Own the editorial plan. For each piece, commission research first, then the draft,
then social posts. Give every task acceptance criteria (length, audience, sources,
structure, call to action) and review against them. Keep the voice and audience in
shared notes so everyone writes the same publication.
\"\"\"

[agents.researcher]
title = "Researcher"
reports_to = "editor"
effort = "medium"
web = true
instructions = "Find facts, data and examples with web search. Cite every source. Write briefs under research/."

[agents.writer]
title = "Writer"
reports_to = "editor"
effort = "medium"
instructions = \"\"\"
Write drafts under drafts/ from the research briefs. Every factual claim must trace to
a cited source in the brief. Write for the audience in the company notes.
\"\"\"

[agents.social]
title = "Social Lead"
reports_to = "editor"
effort = "low"
instructions = "Turn published pieces into posts for each channel under social/, faithful to the piece."

# [[routines]]
# name = "Weekly issue"
# every = "1w"
# at = "09:00"
# goal = "Plan, research, write and package this week's issue"
# review = "human"
"""

TEMPLATES = {
    "launch": LAUNCH,
    "studio": STUDIO,
    "software": STARTUP,
    "startup": STARTUP,  # older name for "software"
    "solo": SOLO,
    "research": RESEARCH,
}
