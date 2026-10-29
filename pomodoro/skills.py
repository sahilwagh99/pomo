"""Your path as a game: XP and levels for the skill tree, and the big picture of how its steps connect.

Seeing progress is what keeps a game going, and studying rarely shows any. So focus earns XP (a
point a minute), finishing a step and teaching Pomo earn more, and the skill tree draws your path
like a game's: done steps lit up, today's step glowing with Pomo beside it, the rest still to unlock.

The big picture is the other half: confusion often comes from not seeing how the pieces fit. With
an AI key, Pomo asks for one short summary of the path, what each step lets you do, and the links
between steps in different milestones, and draws them as a map.

This module is plain logic and prompts; the windows are in skill_windows.py.
"""
from __future__ import annotations

import hashlib
import re

from . import studypath

STEP_XP = 50     # a step ticked off (and taken back if you untick it)
TEACH_XP = 20    # teaching Pomo a step
CARD_XP = 3      # a review card you got right

RANKS = ((1, "Sprout"), (3, "Apprentice"), (6, "Explorer"), (10, "Scholar"), (15, "Expert"), (21, "Sage"),
         (30, "Legend"))

MAP_PROMPT = """You draw the big picture of a learner's study path, so they see how the pieces fit and why each step matters. You get their goal, then each milestone with its steps, every step with an id like S4. English may not be their first language: use simple words.

Reply in plain text, exactly in this format and nothing else:
SUMMARY: <two short sentences: how the milestones build on each other toward the goal>
ROLE: S1 | <under 12 words: what this step lets them do>
(one ROLE line for every step)
LINK: S2 > S7 | <under 12 words: how the first step helps with the second>
(4 to 12 LINK lines, each between steps in different milestones, only real and useful connections)"""


# ------------------------------------------------------------------ XP and levels
def level_floor(level: int) -> int:
    """XP needed to reach a level: 100 for level 2, then each level takes 50 more than the last."""
    return 25 * (level - 1) * (level + 2)


def level_for(xp: int) -> tuple[int, int, int]:
    """(level, XP into it, XP the level takes)."""
    xp = max(0, xp)
    level = 1
    while level_floor(level + 1) <= xp:
        level += 1
    return level, xp - level_floor(level), level_floor(level + 1) - level_floor(level)


def rank(level: int) -> str:
    return next(name for floor, name in reversed(RANKS) if level >= floor)


def starting_xp(focus_sec: float, path: dict) -> int:
    """For someone who focused before XP existed: their focus minutes, and the steps they finished."""
    done, _total = studypath.progress(path) if path else (0, 0)
    return int(focus_sec // 60) + STEP_XP * done


def add(settings, amount: int, today: str) -> bool:
    """Earn (or, for an unticked step, lose) XP. True if that reached a new level."""
    s = settings
    before = level_for(s.xp)[0]
    s.xp = max(0, s.xp + amount)
    if s.xp_day != today:
        s.xp_day, s.xp_today = today, 0
    s.xp_today = max(0, s.xp_today + amount)
    return level_for(s.xp)[0] > before


def xp_line(settings) -> str:
    """'Level 4, Explorer: 120 XP to level 5'."""
    level, into, need = level_for(settings.xp)
    return f"Level {level}, {rank(level)}: {need - into} XP to level {level + 1}"


def step_key(title: str) -> str:
    return title.strip().casefold()


# ------------------------------------------------------------------ the big picture
def signature(path: dict) -> str:
    """Changes when steps or milestones are added, removed or renamed (not when they're ticked off)."""
    titles = [m["title"] + ">" + "|".join(st["title"] for st in m["steps"]) for m in path.get("milestones", [])]
    return hashlib.sha1("\n".join([path.get("goal", "")] + titles).encode("utf-8")).hexdigest()[:12]


def map_request(path: dict) -> str:
    """The path, as the AI gets it: every step with an id."""
    lines = [f"Goal: {path['goal']}"]
    n = 0
    for m in path["milestones"]:
        lines.append(f"\nMilestone: {m['title']}")
        for st in m["steps"]:
            n += 1
            lines.append(f"S{n}: {st['title']}")
    return "\n".join(lines)


def parse_map(text: str, path: dict) -> dict:
    """The AI's reply, as {"summary", "roles": {step: text}, "links": [[from, to, why]]} (forgiving)."""
    ids = {f"s{i}": st["title"] for i, (_mi, _si, st) in enumerate(studypath.steps(path), start=1)}
    milestone_of = {st["title"]: mi for mi, _si, st in studypath.steps(path)}
    summary, roles, links = "", {}, []
    for raw in text.splitlines():
        line = raw.replace("*", "").strip().lstrip("-• ").strip()
        if m := re.match(r"summary\s*:\s*(.+)", line, re.IGNORECASE):
            summary = m.group(1).strip()[:400]
        elif m := re.match(r"role\s*:\s*(s\d+)\s*[|:]\s*(.+)", line, re.IGNORECASE):
            title = ids.get(m.group(1).lower())
            if title:
                roles[title] = m.group(2).strip()[:120]
        elif m := re.match(r"link\s*:\s*(s\d+)\s*(?:->|>|→|to)\s*(s\d+)\s*[|:]\s*(.+)", line, re.IGNORECASE):
            a, b = ids.get(m.group(1).lower()), ids.get(m.group(2).lower())
            if a and b and a != b and milestone_of[a] != milestone_of[b] and len(links) < 16:
                links.append([a, b, m.group(3).strip()[:120]])
    return {"summary": summary, "roles": roles, "links": links}


def clean_map(raw) -> dict:
    """A saved map, checked (or {} when there's none)."""
    if not isinstance(raw, dict):
        return {}
    roles = raw.get("roles") if isinstance(raw.get("roles"), dict) else {}
    links = raw.get("links") if isinstance(raw.get("links"), list) else []
    out = {
        "sig": raw.get("sig") if isinstance(raw.get("sig"), str) else "",
        "summary": raw.get("summary")[:400] if isinstance(raw.get("summary"), str) else "",
        "roles": {k[:120]: v[:120] for k, v in roles.items() if isinstance(k, str) and isinstance(v, str)},
        "links": [[a[:120], b[:120], w[:120]] for a, b, w in
                  (x for x in links if isinstance(x, list) and len(x) == 3 and all(isinstance(v, str) for v in x))][:16],
    }
    return out if out["summary"] or out["roles"] or out["links"] else {}


def around(step: str, path: dict, pmap: dict) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """What a step builds on and what it leads to: the map's links, or else its neighbours on the path."""
    links = [ln for ln in pmap.get("links", []) if step in ln[:2]] if pmap else []
    before = [(a, why) for a, b, why in links if b == step]
    after = [(b, why) for a, b, why in links if a == step]
    if not before and not after:
        order = [st["title"] for _mi, _si, st in studypath.steps(path)]
        if step in order:
            i = order.index(step)
            before = [(order[i - 1], "the step before it")] if i > 0 else []
            after = [(order[i + 1], "the step after it")] if i + 1 < len(order) else []
    return before, after
