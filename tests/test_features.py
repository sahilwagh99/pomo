"""Focus helpers (1.4): ratings and tips, away detection, the distraction check-in, the thought
parking lot, agent pacing, music following the timer, hotkeys, the garden and Ask Pomo's tools."""
import json
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QEventLoop, QTimer

from pomodoro import garden, insights, planner
from pomodoro.config import Settings
from pomodoro.engine import Mode, State, TimerEngine
from pomodoro.hotkeys import MOD_ALT, MOD_CONTROL, MOD_SHIFT, parse
from pomodoro.pacing import AgentPacer
from pomodoro.presence import AwayWatcher, FocusGuard, classify
from pomodoro.storage import AgentEvent, SessionRecord, Storage


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def focus(start: datetime, minutes: int, rating: int = 0, completed: bool = True, actual: int | None = None):
    secs = minutes * 60
    return SessionRecord(start, start + timedelta(seconds=actual or secs), "focus", secs, actual or secs, completed,
                         "", rating)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.engine = TimerEngine(Settings(), clock=self.clock)
        self.logged = []
        self.engine.session_logged.connect(self.logged.append)

    def test_away_time_goes_back_on_the_clock(self):
        e = self.engine
        e.start()
        self.clock.t += 600  # 10 minutes, the last 5 of them away
        e.pause()
        self.assertEqual(e.discount(300), 300)
        self.assertAlmostEqual(e.elapsed, 300)
        self.assertAlmostEqual(e.remaining, 1500 - 300)
        self.assertEqual(e.state, State.PAUSED)

    def test_counting_away_time_after_all(self):
        e = self.engine
        e.start()
        self.clock.t += 300
        e.pause()
        e.discount(120)
        e.credit(120)
        self.assertAlmostEqual(e.elapsed, 300)
        e.credit(5000)  # more than is left: the session completes
        self.assertTrue(self.logged[-1].completed)
        self.assertEqual(e.mode, Mode.SHORT)

    def test_finish_now_counts_the_session(self):
        e = self.engine
        e.start()
        self.clock.t += 18 * 60
        e.finish_now()
        record = self.logged[-1]
        self.assertTrue(record.completed)
        self.assertEqual(record.actual_sec, 18 * 60)
        self.assertTrue(e.mode.is_break)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "history.db"

    def tearDown(self):
        self.dir.cleanup()

    def test_upgrades_an_old_database(self):
        db = sqlite3.connect(self.path)  # the 1.3 layout: no rating or distracted_sec
        db.execute("CREATE TABLE sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, ended_at TEXT"
                   " NOT NULL, day TEXT NOT NULL, mode TEXT NOT NULL, planned_sec INTEGER NOT NULL, actual_sec INTEGER"
                   " NOT NULL, completed INTEGER NOT NULL, task TEXT NOT NULL DEFAULT '')")
        db.execute("INSERT INTO sessions (started_at, ended_at, day, mode, planned_sec, actual_sec, completed, task)"
                   " VALUES ('2026-10-01T09:00:00', '2026-10-01T09:25:00', '2026-10-01', 'focus', 1500, 1500, 1, 'x')")
        db.commit()
        db.close()
        storage = Storage(self.path)
        try:
            [old] = storage.sessions()
            self.assertEqual((old.rating, old.distracted_sec), (0, 0))
            self.assertTrue(storage.rate_last_focus(3))
            self.assertEqual(storage.sessions()[0].rating, 3)
            self.assertEqual(storage.lifetime_pomodoros(), 1)
        finally:
            storage.close()

    def test_thoughts_and_app_time(self):
        storage = Storage(self.path)
        try:
            a = storage.add_thought("  email Sam about the draft ")
            storage.add_thought("buy oat milk")
            self.assertEqual([t.text for t in storage.thoughts()], ["email Sam about the draft", "buy oat milk"])
            storage.set_thought_done(a.id)
            self.assertEqual(len(storage.thoughts()), 1)
            storage.clear_done_thoughts()
            self.assertEqual(len(storage.thoughts(include_done=True)), 1)

            today = date.today()
            storage.add_app_time(today, "VS Code", 1200, False)
            storage.add_app_time(today, "YouTube", 300, True)
            storage.add_app_time(today, "VS Code", 600, False)  # adds up
            rows = storage.app_time(today)
            self.assertEqual(rows[0], ("VS Code", 1800, False))
            score, distracted, total = insights.focus_score(rows)
            self.assertEqual((distracted, total), (300, 2100))
            self.assertAlmostEqual(score, 1 - 300 / 2100)
        finally:
            storage.close()


