"""Commit the workspace whenever a task is done, authored by the agent that did it.

``git log`` in the workspace then reads like a team's history:

    a1b2c3d  engineer   T2 Word counting library
    d4e5f6a  engineer   T3 Command-line interface
    0718293  ceo        T1 Build a word-frequency CLI

Enabled when the workspace is a git repository (``tinyco init`` creates one
if git is installed). Turn it off with ``git = false`` under ``[company]``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

_IDENTITY = ["-c", "user.name=tinyco", "-c", "user.email=tinyco@localhost"]


def available() -> bool:
    return shutil.which("git") is not None


def is_repo(path: Path) -> bool:
    return (path / ".git").exists()


def _git(path: Path, *args: str, identity: bool = False) -> subprocess.CompletedProcess:
    cmd = ["git", *(_IDENTITY if identity else []), *args]
    return subprocess.run(cmd, cwd=path, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)


def init(path: Path) -> bool:
    if not available() or is_repo(path):
        return False
    path.mkdir(parents=True, exist_ok=True)
    if _git(path, "init", "-q").returncode != 0:
        return False
    (path / ".gitignore").write_text("__pycache__/\n*.pyc\n.venv/\nnode_modules/\n", encoding="utf-8")
    return True


def has_committer(path: Path) -> bool:
    return bool(_git(path, "config", "user.email").stdout.strip())


def commit(path: Path, agent: str, message: str) -> str | None:
    """Stage everything and commit as ``agent``. Returns the short sha, or None if nothing changed."""
    if not available() or not is_repo(path):
        return None
    if _git(path, "add", "-A").returncode != 0:
        return None
    if _git(path, "diff", "--cached", "--quiet").returncode == 0:
        return None  # nothing staged
    author = f"{agent} (tinyco) <{agent}@tinyco.local>"
    r = _git(path, "commit", "-q", "--no-verify", f"--author={author}", "-m", message, identity=not has_committer(path))
    if r.returncode != 0:
        return None
    return _git(path, "rev-parse", "--short", "HEAD").stdout.strip() or None
