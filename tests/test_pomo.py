import math
import random
import unittest

from pomodoro.engine import State
from pomodoro.pomo import POKE_WINDOW, Pomo, mood_for


def run(pomo: Pomo, seconds: float, dt: float = 1 / 30, cursor=None) -> None:
    for _ in range(int(seconds / dt)):
        pomo.update(dt, cursor)


class PomoTests(unittest.TestCase):
    def setUp(self):
        self.pomo = Pomo(random.Random(1))

    def test_mood_follows_timer_state(self):
        self.assertEqual(mood_for(State.RUNNING, False, 0), "focus")
        self.assertEqual(mood_for(State.RUNNING, True, 0), "break")
        self.assertEqual(mood_for(State.PAUSED, False, 0), "paused")
        self.assertEqual(mood_for(State.FINISHED, True, 0), "ready")
        self.assertEqual(mood_for(State.IDLE, False, 10), "idle")
        self.assertEqual(mood_for(State.IDLE, False, 600), "sleepy")

    def test_pokes_escalate_from_ouch_to_grumpy_to_dizzy(self):
        reactions = []
        for _ in range(6):
            reactions.append(self.pomo.poke())
            run(self.pomo, 0.1)
        self.assertEqual(reactions[:2], ["ouch", "ouch"])
        self.assertEqual(reactions[2:5], ["grumpy"] * 3)
        self.assertEqual(reactions[5], "dizzy")

    def test_pokes_far_apart_do_not_add_up(self):
        for _ in range(4):
            self.assertEqual(self.pomo.poke(), "ouch")
            run(self.pomo, POKE_WINDOW + 0.1)

    def test_reactions_wear_off(self):
        self.pomo.celebrate(big=True)
        self.assertEqual(self.pomo.face, "happy")
        run(self.pomo, 4.0)
        self.assertIsNone(self.pomo.reaction)
        self.assertEqual(self.pomo.face, "idle")

    def test_hop_lands_and_settles(self):
        self.pomo.hop(2.5)  # airtime is 2v/g, about 0.55 s
        run(self.pomo, 0.2)
        self.assertGreater(self.pomo.hop_y, 0)
        run(self.pomo, 0.6)
        self.assertEqual(self.pomo.hop_y, 0.0)
        run(self.pomo, 1.0)  # still before his first idle fidget at 3 s
        self.assertLess(abs(self.pomo.squish.x), 0.05)

    def test_celebration_particles_expire(self):
        self.pomo.celebrate(big=True)
        self.assertTrue(self.pomo.particles)
        run(self.pomo, 4.0)
        self.assertFalse([p for p in self.pomo.particles if p.kind in ("spark", "confetti")])

    def test_eyes_follow_the_cursor(self):
        run(self.pomo, 1.0, cursor=(5.0, 0.0))  # far to the right
        self.assertGreater(self.pomo.ex, 0.9)
        run(self.pomo, 1.0, cursor=(0.0, -5.0))  # far above
        self.assertLess(self.pomo.ey, -0.9)

    def test_simulation_stays_finite_with_jittery_frames(self):
        rng = random.Random(7)
        for i in range(3000):
            if i % 97 == 0:
                self.pomo.poke()
            if i % 400 == 0:
                self.pomo.celebrate(big=True)
            self.pomo.set_mood(rng.choice(["idle", "focus", "break", "paused", "ready", "sleepy"]))
            self.pomo.update(rng.uniform(0.0, 0.25), (rng.uniform(-4, 4), rng.uniform(-4, 4)))
        for value in (self.pomo.hop_y, self.pomo.squish.x, self.pomo.leaf.x, self.pomo.ex, self.pomo.ey):
            self.assertTrue(math.isfinite(value))
        self.assertLessEqual(len(self.pomo.particles), 80)



