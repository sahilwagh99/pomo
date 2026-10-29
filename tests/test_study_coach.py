"""The study coach: your path and today's step, the study log in English, and the app lock."""
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from pomodoro import applock, planner, quests, studylog, studypath
from pomodoro.ai import PATH_CHECK_PROMPT, PATH_START_PROMPT, SYSTEM_PROMPT, build_context, compat_tools, quick_prompt
from pomodoro.config import Settings
from pomodoro.engine import TimerEngine
from pomodoro.storage import Storage

DAY = date(2026, 10, 29)
MILESTONES = [
    {"title": "Python basics", "steps": [{"title": "Variables and loops", "pomodoros": 2},
                                         {"title": "Functions", "pomodoros": 1}]},
    {"title": "Data", "steps": [{"title": "NumPy arrays", "pomodoros": 3}]},
]


def a_path(day=DAY, weeks=8):
    return studypath.make("Junior data analyst job", "A job I enjoy", weeks, MILESTONES, day)


class PathTests(unittest.TestCase):
    def test_a_new_path_and_todays_step(self):
        path = a_path()
        self.assertEqual(studypath.progress(path), (0, 3))
        mi, si, step = studypath.next_step(path)
        self.assertEqual((mi, si, step["title"]), (0, 0, "Variables and loops"))
        self.assertEqual(studypath.step_label(step), "Variables and loops (0 of 2 pomodoros)")

    def test_sessions_count_and_ticking_off_moves_on(self):
        path = a_path()
        self.assertIsNotNone(studypath.count_session(path, "variables AND loops"))  # case doesn't matter
        self.assertIsNone(studypath.count_session(path, "Email"))
        self.assertEqual(path["milestones"][0]["steps"][0]["spent"], 1)
        self.assertFalse(studypath.set_done(path, 0, 0, True, DAY))
        self.assertTrue(studypath.set_done(path, 0, 1, True, DAY))  # the milestone's last step
        self.assertEqual(studypath.next_step(path)[2]["title"], "NumPy arrays")
        self.assertEqual(studypath.progress(path), (2, 3))
        self.assertEqual(path["milestones"][0]["steps"][0]["done_on"], DAY.isoformat())

    def test_weeks_and_the_review(self):
        path = a_path(weeks=8)
        self.assertEqual(studypath.week_of(path, DAY + timedelta(days=9)), (2, 8))
        end = DAY + timedelta(weeks=8)
        self.assertFalse(studypath.review_due(path, end - timedelta(days=1)))
        self.assertTrue(studypath.review_due(path, end))
        studypath.snooze_review(path, end)
        self.assertFalse(studypath.review_due(path, end))  # asked today already
        self.assertTrue(studypath.review_due(path, end + timedelta(days=1)))
        studypath.keep_going(path, end + timedelta(days=1))
        self.assertEqual(path["weeks"], 12)
        self.assertFalse(studypath.review_due(path, end + timedelta(days=1)))

    def test_cleaning_what_was_saved(self):
        self.assertEqual(studypath.clean({"goal": "  "}), {})
        self.assertEqual(studypath.clean("nonsense"), {})
        raw = {"goal": "Goal", "weeks": 99, "started": "not a date",
               "milestones": [{"title": "M", "steps": [{"title": "S", "pomodoros": 50}, {"title": ""}, "x"]},
                              {"title": ""}, 5]}
        path = studypath.clean(raw)
        self.assertEqual(path["weeks"], 52)
        self.assertEqual(len(path["milestones"]), 1)
        self.assertEqual(path["milestones"][0]["steps"], [
            {"title": "S", "pomodoros": studypath.MAX_POMODOROS, "spent": 0, "done": False, "done_on": ""}])
        self.assertEqual(path["started"], date.today().isoformat())

    def test_settings_keep_the_path(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "settings.json"
            s = Settings()
            s.study_path = a_path()
            s.locked_apps = ["C:\\Games\\Valorant.EXE", "steam", "steam.exe", "pomo", 4]
            s.clamp()
            s.save(file)
            loaded = Settings.load(file)
            self.assertEqual(loaded.study_path["goal"], "Junior data analyst job")
            self.assertEqual(loaded.locked_apps, ["valorant", "steam"])

    def test_summary_for_ask_pomo(self):
        text = studypath.summary(a_path(), DAY + timedelta(days=15))
        self.assertIn("week 3 of 8, 0 of 3 steps done", text)
        self.assertIn("Today's step: Variables and loops (0 of 2 pomodoros)", text)
        self.assertEqual(studypath.summary({}, DAY), "Study path: none yet.")


class PathToolTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings()
        self.engine = TimerEngine(self.settings)
        self.done = []

        def path_changed(path=None):
            if path is not None:
                self.settings.study_path = path

        def complete_step(mi, si, done=True):
            studypath.set_done(self.settings.study_path, mi, si, done, DAY)
            self.done.append((mi, si))

        self.ctrl = SimpleNamespace(engine=self.engine, settings=self.settings, path_changed=path_changed,
                                    complete_step=complete_step)

    def test_ask_pomo_makes_a_path_and_ticks_steps(self):
        result, note = planner.run_tool(self.ctrl, "set_path", {"goal": "Learn data analysis", "why": "",
                                                                "weeks": 8, "milestones": MILESTONES})
        self.assertIn("Today's step: Variables and loops", result)
        self.assertEqual(note, "Made your path: 2 milestones, 3 steps")
        result, _ = planner.run_tool(self.ctrl, "complete_step", {"step": "functions"})
        self.assertEqual(self.done, [(0, 1)])
        with self.assertRaises(planner.ToolError):
            planner.run_tool(self.ctrl, "complete_step", {"step": "Not a step"})
        with self.assertRaises(planner.ToolError):
            planner.run_tool(self.ctrl, "set_path", {"goal": "x", "why": "", "weeks": 8, "milestones": []})

    def test_the_tools_fit_openai_strict_mode(self):
        functions = {t["function"]["name"]: t["function"] for t in compat_tools("openai")}
        schema = json.dumps(functions["set_path"]["parameters"])
        self.assertEqual(schema.count('"additionalProperties": false'), 3)  # every object, nested ones too
        self.assertIn("complete_step", functions)

    def test_the_chip_and_prompt(self):
        self.assertEqual(quick_prompt("path", "", has_path=False), PATH_START_PROMPT)
        self.assertEqual(quick_prompt("path", "", has_path=True), PATH_CHECK_PROMPT)
        self.assertIn("set_path", SYSTEM_PROMPT)


class LogTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.dir.name) / "history.db")

    def tearDown(self):
        self.storage.close()
        self.dir.cleanup()

    def test_writing_and_reading_back(self):
        entry = self.storage.add_log("  Today I learned how loops work.  ", DAY)
        self.assertEqual(entry.text, "Today I learned how loops work.")
        self.storage.set_log_feedback(entry.id, "**Well done:** clear!")
        latest = self.storage.logs()[0]
        self.assertEqual((latest.day, latest.feedback), (DAY, "**Well done:** clear!"))
        self.assertEqual(self.storage.log_days(), {DAY})

    def test_streaks(self):
        days = {DAY, DAY - timedelta(days=1), DAY - timedelta(days=2), DAY - timedelta(days=4)}
        self.assertEqual(studylog.streak(days, DAY), 3)
        self.assertEqual(studylog.streak(days, DAY + timedelta(days=1)), 3)  # today not written yet: still counts
        self.assertEqual(studylog.streak(days, DAY + timedelta(days=2)), 0)
        self.assertEqual(studylog.words("  two   words "), 2)

    def test_ask_pomo_sees_the_log_and_path(self):
        self.storage.add_log("I learned about functions.", DAY)
        settings = Settings()
        settings.study_path = a_path()
        ctrl = SimpleNamespace(settings=settings, engine=TimerEngine(settings), storage=self.storage, agents=None)
        text = build_context(ctrl, now=datetime.combine(DAY, datetime.min.time()).replace(hour=10))
        self.assertIn("Study path: \"Junior data analyst job\"", text)
        self.assertIn("I learned about functions.", text)
        self.assertIn("1-day streak", text)


