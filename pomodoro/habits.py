"""Habits: a monthly habit tracker, and a rank you earn by keeping it, like a game's ranked medals.

You list your habits, each with a monthly goal (on how many days of the month you want to do it), and
tick them off day by day, the way a habit tracker spreadsheet works. Every day is a ranked match against
your daily target, which is what your monthly goals add up to per day unless you set one yourself:

  the target reached     +30 points
  at least half of it    +15
  less than half         -30

Points climb, and slide back, through eight medals of five stars each, 150 points a star, the way ranked
medals work in competitive games: five perfect days earn a star, and a day you skip costs you. Today's
match is still open, so it counts once the day is over. Yesterday can still be ticked (for what you
forgot to tick), and older days are final.

The month view works like the spreadsheet too: each habit's progress toward its monthly goal, the habits
done each day, and a success rate (three parts pace, one part the last three days) with its verdict.

Plain logic, kept pure for tests. The window is habit_window.py, and the medals are drawn in medals.py.
"""
from __future__ import annotations

import calendar
import secrets
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

NAME_MAX = 60
GOAL_MAX = 31          # a monthly goal of 31 means every day, in any month
MAX_HABITS = 40        # habits you track at once
KEEP_RETIRED = 200     # retired habits are kept so the days they counted stay as they were

WIN, HALF, LOSS = 30, 15, -30
STAR_POINTS = 150
STARS = 5
MEDALS = ("Rookie", "Squire", "Knight", "Paladin", "Champion", "Mythic", "Celestial", "Eternal")
TOP = len(MEDALS) - 1                  # the top medal has no stars: it counts up instead
TOP_FLOOR = TOP * STARS * STAR_POINTS  # 5250 points

VERDICTS = ((0.95, "Excellent"), (0.8, "Good job"), (0.5, "Do better"), (0.3, "Falling behind"),
            (0.0, "Start again"))


# ------------------------------------------------------------------ habits
def _day(text) -> date | None:
    try:
        return date.fromisoformat(text) if isinstance(text, str) and text else None
    except ValueError:
        return None


def _goal(value) -> int:
    try:
        return max(1, min(GOAL_MAX, int(value)))
    except (TypeError, ValueError):
        return GOAL_MAX


def make(name: str, goal: int, today: date, existing: list[dict] = ()) -> dict:
    """A new habit, counted from today."""
    taken = {h["id"] for h in existing}
    hid = secrets.token_hex(3)
    while hid in taken:
        hid = secrets.token_hex(3)
    return {"id": hid, "name": name.strip()[:NAME_MAX], "goal": _goal(goal), "since": today.isoformat(), "until": ""}


def clean(habits) -> list[dict]:
    """Habits as settings.json may hold them: well-formed, unique, and not too many.

    A habit retired on the day it was added never counted, so it goes.
    """
    out, seen = [], set()
    for h in habits if isinstance(habits, list) else []:
        if not isinstance(h, dict):
            continue
        hid, name = h.get("id"), h.get("name")
        since, until = _day(h.get("since")), _day(h.get("until"))
        if not (isinstance(hid, str) and hid and hid not in seen and isinstance(name, str) and name.strip()
                and since is not None) or (h.get("until") and until is None):
            continue
        if until is not None and until <= since:
            continue
        seen.add(hid)
        out.append({"id": hid, "name": name.strip()[:NAME_MAX], "goal": _goal(h.get("goal")),
                    "since": since.isoformat(), "until": until.isoformat() if until else ""})
    current_ = [h for h in out if not h["until"]][:MAX_HABITS]
    retired = [h for h in out if h["until"]][-KEEP_RETIRED:]
    keep = {h["id"] for h in current_ + retired}
    return [h for h in out if h["id"] in keep]


def is_active(habit: dict, day: date) -> bool:
    return habit["since"] <= day.isoformat() and (not habit["until"] or day.isoformat() < habit["until"])


def current(habits: list[dict], day: date) -> list[dict]:
    """The habits you track on a day."""
    return [h for h in habits if is_active(h, day)]


def apply_edit(habits: list[dict], rows: list[tuple[str | None, str, int]], today: date) -> None:
    """Save the habit editor: rows are (id, or None for a new habit, name, monthly goal), in the order shown.

    A habit left out is retired from today on; the days it already counted stay as they were.
    """
    by_id = {h["id"]: h for h in habits if not h["until"]}
    kept, ordered = set(), []
    for hid, name, goal in rows:
        name = " ".join(name.split())[:NAME_MAX]
        if not name:
            continue
        h = by_id.get(hid) if hid else None
        if h is not None and hid not in kept:
            h["name"], h["goal"] = name, _goal(goal)
            kept.add(hid)
        else:
            h = make(name, goal, today, habits + ordered)
        ordered.append(h)
    for hid, h in by_id.items():
        if hid not in kept:
            h["until"] = today.isoformat()
    habits[:] = clean([h for h in habits if h["until"]] + ordered)


