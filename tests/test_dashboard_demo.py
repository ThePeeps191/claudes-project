from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from tinyco.config import load
from tinyco.dashboard import serve
from tinyco.demo import run_demo


def test_demo_builds_working_code_with_a_rejected_review(tmp_path):
    office, model = run_demo(tmp_path / "demo", delay=0, kind="code")
    assert model.remaining() == {}
    statuses = {t.id: t.status for t in office.store.tasks()}
    assert statuses == {1: "done", 2: "done", 3: "done"}
    assert office.get(2).attempts == 1  # the first review sent it back
    rejected = [e for e in office.store.events(limit=500, kinds=("reviewed",)) if not e["data"]["approved"]]
    assert len(rejected) == 1
    ws = office.company.workspace
    assert (ws / "wordfreq" / "__init__.py").exists() and (ws / "tests" / "test_cli.py").exists()
    assert "re.compile" in (ws / "wordfreq" / "__init__.py").read_text()
    assert office.store.events(limit=500, kinds=("goal_done",))


def test_demo_refuses_non_empty_dir(tmp_path):
    (tmp_path / "x").write_text("hi")
    with pytest.raises(FileExistsError):
        run_demo(tmp_path, delay=0)


@pytest.fixture
def server(tmp_path):
    run_demo(tmp_path / "co", delay=0, kind="code")
    srv = serve(load(tmp_path / "co"), port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _get(url):
    with urllib.request.urlopen(url) as r:
        return r.status, r.headers, r.read()


def _post(url, body, header=True):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json", **({"X-Tinyco": "1"} if header else {})},
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_dashboard_serves_page_state_and_events(server):
    status, headers, body = _get(server + "/")
    assert status == 200 and b"<title>tinyco</title>" in body
    assert "default-src 'self'" in headers["Content-Security-Policy"]
    _, _, body = _get(server + "/api/state")
    state = json.loads(body)
    assert state["company"]["name"] == "Demo Co"
    assert [a["name"] for a in state["agents"]] == ["ceo", "engineer", "reviewer"]
    assert len(state["tasks"]) == 3 and state["inbox"] == []
    _, _, body = _get(server + "/api/events?after=0")
    events = json.loads(body)
    assert events[0]["kind"] == "goal_created"
    last = events[-1]["id"]
    _, _, body = _get(server + f"/api/events?after={last}")
    assert json.loads(body) == []
    _, _, body = _get(server + "/api/events?task=2")
    assert all(e["task_id"] == 2 for e in json.loads(body))


def test_dashboard_actions_require_header(server):
    code, body = _post(server + "/api/goal", {"title": "Sneaky"}, header=False)
    assert code == 403
    code, body = _post(server + "/api/goal", {"title": "Second goal", "checks": ["true", ""], "reviewer": "human"})
    assert code == 200 and "Created T4" in body["message"]
    _, _, raw = _get(server + "/api/state")
    t4 = next(t for t in json.loads(raw)["tasks"] if t["id"] == 4)
    assert t4["checks"] == ["true"] and t4["reviewer"] == "human" and t4["assignee"] == "ceo"
    code, body = _post(server + "/api/approve", {"task_id": 4})
    assert code == 409 and "not waiting for review" in body["error"]
    code, body = _post(server + "/api/cancel", {"task_id": 4})
    assert code == 200
    code, body = _post(server + "/api/reply", {"task_id": 1})
    assert code == 400


def test_launch_demo_is_a_business_run_ending_with_the_human(tmp_path):
    office, model = run_demo(tmp_path / "launch", delay=0)
    assert model.remaining() == {}
    statuses = {t.id: t.status for t in office.store.tasks()}
    assert statuses == {1: "in_review", 2: "done", 3: "done", 4: "done"}
    assert [t.id for t in office.inbox()] == [1]
    assert office.get(1).criteria and office.get(2).criteria
    ws = office.company.workspace
    for f in ("research/competitors.md", "marketing/messaging.md", "marketing/launch-email.md", "site/index.html"):
        assert (ws / f).exists(), f
    reviews = office.store.events(task_id=2, kinds=("reviewed",))
    assert [r["data"]["approved"] for r in reviews] == [False, True]  # sent back once for an unsourced price
    assert "streakbox.example" in (ws / "research/competitors.md").read_text()
    office.human_review(1, True)
    assert office.get(1).status == "done"
