import json
import re

from tinyco.cli import main
from tinyco.demo import run_demo
from tinyco.export import render


def _snapshot(html: str) -> dict:
    m = re.search(r"<script>window\.TINYCO_SNAPSHOT = (.*?);</script>", html, re.S)
    assert m, "snapshot script missing"
    return json.loads(m.group(1).replace("<\\/", "</"))


def test_export_embeds_the_whole_run(tmp_path):
    office, _ = run_demo(tmp_path / "co", delay=0, kind="code")
    html = render(office)
    assert "<title>Demo Co · tinyco</title>" in html
    snap = _snapshot(html)
    assert [t["status"] for t in snap["state"]["tasks"]] == ["done", "done", "done"]
    assert snap["state"]["running"] is False
    assert len(snap["events"]) == len(office.store.events(limit=10_000))


def test_export_cannot_break_out_of_its_script_tag(tmp_path):
    office, _ = run_demo(tmp_path / "co", delay=0, kind="code")
    evil = "</script><script>alert(1)</script>"
    office.start_goal(evil)
    html = render(office)
    assert evil not in html  # the closing tag is escaped inside the JSON
    assert any(t["title"] == evil for t in _snapshot(html)["state"]["tasks"])  # but the data survives


def test_cli_export(tmp_path, monkeypatch, capsys):
    run_demo(tmp_path / "co", delay=0, kind="code")
    monkeypatch.chdir(tmp_path / "co")
    assert main(["export", "-o", "run.html"]) == 0
    assert (tmp_path / "co" / "run.html").stat().st_size > 10_000
    assert "review it before sharing" in capsys.readouterr().out
