"""The agent review queue: what your coding agents changed, turned into focus work for you.

When an agent finishes a turn that changed files, the change set lands here (settings.reviews).
Pomo suggests it as your next focus task ("Review Claude's changes in api"), shows it on the
break recap and in the right-click menu, and takes it off the list when you finish a focus
session on it. Several turns in the same project add up to one review.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath

MAX_REVIEWS = 10
LABELS = {"claude": "Claude", "codex": "Codex", "gemini": "Gemini"}


def title(review: dict) -> str:
    """The focus task: 'Review Claude's changes in api'."""
    return f"Review {LABELS.get(review.get('agent'), 'Claude')}'s changes in {review.get('project') or 'your project'}"


def lines_text(review: dict) -> str:
    return f"+{int(review.get('added') or 0)} / -{int(review.get('removed') or 0)} lines"


def files_text(review: dict, limit: int = 4) -> str:
    """'app.py, island.py and 2 more' (file names only)."""
    names: list[str] = []
    for path in review.get("files") or []:
        name = PurePosixPath(str(path).replace("\\", "/")).name or str(path)
        if name not in names:
            names.append(name)
    if not names:
        return ""
    shown = names[:limit]
    more = len(names) - len(shown)
    if more:
        return ", ".join(shown) + f" and {more} more"
    return shown[0] if len(shown) == 1 else ", ".join(shown[:-1]) + f" and {shown[-1]}"


def summary(review: dict) -> str:
    """'4 files (+120 / -30 lines): app.py, island.py, orb.py and 1 more'."""
    count = len(review.get("files") or [])
    head = f"{count} file{'' if count == 1 else 's'} ({lines_text(review)})"
    files = files_text(review)
    return f"{head}: {files}" if files else head


def add(settings, review: dict) -> dict:
    """Queue a change set. A review already waiting for the same project and agent grows instead."""
    queue = settings.reviews
    for item in queue:
        if item.get("project") == review.get("project") and item.get("agent") == review.get("agent"):
            fresh = [f for f in review.get("files") or [] if f not in item["files"]]
            item["files"] = (fresh + list(item["files"]))[:30]
            item["added"] = int(item.get("added") or 0) + int(review.get("added") or 0)
            item["removed"] = int(item.get("removed") or 0) + int(review.get("removed") or 0)
            item["at"] = review.get("at") or datetime.now().replace(microsecond=0).isoformat()
            queue.remove(item)
            queue.append(item)  # the newest goes last
            return item
    item = {"project": str(review.get("project") or ""), "agent": str(review.get("agent") or "claude"),
            "cwd": str(review.get("cwd") or ""), "files": list(review.get("files") or [])[:30],
            "added": int(review.get("added") or 0), "removed": int(review.get("removed") or 0),
            "at": review.get("at") or datetime.now().replace(microsecond=0).isoformat()}
    queue.append(item)
    del queue[:-MAX_REVIEWS]
    return item


def latest(settings) -> dict | None:
    return settings.reviews[-1] if settings.reviews else None


def find(settings, task: str) -> dict | None:
    """The waiting review whose focus task this is."""
    task = (task or "").strip()
    return next((r for r in settings.reviews if title(r) == task), None)


def remove(settings, review: dict) -> None:
    settings.reviews = [r for r in settings.reviews if r is not review and r != review]


def since(settings, when: datetime) -> list[dict]:
    """Reviews that came in (or grew) after a moment, say while you were on a break."""
    out = []
    for r in settings.reviews:
        try:
            at = datetime.fromisoformat(str(r.get("at")))
        except ValueError:
            continue
        if at >= when.replace(microsecond=0):
            out.append(r)
    return out