def merge(habits: list[dict], found: list[tuple[str, int]], today: date) -> tuple[int, int]:
    """Add habits from a spreadsheet: new names are added, a name you already track gets the sheet's goal.

    Returns (added, updated).
    """
    added = updated = 0
    by_name = {h["name"].casefold(): h for h in current(habits, today)}
    for name, goal in found:
        h = by_name.get(name.strip().casefold())
        if h is not None:
            if h["goal"] != _goal(goal):
                h["goal"] = _goal(goal)
                updated += 1
        elif len(current(habits, today)) < MAX_HABITS:
            h = make(name, goal, today, habits)
            habits.append(h)
            by_name[h["name"].casefold()] = h
            added += 1
    return added, updated


def start_day(habits: list[dict]) -> date | None:
    """The first day any habit was tracked."""
    days = [_day(h["since"]) for h in habits]
    return min(days) if days else None


def editable(day: date, today: date) -> bool:
    """Today, and yesterday for what you forgot to tick. Older days are final."""
    return today - timedelta(days=1) <= day <= today


# ------------------------------------------------------------------ the daily match
def days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def month_goal(habit: dict, year: int, month: int) -> int:
    """A habit's goal in one month: 31 means every day, even in a 30-day month or February."""
    return min(habit["goal"], days_in_month(year, month))


