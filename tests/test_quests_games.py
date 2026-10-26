"""Daily quests and badges, the quest outfits, and the new mini games (Mochi Stack, Memory Match)."""
import random
import unittest
from datetime import date, datetime, timedelta

from pomodoro import garden, quests
from pomodoro.config import OUTFITS, QUEST_UNLOCKS, Settings
from pomodoro.memory import PEEK_S, MemoryMatch
from pomodoro.stack import LAND_SHARE, MOCHI_W, MochiStack
from pomodoro.storage import SessionRecord

DAY = date(2026, 10, 26)


def focus(hour: int, minute: int = 0, length: int = 25, completed: bool = True, rating: int = 0):
    start = datetime(2026, 10, 26, hour, minute)
    secs = length * 60 if completed else 600
    return SessionRecord(start, start + timedelta(seconds=secs), "focus", length * 60, secs, completed, "", rating)


def long_break(hour: int):
    start = datetime(2026, 10, 26, hour)
    return SessionRecord(start, start + timedelta(minutes=15), "long", 900, 900, True)


class PickingTests(unittest.TestCase):
    def test_same_quests_all_day_and_a_new_mix_tomorrow(self):
        keys = quests.eligible(datetime(2026, 10, 26, 8), uses_agents=True)
        today = quests.pick(DAY, keys)
        self.assertEqual(len(today), 3)
        self.assertEqual(today, quests.pick(DAY, keys))
        week = {tuple(quests.pick(DAY + timedelta(days=i), keys)) for i in range(7)}
        self.assertGreater(len(week), 3)

    def test_quests_that_fit(self):
        morning = quests.eligible(datetime(2026, 10, 26, 7), uses_agents=False)
        self.assertIn("early_bird", morning)
        self.assertNotIn("review", morning)  # no coding agents, no agent quests
        afternoon = quests.eligible(datetime(2026, 10, 26, 14), uses_agents=True)
        self.assertNotIn("early_bird", afternoon)  # too late to be early
        self.assertIn("tandem", afternoon)

    def test_a_new_day_resets(self):
        s = Settings()
        self.assertTrue(quests.roll_over(s, datetime(2026, 10, 26, 8), False))
        s.quests_done = s.quests[:1]
        quests.count(s, "park", 2)
        self.assertFalse(quests.roll_over(s, datetime(2026, 10, 26, 20), False))
        self.assertEqual(s.quest_counts, {"park": 2})
        self.assertTrue(quests.roll_over(s, datetime(2026, 10, 27, 8), False))
        self.assertEqual((s.quests_done, s.quest_counts), ([], {}))


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.s = Settings()
        self.s.daily_goal = 4

    def progress(self, key, records):
        return quests.progress(quests.QUESTS[key], self.s, records, DAY)

    def test_from_your_history(self):
        records = [focus(8), focus(9, 30), focus(10, 15, completed=False), focus(11), focus(13, length=50, rating=3),
                   long_break(14)]
        self.assertEqual(self.progress("early_bird", records), 2)
        self.assertEqual(self.progress("hat_trick", records), 2)  # the one stopped early broke the run
        self.assertEqual(self.progress("deep_dive", records), 1)
        self.assertEqual(self.progress("goal", records), 4)
        self.assertEqual(quests.target_of(quests.QUESTS["goal"], self.s), 4)
        self.assertEqual(self.progress("check_in", records), 1)
        self.assertEqual(self.progress("recharge", records), 1)
        yesterday = [SessionRecord(datetime(2026, 10, 25, 8), datetime(2026, 10, 25, 8, 25), "focus", 1500, 1500, True)]
        self.assertEqual(self.progress("early_bird", yesterday), 0)

    def test_counted_ones(self):
        quests.count(self.s, "park")
        quests.count(self.s, "park")
        self.assertEqual(self.progress("park", []), 2)
        self.assertEqual(self.progress("breathe", []), 0)

    def test_finishing_quests_earns_badges_and_outfits(self):
        s = self.s
        s.quests, s.quest_day = ["park", "breathe", "recharge"], DAY.isoformat()
        quests.count(s, "park", 2)
        done = quests.check(s, [], DAY)
        self.assertEqual([q.key for q in done], ["park"])
        self.assertEqual(quests.check(s, [], DAY), [])  # only once
        quests.count(s, "breathe")
        s.quests_total = 3
        before = s.quests_total
        done = quests.check(s, [long_break(15)], DAY)
        self.assertEqual([q.key for q in done], ["breathe", "recharge", quests.PERFECT_DAY])
        self.assertEqual(s.badges, {"park": 1, "breathe": 1, "recharge": 1, quests.PERFECT_DAY: 1})
        self.assertEqual(quests.unlocked_by(before, s.quests_total), ["starclip"])
        self.assertTrue(garden.is_unlocked("starclip", 0, s.quests_total))
        self.assertFalse(garden.is_unlocked("bunnyears", 1000, s.quests_total))  # quests, not pomodoros
        self.assertEqual(quests.next_outfit_text(s.quests_total), "10 more quests to unlock the bunny ears.")
        self.assertEqual(garden.unlock_hint("bunnyears"), "15 quests")
        self.assertEqual(garden.unlock_hint("wizard"), "100 pomodoros")

    def test_status_lines(self):
        s = self.s
        s.quests = ["park"]
        quests.count(s, "park")
        self.assertEqual(quests.status_line(quests.QUESTS["park"], s, [], DAY), "Parking pro: 1/2")
        s.quests_done = ["park"]
        self.assertEqual(quests.status_line(quests.QUESTS["park"], s, [], DAY), "Parking pro: done")

    def test_settings_stay_tidy(self):
        s = Settings()
        s.quests = ["a", "b", "c", "d", 5]
        s.quests_done = ["a", "zzz"]
        s.badges = {"park": 2, "bad": "x", "zero": 0}
        s.quest_counts = {"park": 1, "flag": True}
        s.clamp()
        self.assertEqual((s.quests, s.quests_done), (["a", "b", "c"], ["a"]))
        self.assertEqual((s.badges, s.quest_counts), ({"park": 2}, {"park": 1}))

    def test_every_quest_has_a_badge_picture(self):
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QImage, QPainter

        image = QImage(64, 64, QImage.Format_ARGB32_Premultiplied)
        for quest in [quests.PERFECT, *quests.QUESTS.values()]:
            with self.subTest(quest=quest.key):
                image.fill(0)
                p = QPainter(image)
                quests.draw_badge(p, QPointF(32, 32), 28, quest)
                p.end()
                self.assertNotEqual(image.pixel(32, 32), 0)

    def test_new_outfits_are_drawn(self):
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QImage, QPainter

        from pomodoro.pomo import Pomo

        def ink(outfit: str) -> int:
            image = QImage(120, 120, QImage.Format_ARGB32_Premultiplied)
            image.fill(0)
            pomo = Pomo(random.Random(1))
            pomo.outfit = outfit
            p = QPainter(image)
            pomo.paint(p, QPointF(60, 80), 80, ground_shadow=False)
            p.end()
            return sum(1 for y in range(0, 50) for x in range(120) if image.pixel(x, y) >> 24)

        bare = ink("none")
        for outfit in QUEST_UNLOCKS:
            with self.subTest(outfit=outfit):
                self.assertIn(outfit, OUTFITS)
                self.assertGreater(ink(outfit), bare + 40)  # something sits on his head


