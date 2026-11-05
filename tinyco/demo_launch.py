"""The scripted product-launch run behind ``tinyco demo``.

A four-person company (CEO, market researcher, marketing lead, web designer)
launches "Cairn", a fictional $9/month habit tracker for indie developers. The
CEO sets shared positioning, commissions research, rejects the first draft
because one price has no source, then runs messaging and the landing page in
order. The goal ends waiting for the human's approval.

The competitors and prices below are invented for the demo (example.com-style
sources); a real run researches real ones with web search.
"""

from __future__ import annotations

from .scripted import call, say

GOAL = "Launch Cairn, a $9/month habit tracker for indie developers"
GOAL_DETAIL = "Know the market, decide what we say, and ship a launch email and a landing page."
GOAL_CRITERIA = [
    "Competitor research with a sourced price for every competitor",
    "One-sentence positioning and a launch email under 200 words",
    "A landing page that states the price and has an email signup form",
]

RESEARCH_V1 = """\
# Competitors

| Product | Price | Source |
|---|---|---|
| Tallyho | $6/month | https://tallyho.example/pricing |
| StreakBox | $4.99/month | |
| Ritualist | $12/month | https://ritualist.example/plans |

## What we learned
- None of them talk to developers: copy is about "wellness" and "routines".
- StreakBox is the cheapest; Ritualist sells team features we don't need.

## Opportunities for Cairn
1. Speak to indie developers: shipping streaks, not water intake.
2. Plain-text export and a CLI, which nobody offers.
3. One honest price ($9) with no team upsell.
"""

RESEARCH_FIX_OLD = "| StreakBox | $4.99/month | |"
RESEARCH_FIX_NEW = "| StreakBox | $4.99/month | https://streakbox.example/pricing (checked today) |"

MESSAGING = """\
# Messaging

**Positioning:** Cairn is the habit tracker for indie developers who want to keep shipping:
small daily streaks, a CLI, and your data in plain text, for $9 a month.

**Headline:** Keep shipping, one small step a day.
**Subhead:** Habit tracking for indie developers. A CLI, plain-text data, $9/month.

## Proof points (from research/competitors.md)
- The only tracker with a CLI and plain-text export.
- One price, no team upsell (Ritualist charges $12 for features solo devs don't need).
"""

EMAIL = """\
Subject: Cairn is live: keep shipping, one small step a day

Hi {first_name},

You start side projects with a burst of energy. Cairn helps you keep going.

Track one small step a day (a commit, a page of docs, a customer email) from your
terminal with `cairn done`, see your streak, and keep everything in plain text you own.

It's $9 a month, one plan, no upsell.

Start your streak: https://cairn.example

Thanks for building,
The Cairn team
"""

