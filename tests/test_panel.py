"""The panel: opening and closing, its pages, weather, the month view, gaming mode's switching and readout."""
import tempfile
import time
import unittest
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest

from pomodoro import gaming, notes, panel, weather
from pomodoro.app import PomoApp
from pomodoro.config import Settings
from pomodoro.engine import Mode, State
from pomodoro.storage import SessionRecord
from pomodoro.theme import theme_for


class Ctrl:
    def __init__(self):
        self.theme = theme_for("light")
        self.settings = Settings()
        self.stickies = SimpleNamespace(new_note=lambda: None)
        self.engine = SimpleNamespace(mode=Mode.SHORT, state=State.IDLE)

    def save_settings(self):
        pass

    def toggle_ultra(self):
        pass

    def open_dashboard(self):
        pass

    def open_settings(self, tab=None):
        pass


class Blank(panel.Page):
    shown = 0

    def refresh(self):
        Blank.shown += 1


class PanelTests(unittest.TestCase):
    def setUp(self):
        from PySide6.QtWidgets import QApplication

        while QApplication.activePopupWidget() is not None:  # a popup another test left open keeps a panel open
            QApplication.activePopupWidget().close()
        self._pages = list(panel.PAGES)
        panel.PAGES.clear()
        panel.register("home", "Home", "home", Blank)
        panel.register("other", "Other", "doc", Blank)
        self.ctrl = Ctrl()
        self.panel = panel.Panel(self.ctrl)

    def tearDown(self):
        self.panel.close()
        self.panel.deleteLater()
        panel.PAGES[:] = self._pages

    def test_opens_on_the_tab_you_left_it_on(self):
        self.ctrl.settings.panel_tab = "other"
        self.panel.open_at(QRect(500, 0, 200, 38), "island")
        self.assertTrue(self.panel.isVisible())
        self.assertEqual(self.panel._current, "other")
        self.panel.show_page("home")
        self.assertEqual(self.ctrl.settings.panel_tab, "home")
        QTest.keyClick(self.panel, Qt.Key_Escape)
        self.assertFalse(self.panel.isVisible())

    def test_closes_when_the_mouse_leaves_unless_pinned(self):
        self.panel.open_at(QRect(500, 0, 200, 38), "island")
        away = QPoint(self.panel.geometry().right() + 300, self.panel.geometry().bottom() + 300)
        with mock.patch.object(panel.QCursor, "pos", return_value=away):
            self.panel.toggle_pin()
            self.panel._check_pointer()
            self.panel._outside_since = time.monotonic() - 5
            self.panel._check_pointer()
            self.assertTrue(self.panel.isVisible())  # pinned
            self.panel.toggle_pin()
            self.panel._check_pointer()
            self.panel._outside_since = time.monotonic() - 5
            self.panel._check_pointer()
        self.assertFalse(self.panel.isVisible())

    def test_beside_the_orb_it_stays_on_screen(self):
        from PySide6.QtGui import QGuiApplication

        area = QGuiApplication.primaryScreen().availableGeometry()
        orb = QRect(area.right() - 230, area.bottom() - 230, 190, 190)
        self.panel.open_at(orb, "orb")
        self.assertTrue(area.adjusted(-panel.SHADOW, -panel.SHADOW, panel.SHADOW, panel.SHADOW)
                        .contains(self.panel.geometry()))


class WeatherTests(unittest.TestCase):
    def test_places_and_weather(self):
        places = weather.parse_places({"results": [
            {"name": "Calgary", "admin1": "Alberta", "country": "Canada", "latitude": 51.05, "longitude": -114.07},
            {"name": "Broken"}]})
        self.assertEqual(places, [weather.Place("Calgary, Alberta, Canada", 51.05, -114.07)])
        w = weather.parse_weather({"current": {"temperature_2m": 12.4, "weather_code": 0, "is_day": 0},
                                   "daily": {"temperature_2m_max": [23], "temperature_2m_min": [9]}})
        self.assertEqual((w.temp, w.high, w.low, w.words, w.glyph), (12.4, 23, 9, "Clear", "moon"))
        self.assertIsNone(weather.parse_weather({"oops": 1}))
        place = weather.Place("Pune", 18.52, 73.86)
        self.assertEqual(weather.Place.from_settings(place.to_settings()), place)
        self.assertIsNone(weather.Place.from_settings({}))


