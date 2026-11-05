"""``tinyco demo``: watch a company work, with no API key.

The model is replaced by a recording (``ScriptedModel``), but everything else
is real: the company rules, the wake queue, file writes, and the checks,
which actually run Python's unittest against the code the "agents" wrote.
The script includes a rejected review so you can see the feedback loop.
"""

from __future__ import annotations

import shlex
import shutil
import sys
import time
from pathlib import Path

from .config import load
from .office import Office
from .runner import Runner
from .scripted import ScriptedModel, call, say
from .store import Store
from .templates import STARTUP

PY = next((n for n in ("python3", "python") if shutil.which(n)), None) or shlex.quote(sys.executable)

LIB_V1 = '''\
"""Count words in text."""

from collections import Counter


def count_words(text: str) -> Counter:
    return Counter(text.split())


def top(text: str, n: int = 10) -> list[tuple[str, int]]:
    return count_words(text).most_common(n)
'''

LIB_TEST_V1 = """\
import unittest

from wordfreq import count_words, top


class WordFreqTest(unittest.TestCase):
    def test_counts(self):
        self.assertEqual(count_words("a b a")["a"], 2)

    def test_top(self):
        self.assertEqual(top("x y x z x y", 2), [("x", 3), ("y", 2)])


if __name__ == "__main__":
    unittest.main()
"""

CLI = '''\
"""Usage: python -m wordfreq [--top N] FILE"""

import argparse

from . import top


def main() -> None:
    p = argparse.ArgumentParser(prog="wordfreq", description="Print the most common words in a file.")
    p.add_argument("file")
    p.add_argument("--top", type=int, default=10)
    args = p.parse_args()
    with open(args.file, encoding="utf-8") as f:
        for word, n in top(f.read(), args.top):
            print(f"{word} {n}")


main()
'''

CLI_TEST = """\
import subprocess
import sys
import unittest


class CliTest(unittest.TestCase):
    def test_prints_top_words(self):
        out = subprocess.run([sys.executable, "-m", "wordfreq", "--top", "3", "sample.txt"],
                             capture_output=True, text=True, check=True).stdout.splitlines()
        self.assertEqual(len(out), 3)
        word, count = out[0].split()
        self.assertTrue(count.isdigit())


if __name__ == "__main__":
    unittest.main()
"""

SAMPLE = "The cat sat on the mat. The end.\\nThe hat, the bat.\\n".replace("\\n", "\n")

LIB_FIX_OLD = "def count_words(text: str) -> Counter:\n    return Counter(text.split())\n"
LIB_FIX_NEW = (
    'WORD = re.compile(r"[a-z0-9\']+")\n\n\n'
    "def count_words(text: str) -> Counter:\n"
    '    """Case-insensitive; punctuation is ignored."""\n'
    "    return Counter(WORD.findall(text.lower()))\n"
)
TEST_FIX_OLD = "    def test_top(self):"
TEST_FIX_NEW = (
    "    def test_case_and_punctuation(self):\n"
    '        self.assertEqual(count_words("The the THE, the.")["the"], 4)\n\n'
    "    def test_top(self):"
)
PROBE = f"{PY} -c \"from wordfreq import count_words; print(dict(count_words('The the THE.')))\""


