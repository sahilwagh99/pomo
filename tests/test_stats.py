import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from pomodoro.config import Settings
from pomodoro.orb import parse_duration
from pomodoro.stats import fmt_clock, fmt_hm, streaks, summarize
from pomodoro.storage import SessionRecord


def focus(start: datetime, minutes: float, completed=True, task="") -> SessionRecord:
    return SessionRecord(start, start + timedelta(minutes=minutes), "focus", 1500, int(minutes * 60), completed, task)


class StatsTests(unittest.TestCase):
    def test_summary_totals_and_hour_spread(self):
        d = date(2026, 10, 14)
        records = [
            focus(datetime(2026, 10, 14, 9, 50), 25, task="Read"),
            focus(datetime(2026, 10, 14, 11, 0), 10, completed=False, task="Read"),
            focus(datetime(2026, 10, 15, 14, 0), 25, task="Write"),
            SessionRecord(datetime(2026, 10, 14, 10, 15), datetime(2026, 10, 14, 10, 20), "short", 300, 300, True),
            focus(datetime(2026, 10, 1, 9, 0), 25),  # outside range
        ]
        s = summarize(records, d, d + timedelta(days=6))
        self.assertEqual(s.pomodoros, 2)
        self.assertEqual(s.interrupted, 1)
        self.assertEqual(s.focus_sec, 60 * 60)
        self.assertEqual(s.break_sec, 300)
        self.assertEqual(s.by_hour[9], 600)
        self.assertEqual(s.by_hour[10], 900)
        self.assertEqual(s.active_days, 2)
        self.assertAlmostEqual(s.completion_rate, 2 / 3)
        self.assertEqual(s.tasks[0][0], "Read")
        self.assertEqual(s.best_day.day, d)
        self.assertEqual(s.peak_hour, 14)
        self.assertEqual(len(s.days), 7)

    def test_streaks(self):
        today = date(2026, 10, 19)
        days = [today - timedelta(days=n) for n in (1, 2, 3, 6, 7, 8, 9, 10)]
        records = [focus(datetime.combine(d, datetime.min.time()), 25) for d in days]
        records.append(focus(datetime(2026, 10, 12, 9), 10, completed=False))
        self.assertEqual(streaks(records, today), (3, 5))
        self.assertEqual(streaks([], today), (0, 0))

    def test_formatting(self):
        self.assertEqual(fmt_clock(1500), "25:00")
        self.assertEqual(fmt_clock(1499.2), "25:00")
        self.assertEqual(fmt_clock(1499.0), "24:59")
        self.assertEqual(fmt_clock(3600), "1:00:00")
        self.assertEqual(fmt_hm(0), "0m")
        self.assertEqual(fmt_hm(20), "<1m")
        self.assertEqual(fmt_hm(3900), "1h 05m")

    def test_parse_duration(self):
        self.assertEqual(parse_duration("25"), 1500)
        self.assertEqual(parse_duration("25:30"), 1530)
        self.assertEqual(parse_duration("0:45"), 45)
        self.assertEqual(parse_duration("1:10:00"), 4200)
        self.assertIsNone(parse_duration(""))
        self.assertIsNone(parse_duration("10:75"))
        self.assertIsNone(parse_duration("0"))


class SettingsTests(unittest.TestCase):
    def test_load_ignores_bad_values_and_clamps(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({"focus_min": 500, "daily_goal": "eight", "theme": "neon",
                                        "auto_start_focus": True, "orb_x": 12, "unknown": 1}))
            s = Settings.load(path)
            self.assertEqual(s.focus_min, 180)
            self.assertEqual(s.daily_goal, 8)
            self.assertEqual(s.theme, "dark")
            self.assertTrue(s.auto_start_focus)
            self.assertEqual(s.orb_x, 12)
            s.save(path)
            self.assertEqual(Settings.load(path).focus_min, 180)

    def test_remember_task_keeps_recent_unique(self):
        s = Settings()
        for t in ("A", "B", "A", " "):
            s.remember_task(t)
        self.assertEqual(s.recent_tasks, ["A", "B"])
        self.assertEqual(s.current_task, "")


if __name__ == "__main__":
    unittest.main()