class GamingTests(unittest.TestCase):
    def reading(self, **kw):
        base = dict(cpu=42.0, gpu=87.0, ram=63.0, ram_used_gb=10.0, ram_total_gb=16.0, vram_used_gb=5.5,
                    cpu_temp=68.0, gpu_temp=82.0, fps=143.6, fps_source="rtss")
        base.update(kw)
        return SimpleNamespace(**base)

    def test_the_pill_readout(self):
        groups = gaming.hud_stats(self.reading())
        self.assertEqual([name for name, _ in groups], ["FPS", "GPU", "CPU"])
        self.assertEqual(groups[0][1][0].value, "144")
        self.assertEqual([s.value for s in groups[1][1]], ["87%", "82°"])
        self.assertTrue(groups[1][1][1].hot)  # 82 degrees: worth a glance
        sparse = gaming.hud_stats(self.reading(fps=None, cpu_temp=None, gpu_temp=None, gpu=None))
        self.assertEqual([name for name, _ in sparse], ["CPU", "RAM"])
        self.assertEqual(gaming.hud_stats(None), [])
        self.assertEqual(gaming.ring_fraction(self.reading()), 0.87)

    def test_switching_on_and_off(self):
        ctrl = Ctrl()
        mode = gaming.GamingMode(ctrl)
        seen = []
        mode.changed.connect(seen.append)
        game = SimpleNamespace(pid=7, exe="game.exe", title="A Game", fullscreen=True, gpu=90.0)
        desk = SimpleNamespace(pid=8, exe="notepad.exe", title="notes", fullscreen=False, gpu=0.0)
        with mock.patch.object(gaming.GamingMode, "_is_game", lambda self, fg: fg is game):
            mode._on_foreground(game)
            self.assertFalse(mode.active)  # one look isn't enough
            mode._on_foreground(game)
            self.assertTrue(mode.active)
            for _ in range(gaming.OFF_AFTER - 1):
                mode._on_foreground(desk)
            self.assertTrue(mode.active)  # a quick alt-tab doesn't end it
            mode._on_foreground(game)
            for _ in range(gaming.OFF_AFTER):
                mode._on_foreground(desk)
            self.assertFalse(mode.active)
            self.assertEqual(seen, [True, False])
            # never during a focus session
            ctrl.engine = SimpleNamespace(mode=Mode.FOCUS, state=State.RUNNING)
            for _ in range(3):
                mode._on_foreground(game)
            self.assertFalse(mode.active)
            # by hand: on even while focusing, then off until you leave the game
            mode.toggle()
            self.assertTrue(mode.active)
            mode.toggle()
            self.assertFalse(mode.active)
            ctrl.engine = SimpleNamespace(mode=Mode.SHORT, state=State.IDLE)
            for _ in range(3):
                mode._on_foreground(game)
            self.assertFalse(mode.active)  # still that game: you turned it off
            mode._on_foreground(desk)
            for _ in range(2):
                mode._on_foreground(game)
            self.assertTrue(mode.active)

    def test_marking_games(self):
        ctrl = Ctrl()
        mode = gaming.GamingMode(ctrl)
        mode.game = SimpleNamespace(pid=1, exe="Tool.exe", title="")
        mode.always_a_game()
        self.assertEqual(ctrl.settings.gaming_games, ["tool.exe"])
        mode.not_a_game()
        self.assertEqual((ctrl.settings.gaming_games, ctrl.settings.gaming_not_games), ([], ["tool.exe"]))
        self.assertEqual(gaming.game_name(SimpleNamespace(exe="eldenring.exe", title="")), "Eldenring")


class MonthTests(unittest.TestCase):
    def test_cells_and_minutes(self):
        from pomodoro.panel_calendar import MonthView

        view = MonthView(Ctrl())
        view.resize(320, 220)
        view.set_month(2026, 11, {date(2026, 11, 3): 50})
        cells = view._cells()
        self.assertEqual(len(cells) % 7, 0)
        self.assertIn(date(2026, 11, 30), [d for d, _ in cells])
        view.grab()  # paints without trouble


class QuickNoteTests(unittest.TestCase):
    def test_goes_into_todays_note(self):
        with tempfile.TemporaryDirectory() as folder:
            vault = notes.Vault(Path(folder) / "Notes")
            vault.ensure()
            app = SimpleNamespace(notes_window=None, notes_vault=lambda: vault)
            self.assertTrue(PomoApp.add_to_daily_note(app, "buy milk"))
            self.assertTrue(PomoApp.add_to_daily_note(app, "call Sam"))
            text = vault.read(notes.daily_path(vault, date.today()))
            lines = text.strip().splitlines()
            self.assertTrue(lines[0].endswith("buy milk") and lines[1].endswith("call Sam"))
            self.assertRegex(lines[0], r"^- \d\d:\d\d buy milk$")


if __name__ == "__main__":
    unittest.main()
