"""Your path as a game (XP, levels, the skill tree), the big picture, and the focus wallpaper."""
import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PySide6.QtCore import QCoreApplication, QEvent, QSize
from PySide6.QtGui import QColor, QIcon, QImage

from pomodoro import skills, studypath
from pomodoro.config import Settings
from pomodoro.theme import theme_for
from pomodoro.wallpaper import FocusWallpaper, render

DAY = date(2026, 10, 29)


def _path() -> dict:
    path = studypath.make("Data analyst", "", 8, [
        {"title": "Python", "steps": [{"title": "Loops"}, {"title": "Functions"}]},
        {"title": "Pandas", "steps": [{"title": "Read a CSV"}, {"title": "Group and summarise"}]},
        {"title": "SQL", "steps": [{"title": "GROUP BY"}]},
    ], DAY)
    studypath.set_done(path, 0, 0, True, DAY)
    return path


class XpTests(unittest.TestCase):
    def test_levels_get_a_little_longer_each_time(self):
        self.assertEqual(skills.level_for(0), (1, 0, 100))
        self.assertEqual(skills.level_for(100), (2, 0, 150))
        self.assertEqual(skills.level_for(260), (3, 10, 200))
        self.assertEqual(skills.rank(1), "Sprout")
        self.assertEqual(skills.rank(10), "Scholar")
        self.assertEqual(skills.rank(99), "Legend")

    def test_earning_xp_and_levelling_up(self):
        s = Settings()
        s.xp = 90
        self.assertTrue(skills.add(s, 25, "2026-10-29"))  # 115: level 2
        self.assertEqual((s.xp, s.xp_today), (115, 25))
        self.assertFalse(skills.add(s, 10, "2026-10-29"))
        self.assertEqual(s.xp_today, 35)
        skills.add(s, 5, "2026-10-30")  # a new day starts the count again
        self.assertEqual(s.xp_today, 5)
        skills.add(s, -500, "2026-10-30")  # an unticked step takes its XP back, never below zero
        self.assertEqual(s.xp, 0)
        self.assertIn("Level 1, Sprout", skills.xp_line(s))

    def test_starting_xp_counts_what_you_already_did(self):
        self.assertEqual(skills.starting_xp(3600 * 2 + 30, _path()), 120 + skills.STEP_XP)
        self.assertEqual(skills.starting_xp(0, {}), 0)

    def test_settings_keep_it_sane(self):
        s = Settings()
        self.assertEqual(s.xp, -1)  # not counted yet
        s.xp, s.xp_today, s.tree_seen = -9, -3, ["loops", 4]
        s.path_map = {"summary": "x", "links": [["a", "b", "why"], ["bad"]], "roles": {"a": "does", "b": 3}}
        s.clamp()
        self.assertEqual((s.xp, s.xp_today, s.tree_seen), (-1, 0, ["loops"]))
        self.assertEqual(s.path_map["links"], [["a", "b", "why"]])
        self.assertEqual(s.path_map["roles"], {"a": "does"})


class MapTests(unittest.TestCase):
    def test_the_request_numbers_every_step(self):
        text = skills.map_request(_path())
        self.assertIn("Goal: Data analyst", text)
        self.assertIn("S3: Read a CSV", text)
        self.assertIn("S5: GROUP BY", text)

    def test_reading_the_ais_map(self):
        reply = ("Here's the map:\n**SUMMARY:** Python first, then pandas, then SQL.\n"
                 "ROLE: S1 | Repeat work without copying code\n- ROLE: s4: Turn rows into totals\nROLE: S9 | nope\n"
                 "LINK: S1 > S3 | loops read files line by line\nLINK: S4 -> S5 | groupby is GROUP BY\n"
                 "LINK: S1 > S2 | same milestone, dropped\nLINK: S2 → S8 | unknown step, dropped\n")
        pmap = skills.parse_map(reply, _path())
        self.assertEqual(pmap["summary"], "Python first, then pandas, then SQL.")
        self.assertEqual(pmap["roles"], {"Loops": "Repeat work without copying code",
                                         "Group and summarise": "Turn rows into totals"})
        self.assertEqual(pmap["links"], [["Loops", "Read a CSV", "loops read files line by line"],
                                         ["Group and summarise", "GROUP BY", "groupby is GROUP BY"]])

    def test_the_signature_follows_renames_not_ticks(self):
        path = _path()
        sig = skills.signature(path)
        studypath.set_done(path, 0, 1, True, DAY)
        self.assertEqual(skills.signature(path), sig)
        path["milestones"][1]["steps"][0]["title"] = "Read an Excel file"
        self.assertNotEqual(skills.signature(path), sig)

    def test_what_a_step_builds_on_and_leads_to(self):
        path = _path()
        pmap = {"links": [["Loops", "Read a CSV", "why 1"], ["Read a CSV", "GROUP BY", "why 2"]]}
        self.assertEqual(skills.around("Read a CSV", path, pmap), ([("Loops", "why 1")], [("GROUP BY", "why 2")]))
        before, after = skills.around("Functions", path, pmap)  # no links: its neighbours on the path
        self.assertEqual((before[0][0], after[0][0]), ("Loops", "Read a CSV"))


