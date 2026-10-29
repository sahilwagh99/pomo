"""The learning loop (curious first, teach Pomo, review cards), the focus ladder, riding the urge, Study with Pomo."""
import random
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from PySide6.QtCore import QPointF
from PySide6.QtGui import QImage, QPainter

from pomodoro import learnloop
from pomodoro.config import Settings
from pomodoro.pomo import Pomo
from pomodoro.storage import Storage

DAY = date(2026, 10, 29)


class CardTests(unittest.TestCase):
    def test_reviews_spread_out_and_a_miss_starts_over(self):
        box, due = learnloop.next_review(0, True, DAY)
        self.assertEqual((box, due), (1, DAY + timedelta(days=3)))
        box, due = learnloop.next_review(box, True, DAY)
        self.assertEqual((box, due), (2, DAY + timedelta(days=7)))
        box, due = learnloop.next_review(4, True, DAY)
        self.assertEqual((box, due), (4, DAY + timedelta(days=30)))  # the longest gap
        self.assertEqual(learnloop.next_review(3, False, DAY), (0, DAY + timedelta(days=1)))
        self.assertEqual(learnloop.first_due(DAY), DAY + timedelta(days=1))

    def test_reading_the_ais_cards(self):
        text = ("Here you go:\n1. Q: What does a JOIN do? || A: Combines rows from two tables\n"
                "**Q:** Which JOIN keeps every left row? || **A:** LEFT JOIN\n"
                "q. Why use an alias? || a. Shorter names\nnot a card\nQ: one more || A: too many")
        cards = learnloop.parse_cards(text)
        self.assertEqual(len(cards), 3)
        self.assertEqual(cards[0], ("What does a JOIN do?", "Combines rows from two tables"))
        self.assertEqual(cards[1], ("Which JOIN keeps every left row?", "LEFT JOIN"))
        self.assertEqual(learnloop.parse_cards("nothing useful"), [])

    def test_the_curious_question_is_cleaned_up(self):
        self.assertEqual(learnloop.clean_question('Question: "Why can a list hold itself?"\nMore text'),
                         "Why can a list hold itself?")
        self.assertIn("Step: Loops", learnloop.context("Data analyst", "Loops", "Python basics"))

    def test_cards_and_urges_are_kept(self):
        with tempfile.TemporaryDirectory() as folder:
            storage = Storage(Path(folder) / "history.db")
            card = storage.add_card("SQL", "What does a JOIN do?", "Combines tables", DAY)
            self.assertEqual([c.id for c in storage.cards(due_by=DAY)], [card.id])
            self.assertEqual(storage.cards(due_by=DAY - timedelta(days=1)), [])
            storage.review_card(card.id, 2, DAY + timedelta(days=7))
            self.assertEqual(storage.cards(due_by=DAY), [])
            self.assertEqual(storage.cards()[0].box, 2)
            storage.add_urge(True)
            storage.add_urge(False)
            self.assertEqual(storage.urges(since=date.today()), (1, 2))
            storage.delete_card(card.id)
            self.assertEqual(storage.cards(), [])
            storage.close()


class LadderTests(unittest.TestCase):
    def setUp(self):
        self.s = Settings()
        self.s.ladder_on, self.s.ladder_min, self.s.ladder_goal = True, 10, 20

    def test_three_clean_sessions_climb_and_two_misses_step_down(self):
        s = self.s
        self.assertIsNone(learnloop.ladder_after(s, 10, True))
        self.assertIsNone(learnloop.ladder_after(s, 10, True))
        self.assertEqual(learnloop.ladder_after(s, 10, True), "up")
        self.assertEqual(s.ladder_min, 15)
        self.assertIsNone(learnloop.ladder_after(s, 15, False))
        self.assertEqual(learnloop.ladder_after(s, 15, False), "down")
        self.assertEqual(s.ladder_min, 10)

    def test_it_stops_at_the_goal_and_ignores_other_lengths(self):
        s = self.s
        s.ladder_min = 20
        for _ in range(3):
            self.assertIsNone(learnloop.ladder_after(s, 20, True))
        self.assertEqual(s.ladder_min, 20)
        self.assertIn("your goal", learnloop.ladder_text(s))
        self.assertIsNone(learnloop.ladder_after(s, 10, False))  # a starter session doesn't count
        self.assertEqual(s.ladder_misses, 0)
        s.ladder_on = False
        self.assertIsNone(learnloop.ladder_after(s, 20, True))
        self.assertEqual(learnloop.ladder_text(s), "")

    def test_settings_keep_the_ladder_sane(self):
        s = Settings()
        s.ladder_min, s.ladder_goal, s.ladder_streak = 300, 60, -2
        s.curious = {"task": "Loops", "question": "Why?", "bad": 3}
        s.clamp()
        self.assertEqual((s.ladder_min, s.ladder_goal, s.ladder_streak), (60, 60, 0))
        self.assertEqual(s.curious, {"task": "Loops", "question": "Why?"})


class WindowTests(unittest.TestCase):
    def test_pomo_reads_his_book(self):
        pomo = Pomo(random.Random(1))
        pomo.reading = True
        image = QImage(160, 140, QImage.Format_ARGB32)
        for _ in range(500):  # past a page turn
            pomo.update(0.02)
        image.fill(0)
        p = QPainter(image)
        pomo.paint(p, QPointF(80, 90), 90)
        p.end()
        self.assertGreater(pomo.ey, 0.5)  # eyes down on the page
        self.assertTrue(pomo.is_grooving() or not pomo.is_calm())  # it keeps moving, gently

    def test_the_review_window_and_the_urge_card(self):
        from types import SimpleNamespace

        from pomodoro.learn_windows import RecallWindow, UrgeCard
        from pomodoro.theme import theme_for

        with tempfile.TemporaryDirectory() as folder:
            storage = Storage(Path(folder) / "history.db")
            storage.add_card("SQL", "What does a JOIN do?", "Combines tables", date.today())
            changed = []
            ctrl = SimpleNamespace(storage=storage, theme=theme_for("light"), icon=None,
                                   engine=SimpleNamespace(task="SQL"), cards_changed=lambda: changed.append(1))
            from PySide6.QtGui import QIcon
            ctrl.icon = QIcon()
            recall = RecallWindow(ctrl)
            recall.load()
            self.assertEqual(recall.question.text(), "What does a JOIN do?")
            recall._reveal()
            self.assertEqual(recall.answer.text(), "Combines tables")
            recall._grade(True)
            self.assertIn("All done", recall.question.text())
            self.assertEqual(storage.cards()[0].box, 1)
            self.assertEqual(changed, [1])
            urge = UrgeCard(ctrl)
            urge.start()
            self.assertFalse(urge.back.isEnabled())  # ride it out first
            image = urge.grab()
            self.assertFalse(image.isNull())
            done = []
            urge.finished.connect(done.append)
            urge._end(True)
            self.assertEqual((done, storage.urges(since=date.today())), ([True], (1, 1)))
            recall.close()
            storage.close()


if __name__ == "__main__":
    unittest.main()