LANDING = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cairn: keep shipping, one small step a day</title>
<style>
  body { font: 18px/1.5 system-ui, sans-serif; max-width: 40rem; margin: 3rem auto; padding: 0 1rem; color: #1b1f24; }
  h1 { font-size: 2.2rem; line-height: 1.15; margin-bottom: .3rem; }
  .price { font-size: 1.4rem; font-weight: 700; }
  form { display: flex; gap: .5rem; flex-wrap: wrap; margin-top: 1.5rem; }
  input { flex: 1; min-width: 12rem; padding: .7rem; font: inherit; }
  button { padding: .7rem 1.2rem; font: inherit; background: #1b1f24; color: #fff; border: 0; border-radius: .4rem; }
</style>
</head>
<body>
  <h1>Keep shipping, one small step a day.</h1>
  <p>Habit tracking for indie developers. A CLI, plain-text data, one honest price.</p>
  <p class="price">$9/month</p>
  <form action="/subscribe" method="post">
    <label for="email" hidden>Email</label>
    <input id="email" name="email" type="email" placeholder="you@example.com" required>
    <button type="submit">Start my streak</button>
  </form>
</body>
</html>
"""

R_CRITERIA = [
    "Profiles at least 3 competing habit trackers with their current price",
    "Cites a source URL for every price",
    "Ends with at least 3 positioning opportunities for Cairn",
]
M_CRITERIA = [
    "A one-sentence positioning statement",
    "A launch email under 200 words with exactly one call to action",
    "Every claim matches research/competitors.md",
]
D_CRITERIA = [
    "Headline and subhead match marketing/messaging.md",
    "Has an email signup form",
    "Readable on a phone (viewport meta tag, no fixed-width layout)",
]


def _ok(n: int, reasons: list[str]) -> list[dict]:
    return [{"criterion": i + 1, "passed": True, "reason": reasons[i]} for i in range(n)]


def script() -> dict[str, list]:
    return {
        "ceo": [
            # T1 assigned: shared decisions, then research first.
            [
                say("Research first, then messaging, then the landing page. Writing down what everyone must follow."),
                call(
                    "note",
                    shared=True,
                    text="Cairn: habit tracker for indie developers, $9/month, one plan. "
                    "Voice: plain, specific, no hype. Files: research/, marketing/, site/.",
                ),
                call(
                    "create_task",
                    parent_id=1,
                    title="Competitor research",
                    assignee="researcher",
                    description="Who else sells habit tracking to developers, at what price, and where Cairn fits. "
                    "Write research/competitors.md.",
                    criteria=R_CRITERIA,
                ),
            ],
            [say("Research is with the researcher. I'll pick it up when it comes back.")],
            # Review T2, first submission: one price has no source.
            [
                call("read_file", path="research/competitors.md"),
                call(
                    "review",
                    task_id=2,
                    approve=False,
                    notes="",
                    verdicts=[
                        {
                            "criterion": 1,
                            "passed": True,
                            "reason": "Tallyho, StreakBox and Ritualist, each with a price.",
                        },
                        {"criterion": 2, "passed": False, "reason": "StreakBox's $4.99 has no source."},
                        {"criterion": 3, "passed": True, "reason": "Three concrete opportunities at the end."},
                    ],
                ),
            ],
            [say("Sent back: one unsourced price.")],
            # Review T2 again.
            [
                call("read_file", path="research/competitors.md"),
                call(
                    "review",
                    task_id=2,
                    approve=True,
                    notes="Good.",
                    verdicts=_ok(
                        3, ["Three competitors with prices.", "Every price now links a source.", "Three opportunities."]
                    ),
                ),
            ],
            [say("Research approved.")],
            # T1: research done, commission messaging.
            [
                call(
                    "create_task",
                    parent_id=1,
                    title="Positioning and launch email",
                    assignee="marketer",
                    description="Use research/competitors.md. Write marketing/messaging.md (positioning, headline, "
                    "subhead, proof points) and marketing/launch-email.md.",
                    criteria=M_CRITERIA,
                )
            ],
            [say("Messaging is next.")],
            # Review T3.
            [
                call("read_file", path="marketing/messaging.md"),
                call("read_file", path="marketing/launch-email.md"),
                call(
                    "review",
                    task_id=3,
                    approve=True,
                    notes="",
                    verdicts=_ok(
                        3,
                        [
                            "Positioning is one sentence.",
                            "Email is about 90 words with one link.",
                            "CLI and pricing claims trace to the research.",
                        ],
                    ),
                ),
            ],
            [say("Messaging approved.")],
            # T1: messaging done, commission the landing page.
            [
                call(
                    "create_task",
                    parent_id=1,
                    title="Landing page",
                    assignee="designer",
                    description="One self-contained page at site/index.html using the headline and subhead in "
                    "marketing/messaging.md.",
                    criteria=D_CRITERIA,
                    checks=["test -f site/index.html", "grep -q '\\$9/month' site/index.html"],
                )
            ],
            [say("Landing page is with the designer.")],
            # Review T4.
            [
                call("read_file", path="site/index.html"),
                call(
                    "review",
                    task_id=4,
                    approve=True,
                    notes="",
                    verdicts=_ok(
                        3,
                        [
                            "Headline and subhead copied from messaging.md.",
                            "Email form posting to /subscribe.",
                            "Viewport meta tag and a max-width layout.",
                        ],
                    ),
                ),
            ],
            [say("Landing page approved.")],
            # T1: everything done; submit the goal for the human's sign-off.
            [
                call(
                    "submit",
                    task_id=1,
                    summary="Research in research/competitors.md, positioning and launch email in "
                    "marketing/, landing page at site/index.html. All three agree on audience, price and claims.",
                )
            ],
            [say("Submitted the launch for your approval.")],
        ],
        "researcher": [
            [
                call("write_file", path="research/competitors.md", content=RESEARCH_V1),
                call("submit", task_id=2, summary="Three competitors with prices and three opportunities."),
            ],
            [say("Submitted.")],
            [
                call("edit_file", path="research/competitors.md", old=RESEARCH_FIX_OLD, new=RESEARCH_FIX_NEW),
                call("submit", task_id=2, summary="Added the StreakBox pricing source."),
            ],
            [say("Fixed and resubmitted.")],
        ],
        "marketer": [
            [
                call("write_file", path="marketing/messaging.md", content=MESSAGING),
                call("write_file", path="marketing/launch-email.md", content=EMAIL),
                call("submit", task_id=3, summary="Positioning, headline, proof points and a 90-word launch email."),
            ],
            [say("Submitted.")],
        ],
        "designer": [
            [
                call("write_file", path="site/index.html", content=LANDING),
                call("submit", task_id=4, summary="Landing page with headline, $9/month price and signup form."),
            ],
            [say("Submitted.")],
        ],
    }