class FakeWallpaper:
    """A stand-in for Windows' wallpaper (the real one is never touched by the tests)."""

    def __init__(self, walls, slideshow=False):
        self.walls, self._slideshow, self.calls = dict(walls), slideshow, []

    def monitors(self):
        return list(self.walls)

    def get(self, m):
        return self.walls[m]

    def set(self, m, path):
        self.calls.append(("set", m, Path(path).name))
        self.walls[m] = path
        return True

    def clear(self):
        self.calls.append(("clear",))

    def position(self):
        return 2

    def set_position(self, v):
        self.calls.append(("position", v))

    def slideshow(self):
        return self._slideshow


class WallpaperTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.mine = self.dir / "mine.jpg"
        QImage(8, 8, QImage.Format_RGB32).save(str(self.mine))
        self.picture = QImage(32, 18, QImage.Format_RGB32)
        self.picture.fill(QColor("#f8f3ea"))
        self.no_registry = mock.patch("pomodoro.wallpaper.background_type", return_value=None)
        self.no_registry.start()

    def tearDown(self):
        self.no_registry.stop()

    def _swapper(self, fake):
        return FocusWallpaper(self.dir / "wallpaper.json", self.dir / "cache", make_backend=lambda: fake)

    def test_it_shows_while_you_focus_and_puts_yours_back(self):
        fake = FakeWallpaper({"A": str(self.mine), "B": ""})
        w = self._swapper(fake)
        w.show(self.picture, "light|Loops|")
        w.wait()
        state = json.loads((self.dir / "wallpaper.json").read_text(encoding="utf-8"))
        self.assertEqual(state["monitors"], {"A": str(self.mine), "B": ""})  # noted before anything changed
        self.assertEqual(fake.calls, [("position", 4), ("set", "A", "focus_wallpaper_a.png"),
                                      ("set", "B", "focus_wallpaper_a.png")])
        w.show(self.picture, "light|Loops|")  # the same words: nothing to do
        w.show(self.picture, "light|Functions|")
        w.restore()
        w.shutdown()
        self.assertEqual(fake.calls[3:], [("position", 4), ("set", "A", "focus_wallpaper_b.png"),
                                          ("set", "B", "focus_wallpaper_b.png"), ("set", "A", "mine.jpg"),
                                          ("clear",), ("position", 2)])
        self.assertFalse((self.dir / "wallpaper.json").exists())

    def test_a_wallpaper_you_picked_meanwhile_stays(self):
        fake = FakeWallpaper({"A": str(self.mine)})
        w = self._swapper(fake)
        w.show(self.picture, "k")
        w.wait()
        fake.walls["A"] = str(self.dir / "new_favourite.jpg")
        w.restore()
        w.shutdown()
        self.assertNotIn(("set", "A", "mine.jpg"), fake.calls)

    def test_a_slideshow_or_spotlight_is_left_alone(self):
        for fake in (FakeWallpaper({"A": str(self.mine)}, slideshow=True),
                     FakeWallpaper({"A": r"C:\Users\me\AppData\Local\Packages\MicrosoftWindows.Client.CBS\LocalCache"
                                         r"\Microsoft\IrisService\1\img.jpg"})):
            w = self._swapper(fake)
            w.show(self.picture, "k")
            w.shutdown()
            self.assertEqual([c for c in fake.calls if c[0] == "set"], [])
            self.assertFalse((self.dir / "wallpaper.json").exists())

    def test_after_a_crash_yours_comes_back_next_time(self):
        fake = FakeWallpaper({"A": str(self.mine)})
        w = self._swapper(fake)
        w.show(self.picture, "k")
        w.wait()
        w._jobs.put(None)  # Pomo stops without restoring (a crash, a power cut)
        w._thread.join(2)
        again = self._swapper(fake)
        again.restore()
        again.shutdown()
        self.assertEqual(fake.walls["A"], str(self.mine))

    def test_windows_own_cached_copy_is_kept(self):
        themes = self.dir / "appdata" / "Microsoft" / "Windows" / "Themes"
        themes.mkdir(parents=True)
        cached = themes / "TranscodedWallpaper"
        cached.write_bytes(b"picture bytes")
        fake = FakeWallpaper({"A": str(self.dir / "deleted_long_ago.jpg")})
        with mock.patch.dict(os.environ, {"APPDATA": str(self.dir / "appdata")}):
            w = self._swapper(fake)
            w.show(self.picture, "k")
            w.restore()
            w.shutdown()
        self.assertEqual(Path(fake.walls["A"]).name, "your_wallpaper_0.jpg")
        self.assertEqual(Path(fake.walls["A"]).read_bytes(), b"picture bytes")

    def test_the_picture(self):
        image = render(theme_for("dark"), "Clean messy data, then group it by month and write down what you found",
                       "Pandas  ·  Data analyst", QSize(1280, 720))
        self.assertEqual((image.width(), image.height()), (1280, 720))
        self.assertNotEqual(image.pixelColor(640, 360), image.pixelColor(10, 10))