class InsightTests(unittest.TestCase):
    def test_suggests_the_length_that_works(self):
        day = date(2026, 10, 20)
        morning = datetime(2026, 10, 20, 9, 0)
        records = []
        for i in range(6):
            records.append(focus(morning - timedelta(days=i), 25, rating=1 if i % 2 else 2))
            records.append(focus(morning - timedelta(days=i) + timedelta(hours=1), 40, rating=3))
        tip = insights.length_tip(records, current_min=25, today=day)
        self.assertEqual(tip.minutes, 40)
        self.assertEqual(tip.score, 3.0)
        self.assertEqual(tip.current_score, 1.5)
        self.assertIn("40-minute", tip.text)
        self.assertIsNone(insights.length_tip(records, current_min=40, today=day))  # already there

    def test_needs_enough_answers(self):
        records = [focus(datetime(2026, 10, 20, 9) - timedelta(days=i), 40, rating=3) for i in range(5)]
        self.assertIsNone(insights.length_tip(records, 25, date(2026, 10, 20)))

    def test_stopping_early_counts_as_tough(self):
        start = datetime(2026, 10, 20, 9)
        records = [focus(start - timedelta(days=i), 50, completed=False, actual=900) for i in range(5)]
        records += [focus(start - timedelta(days=i, hours=2), 25, rating=2) for i in range(5)]
        tip = insights.length_tip(records, current_min=50, today=date(2026, 10, 20))
        self.assertEqual(tip.minutes, 25)

    def test_agent_overlap(self):
        day = date(2026, 10, 20)
        t = datetime(2026, 10, 20, 10, 0)
        events = [AgentEvent(t, "s1", "api", "UserPromptSubmit"), AgentEvent(t + timedelta(minutes=20), "s1", "api", "Stop"),
                  AgentEvent(t + timedelta(minutes=40), "s2", "web", "UserPromptSubmit"),
                  AgentEvent(t + timedelta(minutes=50), "s2", "web", "Stop")]
        records = [focus(t + timedelta(minutes=5), 25),
                   SessionRecord(t + timedelta(minutes=30), t + timedelta(minutes=45), "short", 900, 900, True)]
        o = insights.agent_overlap(events, records, day)
        self.assertEqual(o.turns, 2)
        self.assertEqual(o.agent_sec, 30 * 60)
        self.assertEqual(o.during_focus_sec, 15 * 60)   # 10:05 to 10:20
        self.assertEqual(o.during_break_sec, 5 * 60)    # 10:40 to 10:45

    def test_songs_left(self):
        self.assertEqual(insights.songs_left(1500, 200, 56), "about 8 songs left")
        self.assertEqual(insights.songs_left(100, 200, 56), "ends during this song")
        self.assertEqual(insights.songs_left(1500, 3600, 0), "")  # a long video, not songs


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings()
        self.engine = TimerEngine(self.settings)
        tasks = []
        self.ctrl = SimpleNamespace(
            engine=self.engine, settings=self.settings, tasks=tasks,
            set_task=lambda t: (tasks.append(t), setattr(self.engine, "task", t)),
            plan_changed=lambda: self.ctrl.set_task(self.settings.task_queue[0]["task"]) if self.settings.task_queue else None,
            apply_settings=lambda new: (self.settings.update_from(new), self.engine.apply_settings()))

    def test_working_through_a_plan(self):
        planner.set_plan(self.settings, [{"task": "Thesis", "pomodoros": 2}, {"task": "Reading", "pomodoros": 1}], True)
        self.assertEqual(planner.plan_text(self.settings), "Thesis (2 left), then Reading (1)")
        self.assertEqual(planner.advance(self.settings), ("Thesis", False))
        self.assertEqual(planner.advance(self.settings), ("Reading", False))
        self.assertEqual(planner.advance(self.settings), (None, True))
        self.assertEqual(planner.advance(self.settings), (None, False))

    def test_tools(self):
        result, note = planner.run_tool(self.ctrl, "plan_tasks", {
            "tasks": [{"task": "Thesis", "pomodoros": 3}, {"task": "Email", "pomodoros": 1}], "replace": True})
        self.assertIn("Thesis", result)
        self.assertEqual(note, "Planned 2 tasks, 4 pomodoros")
        self.assertEqual(self.engine.task, "Thesis")
        planner.run_tool(self.ctrl, "set_lengths", {"focus_minutes": 45, "short_break_minutes": 10,
                                                    "long_break_minutes": 0})
        self.assertEqual((self.settings.focus_min, self.settings.short_min, self.settings.long_min), (45, 10, 15))
        result, _ = planner.run_tool(self.ctrl, "start_session", {"kind": "focus", "minutes": 0})
        self.assertEqual(result, "Started a 45-minute focus.")
        self.assertTrue(self.engine.is_running)
        planner.run_tool(self.ctrl, "start_session", {"kind": "short_break", "minutes": 7})
        self.assertEqual((self.engine.mode, round(self.engine.total)), (Mode.SHORT, 420))

    def test_bad_input_is_told_back(self):
        for name, args in (("start_session", {"kind": "nap", "minutes": 5}),
                           ("start_session", {"kind": "focus", "minutes": 900}),
                           ("set_task", {"task": "  "}),
                           ("plan_tasks", {"tasks": [{"task": "x"}], "replace": True}),
                           ("make_coffee", {})):
            with self.subTest(name=name), self.assertRaises(planner.ToolError):
                planner.run_tool(self.ctrl, name, args)

    def test_tool_schemas_are_strict(self):
        for tool in planner.TOOLS:
            self.assertTrue(tool["strict"])
            schema = tool["input_schema"]
            self.assertFalse(schema["additionalProperties"])
            self.assertEqual(sorted(schema["required"]), sorted(schema["properties"]))


