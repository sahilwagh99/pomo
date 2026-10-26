"""What Pomo learns from your history: your best focus length, focus score, and how agents fit your day.

Pure functions over stored records, so they're easy to test and cheap to run.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable

from .storage import AgentEvent, SessionRecord

MIN_PER_LENGTH = 3       # rated sessions needed before a length counts
MIN_RATED = 8            # rated sessions needed before any tip
TIP_MARGIN = 0.35        # how much better (on the 1..3 scale) a length must score to be worth suggesting
LOOKBACK_DAYS = 60
DAYPARTS = (("in the morning", 5, 12), ("in the afternoon", 12, 17), ("in the evening", 17, 24))


@dataclass
class LengthTip:
    minutes: int              # the length that works best for you
    score: float              # its average, 1 (tough) .. 3 (great); stopped-early sessions count as tough
    current_score: float | None
    sessions: int             # rated sessions behind the tip
    best_daypart: str = ""    # "in the morning" etc., when one clearly stands out

    @property
    def text(self) -> str:
        line = f"Your {self.minutes}-minute sessions go best ({self.score:.1f} of 3"
        if self.current_score is not None:
            line += f", vs {self.current_score:.1f} for your current length"
        line += ")."
        if self.best_daypart:
            line += f" You also focus best {self.best_daypart}."
        return line


def _score(r: SessionRecord) -> float | None:
    """How a focus session went: its rating, and a stopped-early session counts as tough."""
    if r.mode != "focus":
        return None
    if not r.completed:
        return 1.0 if r.actual_sec >= 120 else None  # a quick restart isn't a verdict
    return float(r.rating) if r.rating else None


def length_tip(records: Iterable[SessionRecord], current_min: int, today: date | None = None) -> LengthTip | None:
    """Suggest a focus length when another clearly works better for you than your current one."""
    today = today or date.today()
    since = today - timedelta(days=LOOKBACK_DAYS)
    by_length: dict[int, list[float]] = defaultdict(list)
    by_part: dict[str, list[float]] = defaultdict(list)
    for r in records:
        score = _score(r)
        if score is None or r.day < since:
            continue
        minutes = max(5, int(5 * round(r.planned_sec / 60 / 5)))
        by_length[minutes].append(score)
        hour = r.started_at.hour
        for name, start, end in DAYPARTS:
            if start <= hour < end:
                by_part[name].append(score)
    rated = sum(len(v) for v in by_length.values())
    if rated < MIN_RATED:
        return None
    means = {m: sum(v) / len(v) for m, v in by_length.items() if len(v) >= MIN_PER_LENGTH}
    if not means:
        return None
    best = max(means, key=lambda m: (means[m], -abs(m - current_min)))
    current = max(5, int(5 * round(current_min / 5)))
    current_score = means.get(current)
    if best == current or (current_score is not None and means[best] - current_score < TIP_MARGIN):
        return None
    parts = {name: sum(v) / len(v) for name, v in by_part.items() if len(v) >= MIN_PER_LENGTH}
    best_part = ""
    if len(parts) >= 2:
        ordered = sorted(parts, key=parts.get, reverse=True)
        if parts[ordered[0]] - parts[ordered[1]] >= TIP_MARGIN:
            best_part = ordered[0]
    return LengthTip(best, round(means[best], 2), None if current_score is None else round(current_score, 2),
                     rated, best_part)


def rating_counts(records: Iterable[SessionRecord], start: date, end: date) -> dict[int, int]:
    counts = {1: 0, 2: 0, 3: 0}
    for r in records:
        if r.mode == "focus" and r.rating in counts and start <= r.day <= end:
            counts[r.rating] += 1
    return counts


def focus_score(app_rows: Iterable[tuple[str, int, bool]]) -> tuple[float | None, int, int]:
    """(share of focus time not on distracting apps, distracting seconds, total seconds)."""
    total = distracting = 0
    for _app, seconds, is_distracting in app_rows:
        total += seconds
        distracting += seconds if is_distracting else 0
    if total < 60:
        return None, distracting, total
    return 1.0 - distracting / total, distracting, total


@dataclass
class AgentOverlap:
    agent_sec: float = 0.0        # time your agents spent working
    during_focus_sec: float = 0.0
    during_break_sec: float = 0.0
    turns: int = 0


def agent_turns(events: Iterable[AgentEvent]) -> list[tuple[datetime, datetime]]:
    """When agents were working: from each prompt to that session's next finished turn."""
    open_turns: dict[str, datetime] = {}
    turns = []
    for ev in sorted(events, key=lambda e: e.ts):
        if ev.event == "UserPromptSubmit":
            open_turns.setdefault(ev.session, ev.ts)
        elif ev.event == "Stop" and ev.session in open_turns:
            start = open_turns.pop(ev.session)
            if ev.ts > start:
                turns.append((start, ev.ts))
    return turns


def _overlap(a: tuple[datetime, datetime], b: tuple[datetime, datetime]) -> float:
    return max(0.0, (min(a[1], b[1]) - max(a[0], b[0])).total_seconds())


def agent_overlap(events: Iterable[AgentEvent], records: Iterable[SessionRecord], day: date) -> AgentOverlap:
    """How agent work lined up with your focus sessions and breaks on one day."""
    turns = [t for t in agent_turns(e for e in events if e.day == day)]
    spans = [((r.started_at, r.ended_at), r.mode) for r in records if r.day == day and r.ended_at > r.started_at]
    out = AgentOverlap(turns=len(turns))
    for turn in turns:
        out.agent_sec += (turn[1] - turn[0]).total_seconds()
        for span, mode in spans:
            if mode == "focus":
                out.during_focus_sec += _overlap(turn, span)
            else:
                out.during_break_sec += _overlap(turn, span)
    return out


def songs_left(remaining: float, duration: float, position: float = 0.0) -> str:
    """'about 3 songs left' for the focus time left, when the song length makes it meaningful."""
    if not 60 <= duration <= 900 or remaining <= 0:
        return ""
    left_in_song = max(0.0, duration - position)
    if remaining <= left_in_song:
        return "ends during this song"
    count = 1 + math.ceil((remaining - left_in_song) / duration)
    return f"about {count} songs left"
