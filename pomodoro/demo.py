"""Sample history for trying the dashboard (run with --demo; uses its own data folder)."""
from __future__ import annotations

import random
from datetime import date, datetime, time as dtime, timedelta

from .storage import SessionRecord, Storage

TASKS = ["Thesis writing", "Algorithms practice", "Reading papers", "Spanish lessons", "Side project", "Email and admin"]
WEIGHTS = [5, 4, 3, 2, 3, 1]


def seed_demo(storage: Storage, days: int = 120, seed: int = 7) -> None:
    if storage.count():
        return
    rng = random.Random(seed)
    today = date.today()
    now = datetime.now()
    records: list[SessionRecord] = []
    for back in range(days, -1, -1):
        day = today - timedelta(days=back)
        weekend = day.weekday() >= 5
        if rng.random() < (0.4 if weekend else 0.1):
            continue
        sessions = rng.randint(1, 5) if weekend else rng.randint(3, 10)
        t = datetime.combine(day, dtime(rng.choice([7, 8, 9, 9, 10, 13]), rng.choice([0, 15, 30])))
        for i in range(sessions):
            completed = rng.random() > 0.12
            actual = 25 * 60 if completed else rng.randint(4, 20) * 60
            records.append(SessionRecord(t, t + timedelta(seconds=actual), "focus", 25 * 60, actual, completed,
                                         rng.choices(TASKS, WEIGHTS)[0]))
            t += timedelta(seconds=actual)
            long_break = (i + 1) % 4 == 0
            minutes = 15 if long_break else 5
            records.append(SessionRecord(t, t + timedelta(minutes=minutes), "long" if long_break else "short",
                                         minutes * 60, minutes * 60, True))
            t += timedelta(minutes=minutes + rng.choice([0, 0, 5, 20, 60]))
    storage.add_many([r for r in records if r.ended_at <= now])