class PacingTests(unittest.TestCase):
    def make(self, state: State, elapsed_fraction: float = 0.0):
        settings = Settings()
        clock = FakeClock()
        engine = TimerEngine(settings, clock=clock)
        if state in (State.RUNNING, State.PAUSED):
            engine.start()
            clock.t += engine.total * elapsed_fraction
        now = datetime(2026, 10, 20, 11, 0)
        agent = SimpleNamespace(id="s1", project="api", status="working", turn_started=now - timedelta(minutes=3))
        hub = SimpleNamespace(sessions={"s1": agent})
        ctrl = SimpleNamespace(settings=settings, engine=engine, agents=hub)
        pacer = AgentPacer(ctrl, clock=clock, now=lambda: now)
        breaks, focuses = [], []
        pacer.suggest_break.connect(breaks.append)
        pacer.suggest_focus.connect(focuses.append)
        return pacer, breaks, focuses

    def test_break_while_the_agent_works(self):
        pacer, breaks, focuses = self.make(State.RUNNING, 0.7)
        pacer.check()
        pacer.check()  # once per turn
        self.assertEqual(len(breaks), 1)
        self.assertEqual(focuses, [])

    def test_not_early_in_a_session(self):
        pacer, breaks, _ = self.make(State.RUNNING, 0.2)
        pacer.check()
        self.assertEqual(breaks, [])

    def test_focus_while_you_wait(self):
        pacer, breaks, focuses = self.make(State.IDLE)
        pacer.check()
        self.assertEqual((len(breaks), len(focuses)), (0, 1))

    def test_held_news_summary(self):
        text = AgentPacer.summary([("finished", "api", "4:12"), ("waiting", "web", ""), ("finished", "web", "1:05")])
        self.assertEqual(text, "While you focused: api finished (4:12), web finished (1:05).")
        self.assertEqual(AgentPacer.summary([]), "")


class PresenceTests(unittest.TestCase):
    def test_classify(self):
        words = ["youtube", "reddit", "steam"]
        self.assertEqual(classify("chrome", "Lo-fi beats - YouTube - Google Chrome", words), ("YouTube", "youtube"))
        self.assertEqual(classify("msedge", "Docs - Microsoft Edge", words), ("Edge", None))
        # an editor isn't distracting because a file name mentions a site
        self.assertEqual(classify("Code", "youtube_api.py - pomo - Visual Studio Code", words), ("VS Code", None))
        self.assertEqual(classify("steamwebhelper", "Steam", words), ("Steam", "steam"))

    def test_away_and_back(self):
        idle, clock = [0.0], FakeClock()
        watcher = AwayWatcher(idle_source=lambda: idle[0], clock=clock)
        events = []
        watcher.went_away.connect(lambda s: events.append(("away", s)))
        watcher.came_back.connect(lambda s: events.append(("back", s)))
        watcher.configure(True, 300)
        idle[0] = 120
        watcher.check()
        idle[0], clock.t = 305, clock.t + 185
        watcher.check()
        watcher.configure(False, 300)  # the session paused; still watching for your return
        idle[0], clock.t = 1.0, clock.t + 400
        watcher.check()
        self.assertEqual(events[0], ("away", 305))
        self.assertEqual(events[1][0], "back")
        self.assertAlmostEqual(events[1][1], 305 + 400 - 1.0, delta=1)

    def test_guard_counts_and_nudges(self):
        clock = FakeClock()
        front = [("Code", "app.py - Visual Studio Code", 4242)]
        settings = Settings()
        settings.focus_guard = True
        added = []
        storage = SimpleNamespace(add_app_time=lambda day, app, sec, bad: added.append((app, sec, bad)))
        guard = FocusGuard(storage, settings, source=lambda: front[0], clock=clock)
        nudges = []
        guard.nudge.connect(lambda label, word: nudges.append(label))
        guard.set_active(True)
        for _ in range(5):
            clock.t += 2
            guard.sample()
        front[0] = ("chrome", "Cat videos - YouTube - Google Chrome", 4242)
        for _ in range(12):
            clock.t += 2
            guard.sample()
        self.assertEqual(nudges, ["YouTube"])
        self.assertEqual(guard.take_session(), 24)
        guard.set_active(False)
        # each sample counts the time since the last one for whatever is in front now
        self.assertIn(("VS Code", 10, False), added)
        self.assertIn(("YouTube", 24, True), added)