class WardrobeAndThemeTests(unittest.TestCase):
    def test_every_outfit_and_mood_paints(self):
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QGuiApplication, QImage, QPainter

        from pomodoro.config import OUTFITS
        from pomodoro.pomo import FACES

        app = QGuiApplication.instance() or QGuiApplication([])
        img = QImage(200, 200, QImage.Format_ARGB32)
        for outfit in OUTFITS:
            for face in FACES:
                pomo = Pomo(random.Random(2))
                pomo.outfit = outfit
                pomo.reaction = face
                pomo.signal = "busy" if face == "focus" else "alert" if face == "ready" else None
                img.fill(0)
                p = QPainter(img)
                pomo.paint(p, QPointF(100, 120), 90)
                p.end()
                self.assertNotEqual(img.pixel(100, 120) >> 24, 0, (outfit, face))  # something was drawn

    def test_every_theme_builds_its_stylesheet(self):
        import tempfile
        from pathlib import Path

        from PySide6.QtGui import QColor, QGuiApplication

        from pomodoro.theme import THEMES, build_qss, ensure_qss_assets

        app = QGuiApplication.instance() or QGuiApplication([])
        with tempfile.TemporaryDirectory() as tmp:
            for theme in THEMES.values():
                assets = ensure_qss_assets(theme, Path(tmp))
                self.assertTrue(Path(assets["switch_on"]).exists())
                self.assertIn(theme.surface, build_qss(theme, QColor(theme.focus), assets))
                self.assertTrue(theme.label)


class PlayTests(unittest.TestCase):
    def test_rubbing_back_and_forth_pets_him(self):
        pomo = Pomo(random.Random(1))
        loved = [pomo.rub(d) for d in (1, -1, 1, -1)]
        self.assertEqual(loved, [False, False, False, True])
        self.assertEqual(pomo.face, "love")
        self.assertTrue(any(p.kind == "heart" for p in pomo.particles))

    def test_slow_rubs_do_not_add_up(self):
        pomo = Pomo(random.Random(1))
        for d in (1, -1, 1, -1):
            pomo.rub(d)
            run(pomo, 0.6)
        self.assertNotEqual(pomo.reaction, "love")

    def test_twirl_and_munch(self):
        pomo = Pomo(random.Random(1))
        pomo.twirl()
        self.assertEqual(pomo.face, "happy")
        self.assertFalse(pomo.is_calm())
        run(pomo, 2.0)
        pomo.munch()
        self.assertEqual(pomo.face, "munch")
        run(pomo, 2.0)
        self.assertIsNone(pomo.reaction)


class PetalGameTests(unittest.TestCase):
    def setUp(self):
        from pomodoro.game import PetalGame
        self.game = PetalGame(420, 540, random.Random(3))
        self.game.start()
        self.game._spawn_in = 999  # place petals by hand

    def drop(self, x, golden=False):
        from pomodoro.game import Petal
        petal = Petal(x, self.game.pomo_y - 120, 100.0, 0.0, 0.0, golden, 0.0)
        self.game.petals.append(petal)
        return petal

    def step(self, seconds):
        events = []
        for _ in range(int(seconds / 0.02)):
            events += self.game.update(0.02)
        return events

    def test_catching_scores_and_golden_is_worth_three(self):
        self.drop(self.game.pomo_x)
        self.drop(self.game.pomo_x, golden=True)
        events = self.step(2.0)
        self.assertEqual([kind for kind, _ in events].count("catch"), 2)
        self.assertEqual(self.game.score, 4)

    def test_petals_away_from_pomo_are_missed(self):
        self.game.move_to(40)
        self.step(1.0)
        self.drop(380)
        events = self.step(3.0)
        self.assertIn("miss", [kind for kind, _ in events])
        self.assertEqual(self.game.score, 0)

    def test_round_ends_after_thirty_seconds(self):
        events = self.step(31.0)
        self.assertIn("end", [kind for kind, _ in events])
        self.assertFalse(self.game.running)
        self.assertEqual(self.game.update(0.1), [])


if __name__ == "__main__":
    unittest.main()