def auto_target(habits: list[dict], day: date) -> int:
    """What your monthly goals add up to per day, rounded down: a day that keeps you on track."""
    active = current(habits, day)
    if not active:
        return 0
    n = days_in_month(day.year, day.month)
    return max(1, min(len(active), sum(month_goal(h, day.year, day.month) for h in active) // n))


def target_for(habits: list[dict], fixed: int, day: date) -> int:
    """The day's target: the one you set (at most the habits you track), or the automatic one."""
    active = current(habits, day)
    if not active:
        return 0
    return min(len(active), fixed) if fixed > 0 else auto_target(habits, day)


def result(done: int, target: int) -> int:
    """Points for a day: +30 for the target, +15 for at least half of it, -30 for less."""
    if target <= 0:
        return 0
    if done >= target:
        return WIN
    if done * 2 >= target:
        return HALF
    return LOSS


@dataclass(frozen=True)
class Day:
    day: date
    done: int
    target: int
    delta: int    # the points the day earned, or cost
    points: int   # points after it (never below zero)


def history(habits: list[dict], fixed: int, log: dict[date, set[str]], until: date,
            final: dict[date, tuple[int, int]] | None = None) -> list[Day]:
    """Every day that counted, from the first habit through `until`, oldest first.

    `final` holds the days already settled for good, as (done, target): they keep the target they had,
    whatever happens to your habits, goals or daily target later.
    """
    final = final or {}
    out: list[Day] = []
    firsts = [d for d in (start_day(habits), min(final, default=None)) if d is not None]
    day, points = (min(firsts) if firsts else None), 0
    while day is not None and day <= until:
        if day in final:
            done, target = final[day]
        else:
            active = current(habits, day)
            done = len(log.get(day, set()) & {h["id"] for h in active})
            target = target_for(habits, fixed, day)
        if target > 0:
            delta = result(done, target)
            points = max(0, points + delta)
            out.append(Day(day, done, target, delta, points))
        day += timedelta(days=1)
    return out


def to_settle(days, settled: dict, today: date) -> list[Day]:
    """Days older than yesterday that aren't settled for good yet."""
    return [d for d in days if d.day < today - timedelta(days=1) and d.day not in settled]


# ------------------------------------------------------------------ ranks
@dataclass(frozen=True)
class Rank:
    medal: int    # 0 (Rookie) .. TOP (Eternal)
    stars: int    # 1..5 below the top medal, 0 on it
    number: int   # the top medal counts up instead: 1, 2, 3...
    into: int     # points into the current star
    points: int

    @property
    def name(self) -> str:
        return MEDALS[self.medal]

    @property
    def label(self) -> str:
        """'Knight 3' (three stars), or 'Eternal 2' at the top."""
        return f"{self.name} {self.stars or self.number}"

    @property
    def step(self) -> int:
        """Comparable across ranks: one step per star."""
        return self.medal * STARS + max(self.stars, self.number)

    @property
    def fraction(self) -> float:
        return self.into / STAR_POINTS

    @property
    def to_next(self) -> int:
        return STAR_POINTS - self.into

    @property
    def next_label(self) -> str:
        return rank_of(self.points + self.to_next).label


def rank_of(points: int) -> Rank:
    points = max(0, int(points))
    if points >= TOP_FLOOR:
        number, into = divmod(points - TOP_FLOOR, STAR_POINTS)
        return Rank(TOP, 0, number + 1, into, points)
    medal, rest = divmod(points, STARS * STAR_POINTS)
    star, into = divmod(rest, STAR_POINTS)
    return Rank(medal, star + 1, 0, into, points)


@dataclass(frozen=True)
class Standing:
    """Where you are: points through yesterday, and today's match so far."""
    points: int
    rank: Rank
    today_done: int
    today_target: int
    pending: int        # what today earns if it ended now
    days: tuple         # the days that counted (Day), oldest first

    @property
    def preview(self) -> Rank:
        """Your rank once today counts, if it ended now."""
        return rank_of(self.points + self.pending)

    @property
    def last(self) -> Day | None:
        return self.days[-1] if self.days else None


def signed(points: int) -> str:
    return f"+{points}" if points > 0 else str(points)


def half_of(target: int) -> int:
    """The fewest habits that earn +15."""
    return (target + 1) // 2


def today_line(standing: Standing) -> str:
    """'7 of 11 habits today: +15 so far. 11 for +30.'"""
    s = standing
    head = f"{s.today_done} of {s.today_target} habits today"
    if s.pending == WIN:
        return f"{head}. Target reached: +{WIN} tonight!"
    if s.pending == HALF:
        return f"{head}: +{HALF} so far. {s.today_target} for +{WIN}."
    return f"{head}: {LOSS} so far. {half_of(s.today_target)} for +{HALF}, {s.today_target} for +{WIN}."


def tip(standing: Standing) -> str:
    """The rank medal's tooltip."""
    r = standing.rank
    return (f"Habit rank: {r.label}  ·  {standing.points:,} points\n{r.to_next} points to {r.next_label}\n"
            f"{today_line(standing)}\nClick to open your habits")


def standing(habits: list[dict], fixed: int, log: dict[date, set[str]], today: date,
             final: dict[date, tuple[int, int]] | None = None) -> Standing | None:
    """None when you track no habits today."""
    active = current(habits, today)
    if not active:
        return None
    days = history(habits, fixed, log, today - timedelta(days=1), final)
    points = days[-1].points if days else 0
    done = len(log.get(today, set()) & {h["id"] for h in active})
    target = target_for(habits, fixed, today)
    return Standing(points, rank_of(points), done, target, result(done, target), tuple(days))


def points_on(days, day: date) -> int:
    """Points after a day (0 before the first)."""
    points = 0
    for d in days:
        if d.day > day:
            break
        points = d.points
    return points


def news(days, since: date, until: date) -> tuple[list[Day], Rank, Rank]:
    """The days after `since` through `until`, with your rank before and after them (for the morning card)."""
    fresh = [d for d in days if since < d.day <= until]
    return fresh, rank_of(points_on(days, since)), rank_of(points_on(days, until))


# ------------------------------------------------------------------ the month, like the spreadsheet
@dataclass(frozen=True)
class Month:
    year: int
    month: int
    days: int
    rows: tuple          # (habit, times done this month, its goal this month) for every habit tracked in it
    per_day: tuple       # habits done on each day of the month
    done: int            # all ticks, each habit counted up to its goal
    goal: int
    rate: float          # success rate, 0..1
    verdict: str


def verdict(rate: float) -> str:
    return next(text for floor, text in VERDICTS if rate > floor or floor == 0.0)


def month_view(habits: list[dict], log: dict[date, set[str]], year: int, month: int) -> Month:
    """A habit tracked for only part of the month (added or retired in it) gets that share of its goal."""
    n = days_in_month(year, month)
    dates = [date(year, month, 1) + timedelta(days=i) for i in range(n)]
    rows, done_total, goal_total, tracked = [], 0, 0, set()
    for h in habits:
        days_on = [i for i, d in enumerate(dates) if is_active(h, d)]
        if not days_on:
            continue
        tracked.update(days_on)
        goal = month_goal(h, year, month)
        if len(days_on) < n:
            goal = max(1, min(len(days_on), round(goal * len(days_on) / n)))
        times = sum(1 for d in dates if h["id"] in log.get(d, ()))
        rows.append((h, times, goal))
        done_total += min(times, goal)
        goal_total += goal
    ids = {h["id"] for h, _t, _g in rows}
    per_day = [len(log.get(d, set()) & ids) for d in dates]
    window = per_day[min(tracked):max(tracked) + 1] if tracked else []
    rate = success_rate(window, done_total, goal_total)
    return Month(year, month, n, tuple(rows), tuple(per_day), done_total, goal_total, rate, verdict(rate))


def success_rate(per_day: list[int], done: int, goal: int) -> float:
    """The spreadsheet's success rate, over the days habits were tracked: three parts pace (your goals,
    for the days so far) and one part momentum (the last three days against an average day). The days so
    far run to the last day with a tick; with fewer than three of them, momentum counts as half."""
    n = len(per_day)
    elapsed = max((i + 1 for i, c in enumerate(per_day) if c), default=0)
    pace = min(1.0, done / goal * n / elapsed) if goal and elapsed else 0.0
    if elapsed < 3 or not goal:
        momentum = 0.5
    else:
        momentum = min(1.0, sum(per_day[elapsed - 3:elapsed]) / 3 / (goal / n))
    return (momentum + 3 * pace) / 4


# ------------------------------------------------------------------ importing a spreadsheet
MAX_XML = 40_000_000  # bytes: a habit tracker is a few MB at most
_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


def _col(ref: str) -> tuple[int, int]:
    """'AB15' -> (15, 28)."""
    letters = "".join(ch for ch in ref if ch.isalpha())
    col = 0
    for ch in letters.upper():
        col = col * 26 + ord(ch) - 64
    return int(ref[len(letters):]), col


def _xml(z: zipfile.ZipFile, name: str) -> ET.Element:
    if z.getinfo(name).file_size > MAX_XML:
        raise ValueError("This file is too big to be a habit tracker.")
    return ET.fromstring(z.read(name))


def _cells(z: zipfile.ZipFile, sheet: str, strings: list[str]) -> dict[tuple[int, int], str]:
    out = {}
    for c in _xml(z, sheet).iter(f"{_NS}c"):
        ref, kind = c.get("r"), c.get("t")
        v, inline = c.find(f"{_NS}v"), c.find(f"{_NS}is")
        if kind == "s" and v is not None and v.text and v.text.isdigit() and int(v.text) < len(strings):
            text = strings[int(v.text)]
        elif kind == "inlineStr" and inline is not None:
            text = "".join(t.text or "" for t in inline.iter(f"{_NS}t"))
        elif v is not None and v.text is not None and kind not in ("e",):
            text = v.text
        else:
            continue
        if ref and text.strip():
            out[_col(ref)] = text.strip()
    return out


def read_tracker(path: Path) -> list[tuple[str, int]]:
    """The habits and monthly goals in a habit tracker spreadsheet (.xlsx), such as TemplateLab's monthly
    one: a HABIT column with a GOAL column beside it. Raises ValueError when it finds none."""
    try:
        z = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError("Pomo couldn't open that file as an Excel workbook (.xlsx).") from exc
    with z:
        names = set(z.namelist())
        strings = []
        if "xl/sharedStrings.xml" in names:
            for si in _xml(z, "xl/sharedStrings.xml").iter(f"{_NS}si"):
                strings.append("".join(t.text or "" for t in si.iter(f"{_NS}t")))
        sheets = []
        try:
            book = _xml(z, "xl/workbook.xml")
            rels = _xml(z, "xl/_rels/workbook.xml.rels")
            targets = {r.get("Id"): r.get("Target", "") for r in rels}
            for sheet in book.iter(f"{_NS}sheet"):
                target = targets.get(sheet.get(_REL), "").lstrip("/")
                sheets.append(target if target.startswith("xl/") else f"xl/{target}")
        except (KeyError, ET.ParseError):
            pass
        sheets += sorted(n for n in names if n.startswith("xl/worksheets/sheet") and n not in sheets)
        for sheet in sheets:
            if sheet not in names:
                continue
            try:
                found = _habits_in(_cells(z, sheet, strings))
            except ET.ParseError:
                continue
            if found:
                return found
    raise ValueError("Pomo couldn't find a HABIT column with habits under it in that file.")


def _habits_in(cells: dict[tuple[int, int], str]) -> list[tuple[str, int]]:
    header = next(((r, c) for (r, c), text in sorted(cells.items()) if text.upper() == "HABIT"), None)
    if header is None:
        return []
    row, col = header
    goal_col = min((c for (r, c), text in cells.items() if r == row and c > col and text.upper() == "GOAL"),
                   default=None)
    found, blank = [], 0
    last = max(r for r, _c in cells)
    for r in range(row + 1, last + 1):
        name = cells.get((r, col), "")
        if not name:
            blank += 1
            if blank > 12:
                break
            continue
        blank = 0
        if len(name) > NAME_MAX:  # the template's instructions under the list
            break
        try:
            float(name)
            continue  # a number, not a habit
        except ValueError:
            pass
        goal = cells.get((r, goal_col), "") if goal_col else ""
        try:
            goal_n = round(float(goal))
        except ValueError:
            goal_n = GOAL_MAX
        found.append((" ".join(name.split()), _goal(goal_n)))
        if len(found) >= MAX_HABITS:
            break
    return found
