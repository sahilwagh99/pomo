"""The habit tracker: daily matches, the rank ladder, the month view, importing a spreadsheet, and showing it all."""
import tempfile
import unittest
import zipfile
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QCoreApplication, QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter

from pomodoro import habits, medals
from pomodoro.config import Settings
from pomodoro.engine import TimerEngine
from pomodoro.storage import Storage
from pomodoro.theme import theme_for

DAY = date(2026, 11, 15)  # a Sunday in a 30-day month


def _habit(name: str, goal: int = 31, since: date = DAY - timedelta(days=20), until: str = "") -> dict:
    h = habits.make(name, goal, since)
    h["until"] = until
    return h


def _xlsx(path: Path) -> None:
    """A small workbook laid out like TemplateLab's monthly habit tracker."""
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    strings = ["HABIT", "GOAL", "Gym", "Meditation", "In the lines above, in a short but sufficiently descriptive way, "
               "write the habits you want to follow.", "x", "Completed"]
    shared = "".join(f"<si><t>{s}</t></si>" for s in strings)
    sheet = (f"<worksheet {ns}><sheetData>"
             '<row r="3"><c r="B3"><v>2026</v></c></row>'
             '<row r="11"><c r="B11" t="s"><v>0</v></c><c r="D11" t="s"><v>1</v></c><c r="F11"><v>1</v></c></row>'
             '<row r="15"><c r="B15" t="s"><v>2</v></c><c r="D15"><v>27.0</v></c><c r="F15" t="s"><v>5</v></c></row>'
             '<row r="17"><c r="B17" t="inlineStr"><is><t>Read  10 pages</t></is></c><c r="D17"><v>31</v></c></row>'
             '<row r="19"><c r="B19" t="s"><v>3</v></c></row>'
             '<row r="21"><c r="B21"><v>42</v></c></row>'
             '<row r="43"><c r="B43" t="s"><v>4</v></c></row>'
             '<row r="48"><c r="B48" t="s"><v>6</v></c></row>'
             "</sheetData></worksheet>")
    book = (f'<workbook {ns} xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Monthly Habit Tracker" sheetId="1" r:id="rId1"/></sheets></workbook>')
    rels = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", book)
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        z.writestr("xl/sharedStrings.xml", f"<sst {ns}>{shared}</sst>")
        z.writestr("xl/worksheets/sheet1.xml", sheet)


