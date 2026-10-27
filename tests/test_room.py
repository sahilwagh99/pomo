"""Pomo's room: what earns each item, where things go, the news when something new moves in, and drawing."""
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QImage, QPainter

from pomodoro import room
from pomodoro.config import Settings
from pomodoro.theme import THEMES


class EarningTests(unittest.TestCase):
    def test_you_start_with_a_cushion_and_a_rug(self):
        self.assertEqual(room.earned_keys(0, 0, 0), ["cushion", "round_rug"])

    def test_each_way_of_earning(self):
        self.assertIn("succulent", room.earned_keys(1.0, 0, 0))
        self.assertNotIn("succulent", room.earned_keys(0.99, 0, 0))
        self.assertIn("heart_rug", room.earned_keys(0, 10, 0))
        self.assertNotIn("heart_rug", room.earned_keys(500, 9, 6))  # hours don't buy quest items
        self.assertIn("cat", room.earned_keys(0, 0, 7))
        self.assertEqual(len(room.earned_keys(1000, 1000, 1000)), len(room.ITEMS))

    def test_every_item_has_a_place_and_a_drawing(self):
        for key, item in room.ITEMS.items():
            self.assertIn(item.slot, room.SLOTS, key)
            self.assertIn(item.kind, room.KINDS, key)
            self.assertIn(key, room._DRAW, key)
        for slot in room.SLOTS:
            self.assertTrue(any(i.slot == slot for i in room.ITEMS.values()), slot)

    def test_hints(self):
        self.assertEqual(room.hint(room.ITEMS["succulent"]), "1 focus hour")
        self.assertEqual(room.hint(room.ITEMS["monstera"]), "25 focus hours")
        self.assertEqual(room.hint(room.ITEMS["trophies"]), "20 quests")
        self.assertEqual(room.hint(room.ITEMS["cat"]), "7 Perfect days")

    def test_up_next_is_the_closest_item_each_way(self):
        rows = room.upcoming(12.0, 4, 0)
        self.assertEqual([item.key for item, _have in rows], ["bookshelf", "heart_rug", "star_curtains"])
        self.assertEqual(rows[0][1], 12.0)
        self.assertEqual(room.upcoming(1000, 1000, 1000), [])
        self.assertEqual([i.key for i, _ in room.upcoming(1000, 0, 1000)], ["print"])