class QuestTests(unittest.TestCase):
    def test_study_quests_only_for_people_who_study(self):
        now = datetime(2026, 10, 29, 14)
        self.assertNotIn("english_log", quests.eligible(now, uses_agents=False))
        studying = quests.eligible(now, uses_agents=False, studies=True)
        self.assertIn("english_log", studying)
        self.assertIn("path_step", studying)

    def test_they_count_events(self):
        s = Settings()
        s.quests, s.quest_day = ["english_log", "path_step", "goal"], DAY.isoformat()
        quests.count(s, "log")
        fresh = quests.check(s, [], DAY)
        self.assertEqual([q.key for q in fresh], ["english_log"])


class AppLockTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings()
        self.settings.app_lock = True
        self.settings.locked_apps = ["valorant"]
        self.front = (100, "VALORANT", "Valorant", 4242)
        self.now = [0.0]
        self.lock = applock.AppLock(self.settings, source=lambda: self.front, clock=lambda: self.now[0])
        self.blocked = []
        self.lock.blocked.connect(lambda hwnd, exe: self.blocked.append((hwnd, exe)))

    def tearDown(self):
        self.lock.set_active(False)

    def test_a_locked_app_in_front_during_focus_is_blocked(self):
        self.lock.set_active(True)
        self.assertEqual(self.blocked, [(100, "valorant")])
        self.lock.sample()  # the same window straight away: the same try
        self.now[0] = 2.0
        self.lock.sample()  # still there a moment later: blocked again
        self.assertEqual(len(self.blocked), 2)
        self.assertEqual(applock.tries_text(self.lock.take_tries()), "App lock kept away Valorant (2 tries).")
        self.assertEqual(self.lock.take_tries(), {})

    def test_other_apps_and_outside_focus_are_left_alone(self):
        self.front = (7, "code", "pomo - VS Code", 99)
        self.lock.set_active(True)
        self.assertEqual(self.blocked, [])
        self.lock.set_active(False)
        self.front = (100, "valorant", "Valorant", 4242)
        self.lock.sample()  # a sample after focus ends (the timer is stopped) still checks; set_active gates it
        self.settings.app_lock = False
        self.blocked.clear()
        self.lock.set_active(True)
        self.assertFalse(self.lock.active)
        self.assertEqual(self.blocked, [])

    def test_unlocked_until_the_break(self):
        self.lock.set_active(True)
        self.lock.unlock("Valorant.exe")
        self.now[0] = 5.0
        self.lock.sample()
        self.assertEqual(len(self.blocked), 1)
        self.lock.set_active(False)  # the break: locked again next session
        self.assertEqual(self.lock.unlocked, set())

    def test_names(self):
        self.assertEqual(applock.exe_key(r"C:\Riot Games\VALORANT\live\VALORANT.exe"), "valorant")
        self.assertEqual(applock.label("steam"), "Steam")
        self.assertFalse(applock.lockable("explorer.exe"))
        self.assertFalse(applock.lockable("Pomo"))
        self.assertEqual(applock.tries_text({"steam": 1, "valorant": 3}),
                         "App lock kept away Valorant (3 tries) and Steam (1 try).")