class MatchTests(unittest.TestCase):
    def test_a_day_is_won_halved_or_lost(self):
        self.assertEqual(habits.result(11, 11), 30)
        self.assertEqual(habits.result(12, 11), 30)
        self.assertEqual(habits.result(6, 11), 15)   # half of 11, rounded up
        self.assertEqual(habits.result(5, 11), -30)
        self.assertEqual(habits.result(0, 1), -30)
        self.assertEqual(habits.result(0, 0), 0)     # no habits, no match
        self.assertEqual(habits.half_of(11), 6)

    def test_the_target_is_what_the_monthly_goals_add_up_to(self):
        # the spreadsheet's habits: ten every day, gym 27 days, and three now and then
        hs = [_habit(f"Daily {i}") for i in range(10)] + [_habit("Gym", 27), _habit("Care", 10),
                                                          _habit("Laundry", 5), _habit("Learn", 13)]
        self.assertEqual(habits.auto_target(hs, DAY), 11)            # 355 // 30
        self.assertEqual(habits.auto_target(hs, date(2026, 12, 1)), 11)  # 365 // 31
        self.assertEqual(habits.month_goal(hs[0], 2026, 2), 28)
        self.assertEqual(habits.target_for(hs, 8, DAY), 8)           # your own target
        self.assertEqual(habits.target_for(hs, 40, DAY), 14)         # at most the habits you track
        self.assertEqual(habits.target_for(hs, 0, DAY - timedelta(days=30)), 0)  # before any habit
        self.assertEqual(habits.auto_target([_habit("Laundry", 5)], DAY), 1)  # never below one

    def test_points_climb_and_slide_but_never_below_zero(self):
        a, b = _habit("Read"), _habit("Walk")
        start = DAY - timedelta(days=3)
        a["since"] = b["since"] = start.isoformat()
        log = {start: set(), start + timedelta(days=1): {a["id"], b["id"]}, start + timedelta(days=2): {a["id"]}}
        days = habits.history([a, b], 0, log, DAY - timedelta(days=1))
        self.assertEqual([(d.done, d.target, d.delta, d.points) for d in days],
                         [(0, 2, -30, 0), (2, 2, 30, 30), (1, 2, 15, 45)])
        st = habits.standing([a, b], 0, {**log, DAY: {b["id"]}}, DAY)
        self.assertEqual((st.points, st.today_done, st.today_target, st.pending), (45, 1, 2, 15))
        self.assertEqual(st.preview.points, 60)
        self.assertEqual(habits.today_line(st), "1 of 2 habits today: +15 so far. 2 for +30.")

    def test_settled_days_keep_the_target_they_had(self):
        a = _habit("Read")
        start = DAY - timedelta(days=4)
        a["since"] = start.isoformat()
        log = {start + timedelta(days=i): {a["id"]} for i in range(4)}
        final = {start: (1, 1), start + timedelta(days=1): (1, 3)}  # that day you had 3 habits and did 1
        days = habits.history([a], 0, log, DAY - timedelta(days=1), final)
        self.assertEqual([d.delta for d in days], [30, -30, 30, 30])
        self.assertEqual(habits.to_settle(days, final, DAY), [days[2]])  # yesterday stays open
        # a day settled before every habit was retired still counts
        a["until"] = (start + timedelta(days=1)).isoformat()
        self.assertEqual([d.delta for d in habits.history([a], 0, {}, DAY, final)], [30, -30])

    def test_only_today_and_yesterday_can_change(self):
        self.assertTrue(habits.editable(DAY, DAY))
        self.assertTrue(habits.editable(DAY - timedelta(days=1), DAY))
        self.assertFalse(habits.editable(DAY - timedelta(days=2), DAY))
        self.assertFalse(habits.editable(DAY + timedelta(days=1), DAY))

    def test_no_habits_today_no_standing(self):
        self.assertIsNone(habits.standing([], 0, {}, DAY))
        retired = _habit("Old", until=(DAY - timedelta(days=2)).isoformat())
        self.assertIsNone(habits.standing([retired], 0, {}, DAY))


class RankTests(unittest.TestCase):
    def test_the_ladder(self):
        cases = {0: ("Rookie 1", 0), 149: ("Rookie 1", 149), 150: ("Rookie 2", 0), 749: ("Rookie 5", 149),
                 750: ("Squire 1", 0), 2400: ("Paladin 2", 0), 5249: ("Celestial 5", 149),
                 5250: ("Eternal 1", 0), 5420: ("Eternal 2", 20)}
        for points, (label, into) in cases.items():
            rank = habits.rank_of(points)
            self.assertEqual((rank.label, rank.into), (label, into), points)
        self.assertEqual(habits.rank_of(-40).points, 0)
        rank = habits.rank_of(700)
        self.assertEqual((rank.to_next, rank.next_label), (50, "Squire 1"))
        self.assertEqual(habits.rank_of(5249).next_label, "Eternal 1")
        self.assertEqual(habits.rank_of(5300).next_label, "Eternal 2")
        self.assertLess(habits.rank_of(5249).step, habits.rank_of(5250).step)
        self.assertEqual(habits.rank_of(5250).stars, 0)  # the top medal counts up instead of stars

    def test_news_of_the_days_you_missed(self):
        days = (habits.Day(DAY - timedelta(days=3), 2, 2, 30, 740), habits.Day(DAY - timedelta(days=2), 2, 2, 30, 770),
                habits.Day(DAY - timedelta(days=1), 0, 2, -30, 740))
        fresh, before, after = habits.news(days, DAY - timedelta(days=3), DAY - timedelta(days=1))
        self.assertEqual([d.delta for d in fresh], [30, -30])
        self.assertEqual((before.label, after.label), ("Rookie 5", "Rookie 5"))
        _f, before, after = habits.news(days, DAY - timedelta(days=4), DAY - timedelta(days=2))
        self.assertEqual((before.label, after.label), ("Rookie 1", "Squire 1"))