class PlacementTests(unittest.TestCase):
    def setUp(self):
        self.s = Settings()
        self.have = room.earned_keys(30, 12, 0)

    def test_the_newest_item_for_each_spot_shows(self):
        placed = room.layout(self.s, self.have)
        self.assertEqual(placed["plant"], "monstera")
        self.assertEqual(placed["rug"], "heart_rug")
        self.assertEqual(placed["seat"], "cushion")
        self.assertNotIn("friend", placed)  # no cat yet

    def test_your_pick_wins_and_empty_stays_empty(self):
        self.s.room = {"plant": "succulent", "rug": ""}
        placed = room.layout(self.s, self.have)
        self.assertEqual(placed["plant"], "succulent")
        self.assertNotIn("rug", placed)

    def test_a_pick_you_dont_have_or_in_the_wrong_spot_is_ignored(self):
        self.s.room = {"plant": "bonsai", "seat": "monstera"}
        placed = room.layout(self.s, self.have)
        self.assertEqual((placed["plant"], placed["seat"]), ("monstera", "cushion"))

    def test_new_items_move_in_and_are_announced_once(self):
        self.s.room = {"plant": "succulent", "rug": ""}
        fresh = room.newly_earned(self.have, self.s.room_seen)
        self.assertNotIn("cushion", fresh)  # what you start with isn't news
        self.assertIn("monstera", fresh)
        room.move_in(self.s, ["monstera"])
        self.assertEqual(room.layout(self.s, self.have)["plant"], "monstera")
        self.assertNotIn("rug", room.layout(self.s, self.have))  # untouched: still cleared
        self.assertNotIn("monstera", room.newly_earned(self.have, self.s.room_seen))

    def test_news_text(self):
        self.assertEqual(room.news_text([]), "")
        self.assertEqual(room.news_text(["monstera"]), "New for Pomo's room: Monstera!")
        self.assertEqual(room.news_text(["succulent", "desk"]), "New for Pomo's room: Succulent and Little desk!")
        self.assertEqual(room.news_text(["succulent", "desk", "clock", "lights", "print"]),
                         "New for Pomo's room: Succulent, Little desk and 3 more!")

    def test_settings_survive_a_save_and_bad_values_are_dropped(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            self.s.room = {"plant": "succulent", "rug": ""}
            self.s.room_seen = ["succulent"]
            self.s.save(path)
            self.assertEqual(Settings.load(path).room, {"plant": "succulent", "rug": ""})
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["room"] = {"plant": 3, "desk": "desk"}
            raw["room_seen"] = ["desk", None]
            path.write_text(json.dumps(raw), encoding="utf-8")
            loaded = Settings.load(path)
            self.assertEqual((loaded.room, loaded.room_seen), ({"desk": "desk"}, ["desk"]))


class DrawingTests(unittest.TestCase):
    def render(self, theme: str, hour: int, have: list[str]) -> QImage:
        widget = room.RoomWidget(THEMES[theme])
        widget.resize(700, 260)
        widget.now = datetime(2026, 10, 27, hour, 15)
        widget.set_data(room.layout(Settings(), have), have, {"hat_trick": 3, "perfect_day": 1}, "catears")
        image = QImage(700, 260, QImage.Format_ARGB32)
        image.fill(QColor("#ffffff"))
        p = QPainter(image)
        widget.paint_room(p)
        p.end()
        return image

    def test_every_room_draws_by_day_and_night(self):
        everything = list(room.ITEMS)
        for theme, hour in (("light", 8), ("light", 13), ("dark", 18), ("dark", 23)):
            image = self.render(theme, hour, everything)
            self.assertFalse(image.isNull())
        night, day = self.render("light", 23, everything), self.render("light", 13, everything)
        sky = room.WINDOW.center()  # the room is 700/560 wide, centered
        k = 260 / room.STAGE_H
        point = (int((700 - room.STAGE_W * k) / 2 + (sky.x() - 30) * k), int(sky.y() * k - 20 * k))
        self.assertLess(QColor(night.pixel(*point)).lightness(), QColor(day.pixel(*point)).lightness())

    def test_every_item_draws_on_its_own(self):
        image = QImage(64, 64, QImage.Format_ARGB32)
        scene = room.Scene(datetime(2026, 10, 27, 22), {})
        for key in room.ITEMS:
            image.fill(QColor("#ffffff"))
            p = QPainter(image)
            room.thumbnail(p, key, QRectF(4, 4, 56, 56), scene)
            p.end()
            self.assertTrue(any(image.pixel(x, y) != QColor("#ffffff").rgba() for x in range(64) for y in range(64)),
                            key)

    def test_clicking_finds_the_spot(self):
        widget = room.RoomWidget(THEMES["light"])
        widget.resize(int(room.STAGE_W), int(room.STAGE_H))
        widget.set_data(room.layout(Settings(), room.earned_keys(0, 0, 0)), [], {}, "blossom")
        self.assertEqual(widget.slot_at(QPointF(room.POMO_X, widget._pomo_anchor().y())), "pomo")
        self.assertEqual(widget.slot_at(room.SLOTS["plant"][1].center()), "plant")
        self.assertEqual(widget.slot_at(room.WINDOW.center()), "curtains")
        self.assertIsNone(widget.slot_at(QPointF(2, 120)))

    def test_choosing_from_the_menu_reports_the_pick(self):
        widget = room.RoomWidget(THEMES["light"])
        picks = []
        widget.picked.connect(lambda slot, key: picks.append((slot, key)))
        widget.picked.emit("plant", "succulent")
        self.assertEqual(picks, [("plant", "succulent")])

    def test_clicking_an_earned_item_puts_it_in_the_room(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        shelf = room.RoomShelf(THEMES["light"])
        shelf.resize(room.RoomShelf.CELL * 5, 300)
        have = room.earned_keys(30, 0, 0)
        shelf.set_items(have, room.layout(Settings(), have))
        self.assertEqual(shelf.height(), room.RoomShelf.CELL * 4)  # 20 items, 5 a row
        picks = []
        shelf.picked.connect(lambda slot, key: picks.append((slot, key)))
        keys = list(room.ITEMS)
        for key in ("succulent", "monstera", "cat"):  # earned, already in the room, not earned
            QTest.mouseClick(shelf, Qt.LeftButton, pos=shelf._cell(keys.index(key)).center().toPoint())
        self.assertEqual(picks, [("plant", "succulent")])

    def test_badges_on_the_trophy_shelf(self):
        top = room.top_badges({"hat_trick": 2, "laser": 5, "perfect_day": 2, "gone": 9}, 3)
        self.assertEqual([q.key for q in top], ["laser", "hat_trick", "perfect_day"])


if __name__ == "__main__":
    unittest.main()