def script() -> dict[str, list]:
    lib_check = f"{PY} -m unittest -q tests.test_wordfreq"
    cli_check = f"{PY} -m unittest -q tests.test_cli"
    return {
        "ceo": [
            [
                say("Two pieces: a counting library with tests, then a CLI on top. The library gets a review."),
                call(
                    "note", text="wordfreq layout: package wordfreq/, tests in tests/ (unittest), sample.txt at root."
                ),
                call(
                    "create_task",
                    parent_id=1,
                    title="Word counting library",
                    assignee="engineer",
                    description="Create package wordfreq/ with count_words(text) -> Counter and top(text, n) -> "
                    "list of (word, count). Tests in tests/test_wordfreq.py (unittest; add tests/__init__.py).",
                    checks=[lib_check],
                    reviewer="reviewer",
                ),
                call(
                    "create_task",
                    parent_id=1,
                    title="Command-line interface",
                    assignee="engineer",
                    description="`python -m wordfreq [--top N] FILE` prints 'word count' lines, most common first. "
                    "Add sample.txt and tests/test_cli.py that runs the CLI on it.",
                    checks=[cli_check],
                ),
            ],
            [say("Delegated T2 and T3. I'll be woken when they finish.")],
            # woken: T3 done, T2 sent back by the reviewer
            [say("CLI is done. The library went back for changes; nothing for me to do until it's approved.")],
            # woken: T2 done
            [call("run", command=f"{PY} -m wordfreq --top 3 sample.txt")],
            [
                say("Output looks right: 'the' counted case-insensitively."),
                call(
                    "submit",
                    task_id=1,
                    summary="wordfreq package + CLI: `python -m wordfreq --top N FILE`. "
                    "Case-insensitive, punctuation-insensitive, tested.",
                ),
            ],
            [say("Goal submitted and its checks passed.")],
        ],
        "engineer": [
            # T2
            [
                call("write_file", path="wordfreq/__init__.py", content=LIB_V1),
                call("write_file", path="tests/__init__.py", content=""),
                call("write_file", path="tests/test_wordfreq.py", content=LIB_TEST_V1),
            ],
            [call("run", command=lib_check)],
            [call("submit", task_id=2, summary="wordfreq/__init__.py with count_words and top; 2 tests.")],
            [say("T2 submitted for review.")],
            # T3
            [
                call("write_file", path="wordfreq/__main__.py", content=CLI),
                call("write_file", path="sample.txt", content=SAMPLE),
                call("write_file", path="tests/test_cli.py", content=CLI_TEST),
            ],
            [call("submit", task_id=3, summary="CLI in wordfreq/__main__.py, sample.txt, tests/test_cli.py.")],
            [say("T3 done.")],
            # T2 again: changes requested
            [
                say("Good catch. Lowercasing and extracting words with a regex."),
                call(
                    "edit_file",
                    path="wordfreq/__init__.py",
                    old="from collections import Counter\n",
                    new="import re\nfrom collections import Counter\n",
                ),
                call("edit_file", path="wordfreq/__init__.py", old=LIB_FIX_OLD, new=LIB_FIX_NEW),
                call("edit_file", path="tests/test_wordfreq.py", old=TEST_FIX_OLD, new=TEST_FIX_NEW),
            ],
            [call("run", command=lib_check)],
            [call("submit", task_id=2, summary="count_words is now case- and punctuation-insensitive; added a test.")],
            [say("Resubmitted T2.")],
        ],
        "reviewer": [
            # first review
            [call("read_file", path="wordfreq/__init__.py"), call("run", command=PROBE)],
            [
                say("Case-sensitivity bug."),
                call(
                    "review",
                    task_id=2,
                    approve=False,
                    notes="count_words treats 'The', 'the' and 'the.' as different words. Lowercase the text, strip "
                    "punctuation, and add a test for it.",
                ),
            ],
            [say("Sent back.")],
            # second review
            [call("run", command=PROBE)],
            [call("review", task_id=2, approve=True, notes="Counts are case-insensitive now and there's a test.")],
            [say("Approved.")],
        ],
    }


class Paced:
    """Adds a short pause per model turn so the feed is watchable."""

    def __init__(self, model, delay: float):
        self.model, self.delay = model, delay

    def respond(self, **kw):
        time.sleep(self.delay)
        return self.model.respond(**kw)


def run_demo(
    target: Path, delay: float = 0.4, force: bool = False, feed=None, kind: str = "launch"
) -> tuple[Office, ScriptedModel]:
    """Run a scripted company in ``target``: ``launch`` (business, the default) or ``code``."""
    target = target.resolve()
    if target.exists() and any(target.iterdir()) and not force:
        raise FileExistsError(f"{target} is not empty (use --force to reuse it)")
    target.mkdir(parents=True, exist_ok=True)
    if kind == "launch":
        from .templates import LAUNCH

        text = LAUNCH.replace("{name}", "Cairn Labs").replace('shell = "ask"', 'shell = "allow"')
    else:
        text = STARTUP.replace("{name}", "Demo Co").replace('shell = "ask"', 'shell = "allow"')
    (target / "company.toml").write_text(text, encoding="utf-8")
    company = load(target)
    from . import gitlog

    gitlog.init(company.workspace)
    db = company.state_dir / "state.db"
    if db.exists():
        db.unlink()
    store = Store(db)
    if feed is not None:
        store.listeners.append(feed)
    office = Office(company, store)
    if kind == "launch":
        from . import demo_launch

        office.start_goal(
            demo_launch.GOAL, demo_launch.GOAL_DETAIL, reviewer="human", criteria=demo_launch.GOAL_CRITERIA
        )
        turns = demo_launch.script()
    else:
        office.start_goal(
            "Build a word-frequency CLI",
            "A command-line tool that prints the most common words in a text file.",
            checks=[f"{PY} -m unittest discover -q"],
        )
        turns = script()
    model = ScriptedModel(turns, usage={"input": 0, "output": 0, "cache_read": 0, "cache_write": 0})
    Runner(office, Paced(model, delay) if delay else model).run()
    return office, model