class WindowTests(unittest.TestCase):
    def _ctrl(self, path, pmap=None):
        s = Settings()
        s.study_path, s.path_map, s.xp = path, pmap or {}, 260
        return SimpleNamespace(settings=s, theme=theme_for("light"), icon=QIcon(), save_settings=lambda: None,
                               has_ai_key=lambda: False, open_path=lambda: None, start_step=lambda *a, **k: None,
                               open_teach=lambda *a: None)

    def _close(self, *windows):
        for window in windows:  # gone now, not when Python shuts down (Qt can crash then)
            window.close()
            window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_the_skill_tree_and_the_map(self):
        from pomodoro.skill_windows import PathMapWindow

        path = _path()
        ctrl = self._ctrl(path, {"summary": "Python, then pandas.", "roles": {"Functions": "Reuse your steps"},
                                 "links": [["Loops", "Read a CSV", "loops read files"]]})
        w = PathMapWindow(ctrl)
        w.resize(900, 820)
        w.show()
        self.assertEqual(self.states(w), {"Loops": "done", "Functions": "today", "Read a CSV": "locked",
                                          "Group and summarise": "locked", "GROUP BY": "locked"})
        self.assertEqual(w.level.text(), "Level 3")
        self.assertIn("loops", ctrl.settings.tree_seen)  # its unlock was celebrated
        self.assertIn((0, 0), w.tree.bursts)
        self.assertEqual(w.node_title.text(), "Functions")
        self.assertFalse(w.grab().isNull())
        w.show_tab(1)
        self.assertEqual(w.map.today, "Functions")
        self.assertEqual(len(w.map.links), 1)
        self.assertIn("Reuse your steps", w.map_info.text())
        self.assertIn("Builds on: <b>Loops</b>", w.map_info.text())  # no link for it: its neighbours
        self.assertFalse(w.draw_btn.isEnabled())  # no AI key
        self.assertFalse(w.grab().isNull())
        self._close(w)

    def test_no_path_yet(self):
        from pomodoro.skill_windows import PathMapWindow

        w = PathMapWindow(self._ctrl({}))
        w.show()
        self.assertTrue(w.empty.isVisible())
        self.assertFalse(w.stack.isVisible())
        self._close(w)

    @staticmethod
    def states(w):
        return {n["step"]["title"]: n["state"] for n in w.tree.nodes}


if __name__ == "__main__":
    unittest.main()
