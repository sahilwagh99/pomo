"""Your path: what you're studying toward, in steps small enough to start.

Not knowing what to study next is what makes focus slip and study get put off. A path is one goal,
tried for a set number of weeks (an experiment you can change, not a life decision), split into
milestones and concrete steps that each take one to three focus sessions. Pomo always knows today's
step: it offers it when you sit down, counts your pomodoros on it, asks if it's done once you've given
it the time you planned, and at the end of the weeks asks whether to keep going or change direction.

The path is plain data kept in settings.study_path (no Qt here), so it's easy to check and test.
"""
from __future__ import annotations

from datetime import date, timedelta

DEFAULT_WEEKS = 8
EXTEND_WEEKS = 4      # "Keep going" at the review adds this many weeks
MAX_MILESTONES = 8
MAX_STEPS = 12        # per milestone
MAX_POMODOROS = 6     # per step: anything bigger should be split


def _text(value, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _int(value, lo: int, hi: int, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(lo, min(hi, value))


def _day(value) -> str:
    try:
        return date.fromisoformat(value).isoformat() if isinstance(value, str) and value else ""
    except ValueError:
        return ""


def clean(raw) -> dict:
    """A tidy path from whatever was saved (or {} when there's no goal)."""
    if not isinstance(raw, dict) or not _text(raw.get("goal"), 120):
        return {}
    milestones = []
    for m in raw.get("milestones", []) if isinstance(raw.get("milestones"), list) else []:
        if not isinstance(m, dict) or not _text(m.get("title"), 80):
            continue
        steps = []
        for st in m.get("steps", []) if isinstance(m.get("steps"), list) else []:
            if not isinstance(st, dict) or not _text(st.get("title"), 120):
                continue
            steps.append({
                "title": _text(st["title"], 120),
                "pomodoros": _int(st.get("pomodoros"), 1, MAX_POMODOROS, 1),
                "spent": _int(st.get("spent"), 0, 999, 0),
                "done": st.get("done") is True,
                "done_on": _day(st.get("done_on")),
            })
        milestones.append({"title": _text(m["title"], 80), "steps": steps[:MAX_STEPS]})
    return {
        "goal": _text(raw["goal"], 120),
        "why": _text(raw.get("why"), 240),
        "weeks": _int(raw.get("weeks"), 1, 52, DEFAULT_WEEKS),
        "started": _day(raw.get("started")) or date.today().isoformat(),
        "snoozed": _day(raw.get("snoozed")),
        "milestones": milestones[:MAX_MILESTONES],
    }


def make(goal: str, why: str, weeks: int, milestones: list[dict], today: date) -> dict:
    """A fresh path: milestones are {"title", "steps": [{"title", "pomodoros"}]}."""
    return clean({"goal": goal, "why": why, "weeks": weeks, "started": today.isoformat(), "milestones": milestones})


# ------------------------------------------------------------------ reading it
def steps(path: dict) -> list[tuple[int, int, dict]]:
    return [(mi, si, st) for mi, m in enumerate(path.get("milestones", [])) for si, st in enumerate(m["steps"])]


def next_step(path: dict) -> tuple[int, int, dict] | None:
    """Today's step: the first one not done yet."""
    return next(((mi, si, st) for mi, si, st in steps(path) if not st["done"]), None)


def progress(path: dict) -> tuple[int, int]:
    all_steps = steps(path)
    return sum(1 for *_ij, st in all_steps if st["done"]), len(all_steps)


def milestone_progress(milestone: dict) -> tuple[int, int]:
    return sum(1 for st in milestone["steps"] if st["done"]), len(milestone["steps"])


def week_of(path: dict, today: date) -> tuple[int, int]:
    """(this week, of how many): week 1 is the first seven days."""
    started = date.fromisoformat(path["started"])
    return max(1, (today - started).days // 7 + 1), path["weeks"]


def end_day(path: dict) -> date:
    return date.fromisoformat(path["started"]) + timedelta(days=7 * path["weeks"])


def review_due(path: dict, today: date) -> bool:
    """The weeks are up: time to decide whether to keep going (asked at most once a day)."""
    return bool(path) and today >= end_day(path) and path.get("snoozed") != today.isoformat()


def step_label(step: dict) -> str:
    """'NumPy basics (1 of 2 pomodoros)'."""
    n = step["pomodoros"]
    return f"{step['title']} ({min(step['spent'], n)} of {n} {'pomodoro' if n == 1 else 'pomodoros'})"


# ------------------------------------------------------------------ changing it
def find_step(path: dict, title: str) -> tuple[int, int, dict] | None:
    """A step not done yet with this title (the session's task), today's step first."""
    wanted = title.strip().casefold()
    if not wanted:
        return None
    first = next_step(path)
    if first is not None and first[2]["title"].casefold() == wanted:
        return first
    return next(((mi, si, st) for mi, si, st in steps(path) if not st["done"] and st["title"].casefold() == wanted),
                None)


def count_session(path: dict, task: str) -> tuple[int, int, dict] | None:
    """A focus session on a step finished: one more pomodoro on it. Returns the step, if it was one."""
    found = find_step(path, task)
    if found is not None:
        found[2]["spent"] += 1
    return found


def set_done(path: dict, mi: int, si: int, done: bool, today: date) -> bool:
    """Tick a step off (or back on). True if that finished its milestone."""
    step = path["milestones"][mi]["steps"][si]
    step["done"] = done
    step["done_on"] = today.isoformat() if done else ""
    done_n, total = milestone_progress(path["milestones"][mi])
    return done and done_n == total


def keep_going(path: dict, today: date) -> None:
    """At the review: a few more weeks on this path."""
    path["weeks"] = min(52, max(path["weeks"], (today - date.fromisoformat(path["started"])).days // 7) + EXTEND_WEEKS)
    path["snoozed"] = ""


def snooze_review(path: dict, today: date) -> None:
    path["snoozed"] = today.isoformat()


# ------------------------------------------------------------------ words
def summary(path: dict, today: date) -> str:
    """For Ask Pomo's context: the goal, where you are, today's step and what's next."""
    if not path:
        return "Study path: none yet."
    week, weeks = week_of(path, today)
    done, total = progress(path)
    lines = [f"Study path: \"{path['goal']}\"" + (f" (why: {path['why']})" if path["why"] else "")
             + f", week {week} of {weeks}, {done} of {total} steps done."]
    nxt = next_step(path)
    if nxt is not None:
        mi, _si, st = nxt
        lines.append(f"Today's step: {step_label(st)}, in milestone \"{path['milestones'][mi]['title']}\".")
    elif total:
        lines.append("Every step on the path is done.")
    parts = []
    for m in path["milestones"]:
        d, t = milestone_progress(m)
        parts.append(f"{m['title']} ({d}/{t})")
    if parts:
        lines.append("Milestones: " + ", ".join(parts) + ".")
    if review_due(path, today):
        lines.append("The path's weeks are up: it's time to decide whether to keep going or change direction.")
    return " ".join(lines)
