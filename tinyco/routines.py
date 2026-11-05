"""Recurring goals for a company that stays up (``tinyco run --watch``).

    [[routines]]
    name = "Dependency check"
    every = "1d"            # 15m, 6h, 1d, 1w (minimum 1m)
    at = "09:00"            # optional local time of day (for intervals of a day or more)
    goal = "Check for outdated dependencies and upgrade the safe ones"
    checks = ["python -m pytest -q"]
    review = "human"        # optional

A routine fires by creating an ordinary goal. If the goal it created last time
is still open, it waits instead of piling up duplicates.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .config import ConfigError
from .office import Office

_UNITS = {"m": 60, "h": 3600, "d": 86400, "w": 7 * 86400}


@dataclass
class Routine:
    name: str
    every: int  # seconds
    goal: str
    description: str = ""
    checks: list[str] = field(default_factory=list)
    review: str | None = None
    at: tuple[int, int] | None = None  # (hour, minute), local time

    def next_due(self, last_run: float | None, now: float) -> float:
        """When this routine should next fire, given when it last fired."""
        if self.at is None:
            return now if last_run is None else last_run + self.every
        # Anchor to the time of day: the first slot after the last run (or from today on, if never run).
        base = datetime.fromtimestamp(last_run if last_run is not None else now)
        slot = base.replace(hour=self.at[0], minute=self.at[1], second=0, microsecond=0)
        if last_run is None:
            if slot.timestamp() < now - 60:  # today's slot already passed
                slot += timedelta(days=1)
            return slot.timestamp()
        while slot.timestamp() <= last_run:
            slot += timedelta(seconds=self.every)
        return slot.timestamp()

    def _step(self, t: float) -> float:
        """One interval later; anchored routines step in wall-clock time so DST doesn't shift them."""
        if self.at is None:
            return t + self.every
        return (datetime.fromtimestamp(t) + timedelta(seconds=self.every)).timestamp()

    def current_slot(self, last_run: float | None, now: float) -> float | None:
        """The latest slot that is due at ``now`` (missed slots collapse into one), or None if none is."""
        slot = self.next_due(last_run, now)
        if slot > now:
            return None
        while (nxt := self._step(slot)) <= now:
            slot = nxt
        return slot


def parse_every(value: str) -> int:
    m = re.fullmatch(r"\s*(\d+)\s*([mhdw])\s*", str(value))
    if not m or int(m.group(1)) == 0:
        raise ConfigError(f"routine interval {value!r} must look like 15m, 6h, 1d or 1w")
    return int(m.group(1)) * _UNITS[m.group(2)]


def parse_routines(raw: list) -> list[Routine]:
    routines, names = [], set()
    for i, r in enumerate(raw or []):
        if not isinstance(r, dict):
            raise ConfigError("each [[routines]] entry must be a table")
        name = str(r.get("name") or f"routine {i + 1}")
        if name in names:
            raise ConfigError(f"duplicate routine name {name!r}")
        names.add(name)
        goal = str(r.get("goal") or "").strip()
        if not goal:
            raise ConfigError(f"routine {name!r} needs a goal")
        every = parse_every(r.get("every", ""))
        at = None
        if r.get("at"):
            m = re.fullmatch(r"\s*([01]?\d|2[0-3]):([0-5]\d)\s*", str(r["at"]))
            if not m:
                raise ConfigError(f"routine {name!r}: at = {r['at']!r} must be HH:MM")
            if every % 86400:
                raise ConfigError(f"routine {name!r}: `at` only works with intervals in whole days (1d, 7d, 1w)")
            at = (int(m.group(1)), int(m.group(2)))
        routines.append(
            Routine(
                name=name,
                every=every,
                goal=goal,
                description=str(r.get("description") or ""),
                checks=_checks(name, r.get("checks")),
                review=(str(r["review"]).strip() or None) if r.get("review") is not None else None,
                at=at,
            )
        )
    return routines


def _checks(name: str, value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list) and all(isinstance(c, str) for c in value):
        return [c for c in value if c.strip()]
    raise ConfigError(f"routine {name!r}: checks must be a list of shell commands")


class Scheduler:
    """Fires due routines as goals; remembers state in the company database."""

    def __init__(self, office: Office, routines: list[Routine]):
        self.office = office
        self.routines = routines
        office.store.db.execute(
            "CREATE TABLE IF NOT EXISTS routines (name TEXT PRIMARY KEY, last_run REAL, last_task INTEGER)"
        )

    def _state(self, name: str) -> tuple[float | None, int | None]:
        row = self.office.store.db.execute(
            "SELECT last_run, last_task FROM routines WHERE name = ?", (name,)
        ).fetchone()
        return (row["last_run"], row["last_task"]) if row else (None, None)

    def due(self, now: float | None = None) -> list[Routine]:
        now = time.time() if now is None else now
        return [r for r in self.routines if r.next_due(self._state(r.name)[0], now) <= now]

    def next_wakeup(self, now: float | None = None) -> float | None:
        now = time.time() if now is None else now
        times = [r.next_due(self._state(r.name)[0], now) for r in self.routines]
        return min(times) if times else None

    def fire_due(self, now: float | None = None) -> list[int]:
        """Create goals for routines that are due. Returns the new task ids.

        The slot that was due is recorded (not the time it fired), so a late or
        delayed fire never shifts the schedule, and missed slots collapse into one.
        The slot is recorded before the goal is created: if the process dies in
        between, that run is skipped rather than duplicated.
        """
        from .office import OfficeError

        now = time.time() if now is None else now
        fired = []
        for r in self.routines:
            last_run, last_task = self._state(r.name)
            slot = r.current_slot(last_run, now)
            if slot is None:
                continue
            prev = self.office.store.task(last_task) if last_task else None
            if prev is not None and prev.is_open:
                self.office.store.event(
                    "routine_skipped",
                    None,
                    prev.id,
                    routine=r.name,
                    why=f"{prev.ref} from the last run is still {prev.status}",
                )
                self._record(r.name, slot, last_task)  # try again next slot
                continue
            self._record(r.name, slot, None)
            try:
                t = self.office.start_goal(r.goal, r.description, r.checks, r.review)
            except OfficeError as e:
                self.office.store.event("routine_error", None, None, routine=r.name, error=str(e))
                continue
            self.office.store.event("routine_fired", None, t.id, routine=r.name)
            self._record(r.name, slot, t.id)
            fired.append(t.id)
        return fired

    def _record(self, name: str, when: float, task_id: int | None) -> None:
        self.office.store.db.execute(
            "INSERT INTO routines (name, last_run, last_task) VALUES (?, ?, ?)"
            " ON CONFLICT(name) DO UPDATE SET last_run = excluded.last_run, last_task = excluded.last_task",
            (name, when, task_id),
        )

    def status(self, now: float | None = None) -> list[dict]:
        now = time.time() if now is None else now
        out = []
        for r in self.routines:
            last_run, last_task = self._state(r.name)
            out.append(
                {
                    "name": r.name,
                    "goal": r.goal,
                    "every": r.every,
                    "at": r.at,
                    "last_run": last_run,
                    "last_task": last_task,
                    "next": r.next_due(last_run, now),
                }
            )
        return out
