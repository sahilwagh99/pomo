"""Pure functions that turn session history into dashboard numbers."""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Iterable

from .storage import SessionRecord

NO_TASK = "No task"


@dataclass
class DayStat:
    day: date
    focus_sec: float = 0.0
    pomodoros: int = 0


@dataclass
class Summary:
    start: date
    end: date
    focus_sec: float = 0.0
    pomodoros: int = 0
    interrupted: int = 0
    break_sec: float = 0.0
    days: list[DayStat] = field(default_factory=list)
    by_hour: list[float] = field(default_factory=lambda: [0.0] * 24)
    by_weekday: list[float] = field(default_factory=lambda: [0.0] * 7)
    tasks: list[tuple[str, float, int]] = field(default_factory=list)  # name, focus sec, pomodoros

    @property
    def n_days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def active_days(self) -> int:
        return sum(1 for d in self.days if d.focus_sec > 0)

    @property
    def completion_rate(self) -> float | None:
        attempts = self.pomodoros + self.interrupted
        return self.pomodoros / attempts if attempts else None

    @property
    def avg_per_active_day(self) -> float:
        active = self.active_days
        return self.focus_sec / active if active else 0.0

    @property
    def best_day(self) -> DayStat | None:
        best = max(self.days, key=lambda d: d.focus_sec, default=None)
        return best if best and best.focus_sec > 0 else None

    @property
    def peak_hour(self) -> int | None:
        top = max(self.by_hour)
        return self.by_hour.index(top) if top > 0 else None


def summarize(records: Iterable[SessionRecord], start: date, end: date) -> Summary:
    summary = Summary(start=start, end=end)
    per_day = {start + timedelta(days=i): DayStat(start + timedelta(days=i)) for i in range(max(0, (end - start).days + 1))}
    tasks: dict[str, list[float]] = defaultdict(lambda: [0.0, 0])
    for r in records:
        day = r.day
        if day < start or day > end:
            continue
        if r.mode != "focus":
            summary.break_sec += r.actual_sec
            continue
        summary.focus_sec += r.actual_sec
        stat = per_day[day]
        stat.focus_sec += r.actual_sec
        if r.completed:
            summary.pomodoros += 1
            stat.pomodoros += 1
        else:
            summary.interrupted += 1
        _spread_over_hours(summary.by_hour, r.started_at, r.actual_sec)
        summary.by_weekday[day.weekday()] += r.actual_sec
        bucket = tasks[r.task.strip() or NO_TASK]
        bucket[0] += r.actual_sec
        bucket[1] += int(r.completed)
    summary.days = [per_day[d] for d in sorted(per_day)]
    summary.tasks = sorted(((name, v[0], int(v[1])) for name, v in tasks.items()), key=lambda t: -t[1])
    return summary


def _spread_over_hours(bins: list[float], start: datetime, seconds: float) -> None:
    t, left = start, float(seconds)
    while left > 0:
        next_hour = t.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        chunk = min(left, (next_hour - t).total_seconds())
        bins[t.hour] += chunk
        left -= chunk
        t = next_hour


def daily_map(records: Iterable[SessionRecord]) -> dict[date, DayStat]:
    days: dict[date, DayStat] = {}
    for r in records:
        if r.mode != "focus":
            continue
        stat = days.setdefault(r.day, DayStat(r.day))
        stat.focus_sec += r.actual_sec
        stat.pomodoros += int(r.completed)
    return days


def streaks(records: Iterable[SessionRecord], today: date) -> tuple[int, int]:
    """(current, best) runs of consecutive days with at least one completed pomodoro.

    The current streak survives until the end of today, so a run that ended
    yesterday still counts while today is not over.
    """
    days = sorted({r.day for r in records if r.mode == "focus" and r.completed})
    best = run = 0
    prev: date | None = None
    for d in days:
        run = run + 1 if prev is not None and (d - prev).days == 1 else 1
        best = max(best, run)
        prev = d
    present = set(days)
    anchor = today if today in present else today - timedelta(days=1)
    current = 0
    while anchor in present:
        current += 1
        anchor -= timedelta(days=1)
    return current, best


# ------------------------------------------------------------------ formatting
def fmt_hm(seconds: float) -> str:
    minutes = int(round(seconds / 60))
    if seconds > 0 and minutes == 0:
        return "<1m"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"


def fmt_clock(seconds: float) -> str:
    total = max(0, math.ceil(seconds - 1e-6))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def fmt_hour(hour: int) -> str:
    hour %= 24
    suffix = "AM" if hour < 12 else "PM"
    return f"{hour % 12 or 12} {suffix}"


def fmt_day(d: date, weekday: bool = True) -> str:
    text = f"{d:%b} {d.day}"
    return f"{d:%a}, {text}" if weekday else text


def plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"
