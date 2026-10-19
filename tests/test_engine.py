import unittest
from datetime import datetime, timedelta

from PySide6.QtCore import QCoreApplication

from pomodoro.config import Settings
from pomodoro.engine import Mode, State, TimerEngine

_app = QCoreApplication.instance() or QCoreApplication([])


class FakeClock:
    def __init__(self):
        self.t = 1000.0
        self.start_wall = datetime(2026, 10, 19, 9, 0, 0)

    def __call__(self) -> float:
        return self.t

    def now(self) -> datetime:
        return self.start_wall + timedelta(seconds=self.t - 1000.0)

    def advance(self, seconds: float) -> None:
        self.t += seconds


def make(**overrides):
    settings = Settings()
    for key, value in overrides.items():
        setattr(settings, key, value)
    clock = FakeClock()
    engine = TimerEngine(settings, clock=clock, now=clock.now)
    logged, finished = [], []
    engine.session_logged.connect(logged.append)
    engine.finished.connect(lambda *args: finished.append(args))
    return engine, clock, logged, finished


def run_out(engine: TimerEngine, clock: FakeClock) -> None:
    clock.advance(engine.remaining)
    engine._on_tick()


class EngineTests(unittest.TestCase):
    def test_counts_down_and_completes_focus(self):
        e, clock, logged, finished = make(auto_start_breaks=False)
        e.start()
        clock.advance(600)
        self.assertAlmostEqual(e.remaining, 900)
        clock.advance(900)
        e._on_tick()
        self.assertEqual(len(logged), 1)
        rec = logged[0]
        self.assertTrue(rec.completed)
        self.assertEqual(rec.mode, "focus")
        self.assertEqual(rec.actual_sec, 1500)
        self.assertEqual(rec.started_at, datetime(2026, 10, 19, 9, 0, 0))
        self.assertEqual(e.mode, Mode.SHORT)
        self.assertEqual(e.state, State.FINISHED)
        self.assertEqual(e.cycle_index, 1)
        self.assertEqual(finished[0], (Mode.FOCUS, Mode.SHORT, False))

    def test_pause_excludes_paused_time(self):
        e, clock, logged, _ = make()
        e.start()
        clock.advance(300)
        e.pause()
        clock.advance(1000)
        self.assertAlmostEqual(e.remaining, 1200)
        e.start()
        clock.advance(100)
        self.assertAlmostEqual(e.remaining, 1100)
        self.assertAlmostEqual(e.elapsed, 400)

    def test_long_break_after_cycle_then_cycle_resets(self):
        e, clock, logged, finished = make(sessions_before_long=2, auto_start_breaks=True, auto_start_focus=True)
        e.start()
        run_out(e, clock)
        self.assertEqual((e.mode, e.state), (Mode.SHORT, State.RUNNING))
        run_out(e, clock)
        self.assertEqual((e.mode, e.state), (Mode.FOCUS, State.RUNNING))
        run_out(e, clock)
        self.assertEqual(e.mode, Mode.LONG)
        self.assertEqual(e.cycle_index, 2)
        run_out(e, clock)
        self.assertEqual(e.mode, Mode.FOCUS)
        self.assertEqual(e.cycle_index, 0)
        self.assertEqual([r.mode for r in logged], ["focus", "short", "focus", "long"])

    def test_reset_logs_partial_focus_only_after_a_minute(self):
        e, clock, logged, _ = make()
        e.start()
        clock.advance(30)
        e.reset()
        self.assertEqual(logged, [])
        e.start()
        clock.advance(120)
        e.reset()
        self.assertEqual(len(logged), 1)
        self.assertFalse(logged[0].completed)
        self.assertEqual(logged[0].actual_sec, 120)
        self.assertEqual(e.state, State.IDLE)
        self.assertAlmostEqual(e.remaining, 1500)

    def test_skip_focus_moves_to_short_break_without_counting(self):
        e, clock, logged, _ = make()
        e.start()
        clock.advance(200)
        e.skip()
        self.assertEqual(e.mode, Mode.SHORT)
        self.assertEqual(e.cycle_index, 0)
        self.assertFalse(logged[0].completed)

    def test_skipping_long_break_resets_cycle(self):
        e, clock, _, _ = make(sessions_before_long=1, auto_start_breaks=False)
        e.start()
        run_out(e, clock)
        self.assertEqual(e.mode, Mode.LONG)
        e.skip()
        self.assertEqual((e.mode, e.cycle_index), (Mode.FOCUS, 0))

    def test_set_remaining_while_running_keeps_ring_consistent(self):
        e, clock, _, _ = make()
        e.start()
        clock.advance(600)
        e.set_remaining(120)
        self.assertAlmostEqual(e.remaining, 120)
        self.assertAlmostEqual(e.total, 720)
        clock.advance(60)
        self.assertAlmostEqual(e.fraction_left, 60 / 720)

    def test_adjust_when_idle_changes_duration_with_floor(self):
        e, _, _, _ = make()
        e.adjust(300)
        self.assertAlmostEqual(e.total, 1800)
        e.adjust(-10_000)
        self.assertAlmostEqual(e.total, 60)

    def test_apply_settings_keeps_custom_duration_unless_default_changes(self):
        e, _, _, _ = make()
        e.set_duration(600)
        e.settings.sound_enabled = False
        e.apply_settings()
        self.assertAlmostEqual(e.total, 600)
        e.settings.focus_min = 50
        e.apply_settings()
        self.assertAlmostEqual(e.total, 3000)

    def test_sleep_does_not_inflate_logged_time(self):
        e, clock, logged, _ = make()
        e.start()
        clock.advance(5000)  # e.g. laptop slept mid-session
        e._on_tick()
        self.assertEqual(logged[0].actual_sec, 1500)

    def test_shutdown_logs_running_session(self):
        e, clock, logged, _ = make()
        e.start()
        clock.advance(400)
        e.shutdown()
        self.assertEqual(len(logged), 1)
        self.assertEqual(logged[0].actual_sec, 400)

    def test_task_only_recorded_on_focus(self):
        e, clock, logged, _ = make(auto_start_breaks=True)
        e.task = "Thesis"
        e.start()
        run_out(e, clock)
        run_out(e, clock)
        self.assertEqual(logged[0].task, "Thesis")
        self.assertEqual(logged[1].task, "")


if __name__ == "__main__":
    unittest.main()
