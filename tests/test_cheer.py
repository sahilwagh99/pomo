"""Pomo cheering you on during focus: when, and how gently."""
import random
import unittest

from pomodoro import cheer


class WhenTests(unittest.TestCase):
    def run_session(self, minutes: int) -> list[tuple[int, str]]:
        """Every moment Pomo cheers in a session, with the seconds left when it does."""
        done, said = set(), []
        total = minutes * 60
        for left in range(total, -1, -1):
            moment = cheer.due(total, left, done)
            if moment:
                said.append((left, moment))
        return said

    def test_a_standard_pomodoro(self):
        self.assertEqual(self.run_session(25), [(750, "halfway"), (300, "five_left"), (60, "last_minute")])

    def test_short_sessions_get_less(self):
        self.assertEqual(self.run_session(10), [(300, "halfway"), (60, "last_minute")])  # 5 left would be halfway
        self.assertEqual(self.run_session(5), [(150, "halfway")])
        self.assertEqual(self.run_session(3), [])

    def test_once_each_and_skipping_ahead(self):
        done = set()
        self.assertEqual(cheer.due(1500, 40, done), "last_minute")  # time typed down to 40 s: just the latest
        self.assertEqual(done, {"halfway", "five_left", "last_minute"})
        self.assertIsNone(cheer.due(1500, 30, done))
        self.assertIsNone(cheer.due(0, 0, set()))

    def test_words(self):
        rng = random.Random(1)
        for moment in cheer.MOMENTS:
            for _ in range(20):
                text = cheer.line(moment, "Thesis", rng)
                self.assertTrue(text)
                self.assertLessEqual(len(text), 40)  # a little bubble, not a paragraph
                self.assertNotIn("—", text)
        long_task = "Write the whole literature review chapter"
        self.assertTrue(all(long_task not in cheer.line("halfway", long_task, rng) for _ in range(20)))


if __name__ == "__main__":
    unittest.main()