class HabitListTests(unittest.TestCase):
    def test_editing_renames_retires_and_adds(self):
        hs = [_habit("Gym", 27), _habit("Read")]
        gym, read = hs[0]["id"], hs[1]["id"]
        habits.apply_edit(hs, [(gym, " Gym  time ", 20), (None, "Walk", 31)], DAY)
        current = habits.current(hs, DAY)
        self.assertEqual([(h["name"], h["goal"]) for h in current], [("Gym time", 20), ("Walk", 31)])
        self.assertEqual(current[0]["id"], gym)
        old = next(h for h in hs if h["id"] == read)
        self.assertEqual(old["until"], DAY.isoformat())  # retired: the days it counted stay
        walk = current[1]["id"]
        habits.apply_edit(hs, [(gym, "Gym time", 20)], DAY)  # added and removed the same day: gone
        self.assertNotIn(walk, {h["id"] for h in hs})

    def test_settings_keep_it_sane(self):
        good = _habit("Read")
        bad = [{"id": "x"}, "nope", {**good, "id": good["id"]}, {**_habit("Bad date"), "since": "soon"},
               {**_habit("Ended before it began"), "until": (DAY - timedelta(days=40)).isoformat()},
               {**_habit("Big goal"), "goal": 99}]
        cleaned = habits.clean([good] + bad)
        self.assertEqual([h["name"] for h in cleaned], ["Read", "Big goal"])
        self.assertEqual(cleaned[1]["goal"], 31)
        s = Settings()
        s.habits, s.habit_target, s.habit_remind_hour = [good, "junk"], 99, 40
        s.clamp()
        self.assertEqual((len(s.habits), s.habit_target, s.habit_remind_hour), (1, habits.MAX_HABITS, 23))

    def test_settings_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            s = Settings()
            s.habits = [_habit("Read", 20)]
            s.habit_target = 3
            s.save(path)
            loaded = Settings.load(path)
            self.assertEqual(loaded.habits, s.habits)
            self.assertEqual(loaded.habit_target, 3)

    def test_merging_a_spreadsheet(self):
        hs = [_habit("gym", 20)]
        added, updated = habits.merge(hs, [("Gym", 27), ("Read", 31), ("Read", 31)], DAY)
        self.assertEqual((added, updated), (1, 1))
        self.assertEqual([(h["name"], h["goal"]) for h in hs], [("gym", 27), ("Read", 31)])
        self.assertEqual(hs[1]["since"], DAY.isoformat())  # counted from today

    def test_reading_a_habit_tracker_workbook(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tracker.xlsx"
            _xlsx(path)
            self.assertEqual(habits.read_tracker(path), [("Gym", 27), ("Read 10 pages", 31), ("Meditation", 31)])
            other = Path(tmp) / "notes.xlsx"
            other.write_text("not a workbook", encoding="utf-8")
            with self.assertRaises(ValueError):
                habits.read_tracker(other)
            empty = Path(tmp) / "empty.xlsx"
            with zipfile.ZipFile(empty, "w") as z:
                z.writestr("xl/worksheets/sheet1.xml", '<worksheet xmlns="http://schemas.openxmlformats.org/'
                                                       'spreadsheetml/2006/main"><sheetData/></worksheet>')
            with self.assertRaises(ValueError):
                habits.read_tracker(empty)


class MonthTests(unittest.TestCase):
    def test_like_the_spreadsheet(self):
        a, b = _habit("Read", since=date(2026, 10, 1)), _habit("Gym", 10, since=date(2026, 10, 1))
        log = {date(2026, 11, d): {a["id"], b["id"]} if d % 2 else {a["id"]} for d in range(1, 11)}
        view = habits.month_view([a, b], log, 2026, 11)
        self.assertEqual(view.days, 30)
        self.assertEqual([(h["name"], times, goal) for h, times, goal in view.rows], [("Read", 10, 30), ("Gym", 5, 10)])
        self.assertEqual(view.per_day[:3], (2, 1, 2))
        self.assertEqual((view.done, view.goal), (15, 40))
        # pace: 15/40 of the goals in 10 of 30 days, capped at 1; momentum: 4 ticks in the last 3 days, 4/3 a day
        self.assertAlmostEqual(view.rate, (1.0 + 3 * 1.0) / 4)
        self.assertEqual(view.verdict, "Excellent")

    def test_nothing_ticked_yet(self):
        view = habits.month_view([_habit("Read")], {}, 2026, 11)
        self.assertAlmostEqual(view.rate, 0.125)  # what the spreadsheet shows before any tick
        self.assertEqual(view.verdict, "Start again")

    def test_a_habit_started_mid_month_gets_its_share_of_the_goal(self):
        h = _habit("Read", 31, since=date(2026, 11, 16))  # 15 of November's 30 days
        view = habits.month_view([h], {}, 2026, 11)
        self.assertEqual(view.rows[0][2], 15)
        self.assertEqual(habits.month_view([h], {}, 2026, 10).rows, ())


class StorageTests(unittest.TestCase):
    def test_ticks_and_settled_days(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(Path(tmp) / "history.db")
            storage.set_habit(DAY, "a1", True)
            storage.set_habit(DAY, "a1", True)
            storage.set_habit(DAY, "b2", True)
            storage.set_habit(DAY - timedelta(days=1), "a1", True)
            storage.set_habit(DAY, "b2", False)
            self.assertEqual(storage.habit_log(), {DAY: {"a1"}, DAY - timedelta(days=1): {"a1"}})
            self.assertEqual(storage.habit_log(since=DAY), {DAY: {"a1"}})
            storage.settle_habit_days([(DAY - timedelta(days=2), 3, 4)])
            self.assertEqual(storage.settled_habit_days(), {DAY - timedelta(days=2): (3, 4)})
            storage.close()


def _standing(points: int, done: int = 3, target: int = 4) -> habits.Standing:
    return habits.Standing(points, habits.rank_of(points), done, target, habits.result(done, target), ())


class MedalTests(unittest.TestCase):
    def test_every_medal_draws(self):
        img = QImage(900, 120, QImage.Format_ARGB32)
        img.fill(0)
        p = QPainter(img)
        for i in range(len(habits.MEDALS)):
            rank = habits.Rank(i, 3 if i < habits.TOP else 0, 0 if i < habits.TOP else 12, 0, 0)
            medals.paint_medal(p, QPointF(60 + i * 110, 60), 30, rank)
            medals.paint_cached(p, QPointF(60 + i * 110, 110), 8, rank, 2.0)
            side, top, bottom = medals.extent(i)
            self.assertTrue(1.0 <= side <= 1.7 and 1.0 <= top <= 1.6 and bottom > 1.0)
        p.end()
        self.assertNotEqual(img.pixelColor(60, 60).alpha(), 0)

    def test_progress_shows_todays_open_match(self):
        self.assertEqual(medals.progress_parts(_standing(75, 4, 4)), (0.5, 0.7))     # +30 ahead
        self.assertEqual(medals.progress_parts(_standing(75, 0, 4)), (0.3, 0.5))     # -30 at risk
        self.assertEqual(medals.progress_parts(_standing(10, 0, 4)), (0.0, 10 / 150))  # it would drop a star
        self.assertEqual(medals.progress_parts(_standing(140, 4, 4)), (140 / 150, 1.0))
        img = QImage(200, 40, QImage.Format_ARGB32)
        img.fill(0)
        p = QPainter(img)
        medals.paint_bar(p, QRectF(4, 15, 192, 10), _standing(75), False, QColor("#b5544e"))
        p.end()
        self.assertNotEqual(img.pixelColor(20, 20).alpha(), 0)


class _Ctrl:
    """Just enough of the controller for the timer views and the habit window."""

    def __init__(self, settings: Settings, standing, ticks=None):
        self.settings, self.theme, self.icon = settings, theme_for("light"), QIcon()
        self.engine = TimerEngine(settings)
        self.today_count, self.track, self.agents, self.whisper = 0, None, None, None
        self.habit_standing, self.habit_ticks = standing, ticks or {}
        self.toggled, self.opened = [], 0

    def idle_seconds(self): return 0.0
    def agent_signal(self): return None
    def current_outfit(self): return "blossom"
    def pomo_listening(self): return False
    def music_playing(self): return False
    def mode_label(self, mode=None): return (mode or self.engine.mode).label
    def mode_dot(self, mode=None): return None
    def toggle_habit(self, hid, day): self.toggled.append((hid, day))
    def open_habits(self): self.opened += 1


def _close(*widgets):
    for w in widgets:  # gone now, not when Python shuts down (Qt can crash then)
        w.close()
        w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


class ViewTests(unittest.TestCase):
    def test_the_orb_wears_the_rank_below_its_face(self):
        from pomodoro.orb import OrbWidget

        s = Settings()
        ctrl = _Ctrl(s, None)
        orb = OrbWidget(ctrl)
        plain = orb.height()
        self.assertNotIn("rank", orb._badges())
        ctrl.habit_standing = _standing(2400, 1, 4)
        orb.apply_settings()
        self.assertGreater(orb.height(), plain)  # room for the medal hanging below
        badges = orb._badges()
        self.assertIn("rank", badges)
        center, r, _seen = badges["rank"]
        self.assertGreater(center.y(), orb._center().y() + s.orb_size / 2)
        orb._hover = 1.0
        ultra = orb._badges()["ultra"][0]
        self.assertAlmostEqual(ultra.y(), orb._center().y())  # moved up beside the face, off the arc
        self.assertFalse(orb.grab().isNull())
        orb._trigger("rank")
        self.assertEqual(ctrl.opened, 1)
        s.show_rank = False
        self.assertNotIn("rank", orb._badges())
        _close(orb)

    def test_the_island_shows_a_rank_row_when_open(self):
        from pomodoro.island import RANK_H, IslandWidget

        ctrl = _Ctrl(Settings(), None)
        isl = IslandWidget(ctrl)
        isl._presence, isl._expand = 1.0, 1.0
        tall = isl._expanded_h()
        ctrl.habit_standing = _standing(900, 4, 4)
        self.assertEqual(isl._expanded_h(), tall + RANK_H)
        self.assertEqual(isl._button_at(isl._rank_rect().center()), "rank")
        self.assertFalse(isl.grab().isNull())
        isl._expand = 0.0
        self.assertFalse(isl.grab().isNull())  # the compact pill's little medal
        _close(isl)

    def test_the_habit_window(self):
        from PySide6.QtTest import QTest
        from pomodoro.habit_window import HabitEditor, HabitWindow

        today = date.today()
        s = Settings()
        s.habits = [_habit("Read", since=today - timedelta(days=5)), _habit("Walk", 10, since=today - timedelta(days=5))]
        ticks = {today - timedelta(days=1): {s.habits[0]["id"]}, today: {s.habits[1]["id"]}}
        standing = habits.standing(s.habits, 0, ticks, today)
        ctrl = _Ctrl(s, standing, ticks)
        w = HabitWindow(ctrl)
        w.show()
        self.assertEqual(w.rank_label.text(), standing.rank.label)
        self.assertTrue(w.grid.isVisible())
        self.assertFalse(w.empty.isVisible())
        grid = w.grid
        row = 0
        box = grid._box(row, today.day - 1)
        QTest.mouseClick(grid, Qt.LeftButton, pos=box.center().toPoint())
        self.assertEqual(ctrl.toggled, [(s.habits[0]["id"], today)])
        if today.day > 2:  # a final day can't be ticked
            QTest.mouseClick(grid, Qt.LeftButton, pos=grid._box(row, today.day - 3).center().toPoint())
            self.assertEqual(len(ctrl.toggled), 1)
        self.assertIn("click to tick", grid._tip(box.center()))
        self.assertFalse(w.grab().isNull())

        editor = HabitEditor(ctrl, w)
        self.assertEqual([r[1:] for r in editor.rows_out()], [("Read", 31), ("Walk", 10)])
        editor.rows[0][1].setText("Read a chapter")
        editor._add_row(None, "Stretch", 31)
        self.assertEqual(editor.rows_out()[-1], (None, "Stretch", 31))
        self.assertIn("Auto (", editor.target.specialValueText())
        _close(editor, w)

        empty = HabitWindow(_Ctrl(Settings(), None))
        empty.show()
        self.assertTrue(empty.empty.isVisible())
        self.assertFalse(empty.grid.isVisible())
        _close(empty)

    def test_the_controller_settles_days_reports_them_and_cheers_the_target(self):
        from PySide6.QtCore import QRect
        from pomodoro.app import PomoApp

        today = date.today()
        s = Settings()
        s.habits = [_habit("Read", since=today - timedelta(days=3)), _habit("Walk", since=today - timedelta(days=3))]
        read, walk = s.habits[0]["id"], s.habits[1]["id"]
        s.habit_day = (today - timedelta(days=4)).isoformat()
        cards, cheers = [], []
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(Path(tmp) / "history.db")
            for day, ids in ((today - timedelta(days=3), (read, walk)), (today - timedelta(days=2), (read,))):
                for hid in ids:
                    storage.set_habit(day, hid, True)
            app = SimpleNamespace(
                settings=s, storage=storage, habit_standing=None, habit_ticks={}, habit_window=None, whisper=None,
                toast=SimpleNamespace(show_message=lambda *a, **k: cards.append(a)),
                view=SimpleNamespace(celebrate=lambda big=False: cheers.append(big), isVisible=lambda: False,
                                     anchor_rect_global=QRect),
                orb=SimpleNamespace(apply_settings=lambda: None, update=lambda: None),
                island=SimpleNamespace(update=lambda: None),
                save_settings=lambda: None, open_habits=lambda: None, _card_ok=lambda: True)
            app._recount_habits = lambda: PomoApp._recount_habits(app)
            app.refresh_habits = lambda: PomoApp.refresh_habits(app)

            app.refresh_habits()
            st = app.habit_standing
            self.assertEqual([d.delta for d in st.days], [30, 15, -30])
            self.assertEqual(st.points, 15)
            self.assertEqual(set(storage.settled_habit_days()), {today - timedelta(days=3), today - timedelta(days=2)})

            PomoApp._maybe_habit_news(app)
            title, body = cards[0][0], cards[0][1]
            self.assertEqual(title, "Your habits, the last 3 days")
            self.assertIn("+30, +15, -30: +15 points in all.", body)
            self.assertIn("Yesterday's habits can still be ticked today", body)
            self.assertEqual(s.habit_day, (today - timedelta(days=1)).isoformat())
            PomoApp._maybe_habit_news(app)
            self.assertEqual(len(cards), 1)  # once a day

            PomoApp.toggle_habit(app, read, today)
            self.assertEqual((app.habit_standing.today_done, cheers), (1, []))
            PomoApp.toggle_habit(app, walk, today)
            self.assertEqual(cheers, [False])  # the daily target, reached
            PomoApp.toggle_habit(app, read, today - timedelta(days=2))  # final: nothing happens
            self.assertEqual(storage.habit_log()[today - timedelta(days=2)], {read})
            PomoApp.toggle_habit(app, walk, today - timedelta(days=1))  # yesterday is still open
            self.assertEqual(app.habit_standing.days[-1].delta, 15)
            storage.close()

    def test_ask_pomo_knows_your_habits(self):
        from pomodoro.ai import build_context

        today = date.today()
        s = Settings()
        s.habits = [_habit("Read", since=today), _habit("Walk", since=today)]
        ticks = {today: {s.habits[0]["id"]}}
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(Path(tmp) / "history.db")
            ctrl = SimpleNamespace(settings=s, engine=TimerEngine(s), storage=storage, agents=None,
                                   habit_standing=habits.standing(s.habits, 0, ticks, today), habit_ticks=ticks)
            text = build_context(ctrl)
            storage.close()
        self.assertIn("Habit tracker: rank Rookie 1 (0 points", text)
        self.assertIn("today 1 of 2 habits done, not yet: Walk.", text)


if __name__ == "__main__":
    unittest.main()
