"""The learning loop: curious first, teach Pomo, recall it later. And the focus ladder.

Interest grows from curiosity and from feeling capable, so each step on your path gets three
small moments:

- Curious first: before you start, Pomo asks one intriguing question the step will answer, and
  you can guess. Trying to answer before learning helps you learn (the pretesting effect).
- Teach Pomo: after it, you explain it to Pomo in simple words and he asks one follow-up, like a
  curious beginner. Explaining to someone is one of the best ways to understand (the protégé effect).
- Recall: what you taught becomes three review cards, asked again after 1, 3, 7, 14 and 30 days.
  Remembering beats re-reading, and getting them right is how "I'm bad at this" becomes "I get this".

The focus ladder trains attention like a muscle: sessions start short and grow 5 minutes after
three clean ones in a row, and step back after two stopped early.

This module is plain logic and prompts; the windows are in learn_windows.py.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

INTERVALS = (1, 3, 7, 14, 30)  # days until a card comes back, box by box
CARDS_PER_TEACH = 3

LADDER_STEP = 5      # minutes added (or taken off)
LADDER_UP_AFTER = 3  # clean sessions in a row at this length
LADDER_DOWN_AFTER = 2  # sessions stopped early in a row
LADDER_FLOOR = 5

CURIOUS_PROMPT = """You spark curiosity just before a study session. You get the learner's goal and the step they're about to study. Write ONE short question (under 25 words) that this step will help them answer: something surprising, puzzling or practical that makes them want to find out, not a definition quiz. Use simple English. Reply with the question only, nothing else."""

TEACH_PROMPT = """You are Pomo, a friendly matcha mochi, playing a curious beginner whom the learner is teaching. They just studied the step named in the first message. English may not be their first language: never comment on their English here, only on the ideas.

When they explain it, reply in under 80 words and plain text: first one specific thing their explanation made clear to you, then one gentle follow-up question a curious beginner would ask, that makes them think one step deeper. If something they said seems wrong, ask about it as a question ("Wait, I thought...?") instead of saying they're wrong.

When they answer your follow-up, thank them warmly, sum up what you learned in one sentence, and ask nothing more."""

CARDS_PROMPT = f"""Make {CARDS_PER_TEACH} short recall questions for spaced review, from what the learner taught about the step below. Each question tests one key idea, in simple English, answerable in a few words. Output exactly {CARDS_PER_TEACH} lines in this form and nothing else:
Q: <question> || A: <answer>"""


# ------------------------------------------------------------------ review cards
def next_review(box: int, got_it: bool, today: date) -> tuple[int, date]:
    """After a review: the card's new box, and when it comes back."""
    box = min(box + 1, len(INTERVALS) - 1) if got_it else 0
    return box, today + timedelta(days=INTERVALS[box])


def first_due(today: date) -> date:
    return today + timedelta(days=INTERVALS[0])


def parse_cards(text: str) -> list[tuple[str, str]]:
    """'Q: ... || A: ...' lines from the AI (forgiving about extra words and numbering)."""
    cards = []
    for line in text.splitlines():
        line = line.replace("*", "").strip()  # models like to bold the labels
        match = re.search(r"Q\s*[:.]\s*(.+?)\s*\|\|\s*A\s*[:.]\s*(.+)", line, re.IGNORECASE)
        if match:
            q, a = match.group(1).strip(), match.group(2).strip()
            if q and a:
                cards.append((q[:200], a[:200]))
    return cards[:CARDS_PER_TEACH]


def clean_question(text: str) -> str:
    """The curious question, without quotes, labels or anything after it."""
    line = next((ln.strip() for ln in text.strip().splitlines() if ln.strip()), "")
    line = re.sub(r"^(question|q)\s*[:.]\s*", "", line, flags=re.IGNORECASE).strip().strip('"“”*')
    return line[:220]


def context(goal: str, step: str, milestone: str = "") -> str:
    """What the AI is told about the step."""
    parts = [f"Goal: {goal}" if goal else "", f"Milestone: {milestone}" if milestone else "", f"Step: {step}"]
    return "\n".join(p for p in parts if p)


# ------------------------------------------------------------------ the focus ladder
def ladder_after(settings, planned_min: int, completed: bool) -> str | None:
    """A focus session ended. Returns "up" or "down" when the ladder moved (settings.ladder_min changed)."""
    s = settings
    if not s.ladder_on or planned_min != s.ladder_min:
        return None  # a starter session, or a length you set yourself: it doesn't count
    if completed:
        s.ladder_streak += 1
        s.ladder_misses = 0
        if s.ladder_streak >= LADDER_UP_AFTER and s.ladder_min < s.ladder_goal:
            s.ladder_min = min(s.ladder_goal, s.ladder_min + LADDER_STEP)
            s.ladder_streak = 0
            return "up"
        return None
    s.ladder_misses += 1
    s.ladder_streak = 0
    if s.ladder_misses >= LADDER_DOWN_AFTER and s.ladder_min > LADDER_FLOOR:
        s.ladder_min = max(LADDER_FLOOR, s.ladder_min - LADDER_STEP)
        s.ladder_misses = 0
        return "down"
    return None


def ladder_text(settings) -> str:
    """'Focus ladder: 15 min, 2 of 3 clean sessions to 20 min' (or that you've reached your goal)."""
    s = settings
    if not s.ladder_on:
        return ""
    if s.ladder_min >= s.ladder_goal:
        return f"Focus ladder: {s.ladder_min} min, your goal. Well done!"
    return (f"Focus ladder: {s.ladder_min} min, {s.ladder_streak} of {LADDER_UP_AFTER} clean sessions "
            f"to {min(s.ladder_goal, s.ladder_min + LADDER_STEP)} min")