class SmallThingsTests(unittest.TestCase):
    def test_hotkey_parsing(self):
        self.assertEqual(parse("Ctrl+Alt+Shift+P"), (MOD_CONTROL | MOD_ALT | MOD_SHIFT, 0x50))
        self.assertEqual(parse("F9"), (0, 0x78))
        self.assertIsNone(parse("P"))          # would swallow typing
        self.assertIsNone(parse("Shift+P"))
        self.assertIsNone(parse(""))

    def test_garden(self):
        self.assertEqual(garden.stage(0)[1], "Sprout")
        self.assertEqual(garden.stage(120)[1], "Sakura tree")
        self.assertEqual(garden.next_unlock(12), ("flowercrown", 25))
        self.assertEqual(garden.newly_unlocked(30, ["catears"]), ["flowercrown"])
        self.assertTrue(garden.is_unlocked("blossom", 0))
        self.assertFalse(garden.is_unlocked("wizard", 99))
        self.assertIn("13 more pomodoros", garden.progress_text(12))

    def test_pomo_dresses_for_what_you_do(self):
        from pomodoro.app import PomoApp

        class Win:
            def __init__(self, shown):
                self.shown = shown

            def isVisible(self):
                return self.shown

            def isMinimized(self):
                return False

        settings = Settings()
        engine = TimerEngine(settings)
        state = {"music": False, "signal": None, "idle": 0.0}
        fake = SimpleNamespace(settings=settings, engine=engine, lifetime=0, game=None, breathe=None, chat=None,
                               dashboard=None, pomo_listening=lambda: state["music"],
                               agent_signal=lambda: state["signal"], idle_seconds=lambda: state["idle"])
        fake._activity_outfits = lambda: PomoApp._activity_outfits(fake)
        outfit = lambda: PomoApp.current_outfit(fake)
        self.assertEqual(outfit(), "blossom")             # nothing going on: your pick
        state["idle"] = 600
        self.assertEqual(outfit(), "nightcap")
        engine.start()
        self.assertEqual(outfit(), "headband")            # focusing
        state["signal"] = "busy"
        self.assertEqual(outfit(), "hardhat")             # agents at work
        state["music"] = True
        self.assertEqual(outfit(), "headphones")
        fake.dashboard = Win(True)
        self.assertEqual(outfit(), "glasses")
        fake.breathe = Win(True)
        self.assertEqual(outfit(), "glasses")             # the flower crown isn't earned yet
        fake.lifetime = 30
        self.assertEqual(outfit(), "flowercrown")
        fake.game = Win(True)
        self.assertEqual(outfit(), "party")
        settings.outfit_follows = False
        self.assertEqual(outfit(), "blossom")

    def test_settings_clean_up_plans_and_lists(self):
        s = Settings()
        s.task_queue = [{"task": " Thesis ", "pomodoros": 99}, {"task": ""}, "nope", {"task": "Read", "pomodoros": 2}]
        s.distracting = ["  YouTube ", "", 7]
        s.clamp()
        self.assertEqual(s.task_queue, [{"task": "Thesis", "pomodoros": 12}, {"task": "Read", "pomodoros": 2}])
        self.assertEqual(s.distracting, ["youtube"])


def sse(*events) -> bytes:
    return "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events).encode()