class StackTests(unittest.TestCase):
    def setUp(self):
        self.game = MochiStack(420, 540, random.Random(4))
        self.game.start()

    def drop_at(self, x: float) -> list[str]:
        self.game.x = x
        self.game.drop()
        events = []
        for _ in range(200):
            events += self.game.update(0.02)
            if not self.game.dropping or not self.game.running:
                break
        return events

    def test_landing_and_perfect_drops(self):
        top = self.game.tower[-1].x
        self.assertEqual(self.drop_at(top + 20), ["land"])
        self.assertEqual((self.game.height, self.game.score), (1, 1))
        self.assertEqual(self.drop_at(self.game.tower[-1].x + 2), ["perfect"])
        self.assertEqual((self.game.height, self.game.score, self.game.perfects), (2, 3, 1))
        self.assertGreater(self.game.speed, 130.0)  # each mochi glides a little faster

    def test_a_miss_ends_the_round(self):
        events = self.drop_at(self.game.tower[-1].x + MOCHI_W * LAND_SHARE + 5)
        self.assertEqual(events, ["miss", "end"])
        self.assertFalse(self.game.running)
        self.assertEqual(len(self.game.fallers), 1)
        self.assertFalse(self.game.drop())

    def test_it_glides_between_the_walls(self):
        for _ in range(500):
            self.game.update(0.02)
            self.assertTrue(MOCHI_W / 2 <= self.game.x <= 420 - MOCHI_W / 2 + 6)


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.game = MemoryMatch(random.Random(2))
        self.game.start()

    def pair(self, outfit: str) -> list[int]:
        return [i for i, c in enumerate(self.game.cards) if c.outfit == outfit]

    def test_board(self):
        outfits = [c.outfit for c in self.game.cards]
        self.assertEqual(len(outfits), 16)
        self.assertTrue(all(outfits.count(o) == 2 for o in outfits))
        self.assertNotIn("none", outfits)

    def test_match_and_miss(self):
        first = self.game.cards[0].outfit
        a, b = self.pair(first)
        other = next(i for i, c in enumerate(self.game.cards) if c.outfit != first)
        self.assertEqual(self.game.flip(a), ["flip"])
        self.assertEqual(self.game.flip(other), ["miss"])
        self.assertEqual(self.game.flip(b), [])  # wait for the miss to turn back
        for _ in range(int(PEEK_S / 0.05) + 2):  # frames come every 16 ms; a long pause is taken in steps
            self.game.update(0.05)
        self.assertFalse(self.game.cards[a].up or self.game.cards[other].up)
        self.game.flip(a)
        self.assertEqual(self.game.flip(b), ["match"])
        self.assertTrue(self.game.cards[a].matched and self.game.cards[b].matched)
        self.assertEqual(self.game.moves, 2)
        self.assertEqual(self.game.flip(a), [])  # already matched

    def test_clearing_the_board(self):
        events = []
        for outfit in dict.fromkeys(c.outfit for c in self.game.cards):
            a, b = self.pair(outfit)
            self.game.flip(a)
            events = self.game.flip(b)
        self.assertEqual(events, ["match", "win"])
        self.assertEqual((self.game.moves, self.game.pairs_left, self.game.running), (8, 0, False))


if __name__ == "__main__":
    unittest.main()
