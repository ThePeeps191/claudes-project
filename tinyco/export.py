"""``tinyco export``: a single, self-contained HTML snapshot of a company's run.

It's the dashboard page with the data embedded and the write actions hidden,
so it opens from disk or any static host (GitHub Pages, a gist preview, ...).
"""

from __future__ import annotations

import json
import time
from importlib import resources

from .dashboard import snapshot
from .office import Office


def all_events(office: Office) -> list[dict]:
    rows = office.store.db.execute("SELECT * FROM events ORDER BY id").fetchall()
    return [
        {
            "id": r["id"],
            "ts": r["ts"],
            "kind": r["kind"],
            "agent": r["agent"],
            "task_id": r["task_id"],
            "data": json.loads(r["data"]),
        }
        for r in rows
    ]


def render(office: Office) -> str:
    page = resources.files("tinyco").joinpath("static/index.html").read_text(encoding="utf-8")
    state = snapshot(office)
    state["running"] = False
    state["pending"] = 0
    data = {"exported_at": time.time(), "state": state, "events": all_events(office)}
    payload = json.dumps(data, default=str, ensure_ascii=False)
    # Make the JSON safe inside a <script> element.
    payload = payload.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    title = f"<title>{_esc(office.company.name)} · tinyco</title>"
    page = page.replace("<title>tinyco</title>", title, 1)
    return page.replace(
        '<script>\n"use strict";', f'<script>window.TINYCO_SNAPSHOT = {payload};</script>\n<script>\n"use strict";', 1
    )


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