def reply(index_blocks, stop_reason):
    events = [("message_start", {"type": "message_start", "message": {
        "id": "msg", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": [],
        "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 1}}})]
    for i, block in enumerate(index_blocks):
        if block["type"] == "text":
            events += [("content_block_start", {"type": "content_block_start", "index": i,
                                                "content_block": {"type": "text", "text": ""}}),
                       ("content_block_delta", {"type": "content_block_delta", "index": i,
                                                "delta": {"type": "text_delta", "text": block["text"]}})]
        else:
            events += [("content_block_start", {"type": "content_block_start", "index": i, "content_block": {
                "type": "tool_use", "id": block["id"], "name": block["name"], "input": {}}}),
                       ("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {
                           "type": "input_json_delta", "partial_json": json.dumps(block["input"])}})]
        events.append(("content_block_stop", {"type": "content_block_stop", "index": i}))
    events += [("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                                  "usage": {"output_tokens": 5}}),
               ("message_stop", {"type": "message_stop"})]
    return sse(*events)


class AskPomoToolsTests(unittest.TestCase):
    """The whole loop in the chat window: Claude asks for tools, Pomo runs them, Claude finishes the answer."""

    def test_plan_and_start_from_one_message(self):
        import anthropic
        import httpx2

        from pomodoro.chat import ChatWindow
        from pomodoro.theme import theme_for

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        storage = Storage(Path(tmp.name) / "history.db")
        self.addCleanup(storage.close)
        settings = Settings()
        engine = TimerEngine(settings)

        class Ctrl:
            pass

        ctrl = Ctrl()
        ctrl.settings, ctrl.engine, ctrl.storage, ctrl.theme = settings, engine, storage, theme_for("light")
        from PySide6.QtGui import QIcon
        ctrl.icon, ctrl.agents = QIcon(), None
        ctrl.has_ai_key = lambda: True
        ctrl.ai_key = lambda: "test-key"
        ctrl.env_ai_key = lambda: None
        ctrl.save_settings = lambda: None
        ctrl.pomo_listening = lambda: False
        ctrl.current_outfit = lambda: settings.pomo_outfit

        def set_task(task):
            settings.remember_task(task)
            engine.task = settings.current_task
        ctrl.set_task = set_task
        ctrl.plan_changed = lambda: set_task(settings.task_queue[0]["task"]) if settings.task_queue else None
        ctrl.apply_settings = lambda new: (settings.update_from(new), engine.apply_settings())

        bodies = []
        replies = [
            reply([{"type": "text", "text": "On it!"},
                   {"type": "tool_use", "id": "toolu_1", "name": "plan_tasks",
                    "input": {"tasks": [{"task": "Thesis", "pomodoros": 3}], "replace": True}},
                   {"type": "tool_use", "id": "toolu_2", "name": "start_session",
                    "input": {"kind": "focus", "minutes": 45}}], "tool_use"),
            reply([{"type": "text", "text": "Your thesis plan is set and the first 45 minutes have started."}],
                  "end_turn"),
        ]

        def handler(request):
            bodies.append(json.loads(request.content))
            return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=replies[len(bodies) - 1])

        real = anthropic.Anthropic
        anthropic.Anthropic = lambda **kw: real(api_key="test-key", max_retries=0, http_client=anthropic.DefaultHttpxClient(
            transport=httpx2.MockTransport(handler)))
        self.addCleanup(setattr, anthropic, "Anthropic", real)

        chat = ChatWindow(ctrl)
        self.addCleanup(chat.deleteLater)
        chat.send("45 minutes on my thesis, three rounds please")
        loop = QEventLoop()
        timer = QTimer()
        timer.timeout.connect(lambda: loop.quit() if chat.worker is None and chat._continue is None else None)
        timer.start(50)
        guard = QTimer()  # not a singleShot: that would outlive this test and quit a deleted loop
        guard.setSingleShot(True)
        guard.timeout.connect(loop.quit)
        guard.start(10000)
        loop.exec()
        timer.stop()
        guard.stop()

        self.assertEqual(len(bodies), 2)
        self.assertEqual({t["name"] for t in bodies[0]["tools"]}, {t["name"] for t in planner.TOOLS})
        # the follow-up answers both calls in one user message, after the assistant turn that made them
        roles = [m["role"] for m in bodies[1]["messages"]]
        self.assertEqual(roles, ["user", "system", "assistant", "user"])
        results = bodies[1]["messages"][-1]["content"]
        self.assertEqual([r["tool_use_id"] for r in results], ["toolu_1", "toolu_2"])
        self.assertTrue(engine.is_running)
        self.assertEqual(round(engine.total), 45 * 60)
        self.assertEqual(engine.task, "Thesis")
        self.assertEqual(settings.task_queue, [{"task": "Thesis", "pomodoros": 3}])
        self.assertEqual(len(chat.history), 5)  # user, context, tool calls, tool results, final answer


if __name__ == "__main__":
    unittest.main()
