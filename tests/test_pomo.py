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


if __name__ == "__main__":
    unittest.main()