if __name__ == "__main__":
    unittest.main()


class UltraTests(unittest.TestCase):
    def test_the_transformation_in_stages(self):
        from pomodoro import ultra

        self.assertFalse(ultra.look_at(None).showing)
        charging = ultra.look_at(0.5)
        self.assertGreater(charging.charge, 0)
        self.assertEqual(charging.hair, 0)  # no hair until the burst
        burst = ultra.look_at(ultra.CHARGE_S + 0.05)
        self.assertGreater(burst.flash, 0.5)
        self.assertGreater(burst.aura, 1.0)
        steady = ultra.look_at(ultra.TRANSFORM_S + 5)
        self.assertEqual((steady.aura, steady.hair, steady.flash, steady.charge), (1.0, 1.0, 0.0, 0.0))
        self.assertLess(ultra.look_at(None, 0.3).aura, 1.0)  # powering down fades
        self.assertFalse(ultra.look_at(None, ultra.POWER_DOWN_S).showing)

    def test_pomo_powers_up_and_down(self):
        import random

        from PySide6.QtGui import QImage, QPainter
        from PySide6.QtCore import QPointF

        from pomodoro.pomo import Pomo

        pomo = Pomo(random.Random(1))
        pomo.power_up()
        image = QImage(200, 260, QImage.Format_ARGB32)
        for _ in range(140):  # through the whole transformation, painting each frame
            pomo.update(0.02)
            image.fill(0)
            p = QPainter(image)
            pomo.paint(p, QPointF(100, 180), 80)
            p.end()
        self.assertTrue(pomo.ultra_look().showing)
        self.assertFalse(pomo.is_calm())  # the aura flickers: keep animating
        pomo.power_down()
        for _ in range(40):
            pomo.update(0.02)
        self.assertFalse(pomo.ultra_look().showing)

    def test_ultra_apps_stay_blocked_through_breaks_with_no_quick_unlock(self):
        s = Settings()
        s.ultra_on, s.ultra_apps = True, ["valorant"]
        s.app_lock, s.locked_apps = True, ["steam"]
        front = {"w": (1, "valorant", "VALORANT", 5)}
        blocked = []
        lock = applock.AppLock(s, source=lambda: front["w"], clock=lambda: 0.0)
        lock.blocked.connect(lambda hwnd, exe: blocked.append(exe))
        lock.set_active(False)  # a break
        lock.refresh()
        self.assertEqual(blocked, ["valorant"])
        self.assertEqual(lock.locked(), {"valorant"})  # the app lock list only counts during focus
        lock.unlock("valorant")
        self.assertIn("valorant", lock.locked())  # only powering down unlocks it
        self.assertTrue(lock.ultra_app("Valorant.exe"))
        s.ultra_on = False
        lock.refresh()
        self.assertFalse(lock.active)

    def test_settings_and_the_sound(self):
        s = Settings()
        s.ultra_apps = ["D:\Games\Valorant.exe", "explorer", "VALORANT"]
        s.clamp()
        self.assertEqual(s.ultra_apps, ["valorant"])
        from pomodoro.sounds import render_wav

        with tempfile.TemporaryDirectory() as folder:
            length = render_wav("PowerUp", 60, Path(folder) / "power.wav")
        self.assertGreater(length, 2.0)
