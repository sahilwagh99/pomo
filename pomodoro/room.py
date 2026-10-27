"""Pomo's room: a little room on the dashboard that fills up with things you earn.

Focus hours, daily quests and Perfect days each earn furniture (a desk, plants, string lights,
a sleepy cat...). Every item has its place in the room. A new item goes straight in, and you can
click any spot to swap what's there for something else you've earned, or clear it. The window
shows the time of day, and at night the lamps glow while Pomo dozes.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontMetricsF,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import QMenu, QSizePolicy, QToolTip, QWidget

from . import quests
from .pomo import OUTLINE, Pomo
from .theme import FONT_FAMILY

STAGE_W, STAGE_H = 560.0, 250.0  # the room's own coordinates; it's scaled to fit
FLOOR_Y = 176.0
POMO_X = 281.0
POMO_SIZE = 66.0

KINDS = {"hours": ("focus hour", "focus hours"), "quests": ("quest", "quests"),
         "perfect": ("Perfect day", "Perfect days")}


@dataclass(frozen=True)
class Item:
    key: str
    name: str
    slot: str
    kind: str     # what earns it: "hours" (focus hours), "quests" (daily quests), "perfect" (Perfect days)
    at: int       # how many it takes (0: in the room from the start)
    note: str = ""


ITEMS: dict[str, Item] = {i.key: i for i in (  # within a slot, later items are the fancier ones
    Item("cushion", "Floor cushion", "seat", "hours", 0),
    Item("round_rug", "Round rug", "rug", "hours", 0),
    Item("succulent", "Succulent", "plant", "hours", 1),
    Item("desk", "Little desk", "desk", "hours", 3),
    Item("print", "Sakura print", "wall", "quests", 3),
    Item("clock", "Wall clock", "wall", "hours", 5, "It shows the real time"),
    Item("lights", "String lights", "lights", "hours", 10, "They glow in the evening"),
    Item("heart_rug", "Heart rug", "rug", "quests", 10),
    Item("bookshelf", "Bookshelf", "shelf", "hours", 15),
    Item("trophies", "Trophy shelf", "shelf", "quests", 20, "It shows off your top badges"),
    Item("monstera", "Monstera", "plant", "hours", 25),
    Item("curtains", "Sakura curtains", "curtains", "hours", 35),
    Item("beanbag", "Bean bag", "seat", "hours", 50),
    Item("star_curtains", "Starry curtains", "curtains", "perfect", 3),
    Item("floor_lamp", "Floor lamp", "corner", "hours", 70, "It glows at night"),
    Item("fishbowl", "Fish bowl", "corner", "quests", 40),
    Item("cat", "Sleepy cat", "friend", "perfect", 7),
    Item("study_desk", "Study desk", "desk", "hours", 100, "With a laptop and a lamp"),
    Item("bonsai", "Sakura bonsai", "plant", "hours", 150),
    Item("cloud_sofa", "Cloud sofa", "seat", "hours", 200),
)}

SLOTS: dict[str, tuple[str, QRectF]] = {  # slot -> (label, its box in the room), drawn back to front
    "lights": ("String lights", QRectF(14, 4, 532, 30)),
    "wall": ("Wall", QRectF(40, 26, 66, 72)),
    "curtains": ("Window", QRectF(200, 20, 160, 124)),
    "shelf": ("Shelf", QRectF(428, 46, 114, 54)),
    "rug": ("Rug", QRectF(178, 194, 206, 40)),
    "desk": ("Desk", QRectF(22, 96, 134, 102)),
    "corner": ("Corner", QRectF(366, 82, 54, 116)),
    "plant": ("Plant", QRectF(470, 108, 66, 92)),
    "friend": ("Friend", QRectF(160, 170, 58, 34)),
    "seat": ("Pomo's seat", QRectF(214, 140, 134, 66)),
}
WINDOW = QRectF(224, 30, 112, 100)
SIT = {"cushion": 21.0, "beanbag": 24.0, "cloud_sofa": 30.0}  # how far above its box's bottom Pomo sits


# ------------------------------------------------------------------ earning
def earned(item: Item, hours: float, quests_total: int, perfect_days: int) -> bool:
    have = {"hours": hours, "quests": quests_total, "perfect": perfect_days}[item.kind]
    return have >= item.at


def earned_keys(hours: float, quests_total: int, perfect_days: int) -> list[str]:
    return [k for k, item in ITEMS.items() if earned(item, hours, quests_total, perfect_days)]


def hint(item: Item) -> str:
    """'25 focus hours', '10 quests', '3 Perfect days'."""
    one, many = KINDS[item.kind]
    return f"{item.at} {one if item.at == 1 else many}"


def newly_earned(keys: list[str], seen: list[str]) -> list[str]:
    """Earned items that haven't been announced (the ones you start with never are)."""
    return [k for k in keys if k not in seen and ITEMS[k].at > 0]


def news_text(keys: list[str]) -> str:
    """'New for Pomo's room: Monstera!' or '...: Succulent, Little desk and 3 more!'."""
    names = [ITEMS[k].name for k in keys]
    if not names:
        return ""
    if len(names) > 3:
        names = names[:2] + [f"{len(names) - 2} more"]
    listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return f"New for Pomo's room: {listed}!"


def move_in(settings, keys: list[str]) -> None:
    """New items go straight into the room: clear any pick for their spot so the newest shows."""
    for key in keys:
        settings.room.pop(ITEMS[key].slot, None)
    settings.room_seen += [k for k in keys if k not in settings.room_seen]


def layout(settings, have: list[str]) -> dict[str, str]:
    """What's in each spot: your pick (if you still have it), else the newest item you've earned for it."""
    placed: dict[str, str] = {}
    for slot in SLOTS:
        options = [k for k in have if ITEMS[k].slot == slot]
        pick = settings.room.get(slot)
        if pick == "":
            continue  # you cleared it
        if pick in options:
            placed[slot] = pick
        elif options:
            placed[slot] = options[-1]
    return placed


def upcoming(hours: float, quests_total: int, perfect_days: int, count: int = 3) -> list[tuple[Item, float]]:
    """The next item to earn each way (focus hours, quests, Perfect days), with how far along you are."""
    have = {"hours": hours, "quests": quests_total, "perfect": perfect_days}
    out = []
    for kind in KINDS:
        ahead = sorted((i for i in ITEMS.values() if i.kind == kind and i.at > have[kind]), key=lambda i: i.at)
        if ahead:
            out.append((ahead[0], have[kind]))
    out.sort(key=lambda pair: -pair[1] / pair[0].at)
    return out[:count]


def top_badges(badges: dict[str, int], count: int = 4) -> list[quests.Quest]:
    ranked = sorted(((n, k) for k, n in badges.items() if n > 0 and quests.badge(k)), key=lambda x: (-x[0], x[1]))
    return [quests.badge(k) for _n, k in ranked[:count]]


def part_of_day(hour: int) -> str:
    if 6 <= hour < 10:
        return "morning"
    if 10 <= hour < 17:
        return "day"
    if 17 <= hour < 20:
        return "evening"
    return "night"


# ------------------------------------------------------------------ drawing
@dataclass
class Scene:
    now: datetime
    badges: dict

    @property
    def glow(self) -> float:
        """How much lamps and lights glow: none by day, a little in the evening, fully at night."""
        return {"evening": 0.6, "night": 1.0}.get(part_of_day(self.now.hour), 0.0)


def _pen(lw: float) -> QPen:
    return QPen(OUTLINE, lw, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)


def _glow(p: QPainter, c: QPointF, r: float, strength: float, color: str = "#ffe7a3") -> None:
    if strength <= 0:
        return
    gradient = QRadialGradient(c, r)
    inner = QColor(color)
    inner.setAlpha(int(150 * strength))
    outer = QColor(color)
    outer.setAlpha(0)
    gradient.setColorAt(0.0, inner)
    gradient.setColorAt(1.0, outer)
    p.setPen(Qt.NoPen)
    p.setBrush(gradient)
    p.drawEllipse(c, r, r)


def draw_item(p: QPainter, key: str, r: QRectF, lw: float, scene: Scene) -> None:
    """Draw an item into its box. lw is the outline width in the same units as the box."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    _DRAW[key](p, r, lw, scene)
    p.restore()


def _cushion(p, r, lw, _scene) -> None:
    cx, bottom = r.center().x(), r.bottom()
    p.setPen(_pen(lw))
    p.setBrush(QColor("#f6b3c4"))
    p.drawRoundedRect(QRectF(cx - r.width() * 0.34, bottom - 26, r.width() * 0.68, 22), 11, 11)
    p.setPen(QPen(QColor(91, 44, 41, 70), lw * 0.7))
    p.drawLine(QPointF(cx - r.width() * 0.26, bottom - 15), QPointF(cx + r.width() * 0.26, bottom - 15))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255, 140))
    p.drawEllipse(QPointF(cx - r.width() * 0.2, bottom - 21), 7, 2.2)


def _beanbag(p, r, lw, _scene) -> None:
    cx, bottom = r.center().x(), r.bottom()
    bag = QPainterPath()
    bag.addEllipse(QPointF(cx, bottom - 40), r.width() * 0.3, 26)
    base = QPainterPath()
    base.addEllipse(QPointF(cx, bottom - 17), r.width() * 0.4, 16)
    shape = bag.united(base)
    p.setPen(_pen(lw))
    p.setBrush(QColor("#c9c3ea"))
    p.drawPath(shape)
    p.setPen(QPen(QColor(91, 44, 41, 70), lw * 0.7, Qt.SolidLine, Qt.RoundCap))
    p.drawArc(QRectF(cx - r.width() * 0.3, bottom - 34, r.width() * 0.6, 20), 200 * 16, 140 * 16)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255, 120))
    p.drawEllipse(QPointF(cx - r.width() * 0.17, bottom - 54), 8, 4)


def _cloud_sofa(p, r, lw, _scene) -> None:
    cx, bottom, w = r.center().x(), r.bottom(), r.width()
    back = QPainterPath()
    for dx, dy, rad in ((-0.3, 44, 21), (0.0, 50, 24), (0.3, 44, 21)):
        bump = QPainterPath()
        bump.addEllipse(QPointF(cx + dx * w, bottom - dy), rad, rad)
        back = back.united(bump)
    seat = QPainterPath()
    seat.addRoundedRect(QRectF(cx - w * 0.47, bottom - 36, w * 0.94, 30), 15, 15)
    sofa = back.united(seat)
    p.setPen(_pen(lw))
    p.setBrush(QColor("#4b4a6b"))
    for side in (-1, 1):
        p.drawRoundedRect(QRectF(cx + side * w * 0.36 - 3, bottom - 8, 6, 8), 2, 2)
    p.setBrush(QColor("#eaf2fb"))
    p.drawPath(sofa)
    p.setPen(QPen(QColor(91, 44, 41, 60), lw * 0.7, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(cx - w * 0.36, bottom - 26), QPointF(cx + w * 0.36, bottom - 26))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255, 200))
    p.drawEllipse(QPointF(cx - w * 0.32, bottom - 52), 7, 4)
    p.drawEllipse(QPointF(cx - w * 0.02, bottom - 60), 8, 4)


def _round_rug(p, r, lw, _scene) -> None:
    p.setPen(_pen(lw))
    p.setBrush(QColor("#f7c59f"))
    p.drawEllipse(r.adjusted(2, 2, -2, -2))
    p.setPen(QPen(QColor("#fffdf8"), lw * 1.2, Qt.DashLine, Qt.RoundCap))
    p.setBrush(QColor("#fbdcc0"))
    p.drawEllipse(r.adjusted(r.width() * 0.12, r.height() * 0.18, -r.width() * 0.12, -r.height() * 0.18))


def _heart_path(c: QPointF, w: float, h: float) -> QPainterPath:
    x, y = c.x(), c.y()
    path = QPainterPath(QPointF(x, y + h * 0.5))
    path.cubicTo(x - w * 0.55, y + h * 0.15, x - w * 0.55, y - h * 0.5, x - w * 0.26, y - h * 0.5)
    path.cubicTo(x - w * 0.1, y - h * 0.5, x, y - h * 0.32, x, y - h * 0.2)
    path.cubicTo(x, y - h * 0.32, x + w * 0.1, y - h * 0.5, x + w * 0.26, y - h * 0.5)
    path.cubicTo(x + w * 0.55, y - h * 0.5, x + w * 0.55, y + h * 0.15, x, y + h * 0.5)
    return path


def _heart_rug(p, r, lw, _scene) -> None:
    c = r.center()
    p.setPen(_pen(lw))
    p.setBrush(QColor("#f6b3c4"))
    p.drawPath(_heart_path(c, r.width() * 0.9, r.height() * 0.95))
    p.setPen(QPen(QColor("#fffdf8"), lw * 1.2, Qt.DashLine, Qt.RoundCap))
    p.setBrush(QColor("#fbd0d7"))
    p.drawPath(_heart_path(QPointF(c.x(), c.y() + r.height() * 0.02), r.width() * 0.62, r.height() * 0.6))


def _pot(p, cx: float, bottom: float, w: float, h: float, lw: float, color: str, band: str) -> None:
    pot = QPainterPath(QPointF(cx - w / 2, bottom - h))
    pot.lineTo(cx + w / 2, bottom - h)
    pot.lineTo(cx + w * 0.36, bottom)
    pot.lineTo(cx - w * 0.36, bottom)
    pot.closeSubpath()
    p.setPen(_pen(lw))
    p.setBrush(QColor(color))
    p.drawPath(pot)
    p.setBrush(QColor(band))
    p.drawRoundedRect(QRectF(cx - w / 2 - 2, bottom - h - 2, w + 4, h * 0.28), 3, 3)


def _leaf(p, base: QPointF, angle: float, length: float, width: float, lw: float, color: str) -> None:
    p.save()
    p.translate(base)
    p.rotate(angle)
    leaf = QPainterPath(QPointF(0, 0))
    leaf.cubicTo(width, -length * 0.25, width * 0.8, -length * 0.8, 0, -length)
    leaf.cubicTo(-width * 0.8, -length * 0.8, -width, -length * 0.25, 0, 0)
    p.setPen(_pen(lw))
    p.setBrush(QColor(color))
    p.drawPath(leaf)
    p.restore()


def _succulent(p, r, lw, _scene) -> None:
    cx, bottom = r.center().x(), r.bottom()
    stool = QRectF(cx - 22, bottom - 30, 44, 8)
    p.setPen(_pen(lw))
    p.setBrush(QColor("#e3b98f"))
    for side in (-1, 1):
        p.drawLine(QPointF(cx + side * 16, bottom - 22), QPointF(cx + side * 19, bottom))
    p.drawRoundedRect(stool, 4, 4)
    for angle, length in ((-62, 20), (62, 20), (-30, 24), (30, 24), (0, 26)):
        _leaf(p, QPointF(cx, bottom - 50), angle, length, 8, lw, "#a6cf92" if angle else "#b9dca6")
    _pot(p, cx, bottom - 30, 30, 24, lw, "#f7c59f", "#f2a88f")


def _monstera_leaf(p, base: QPointF, angle: float, size: float, lw: float) -> None:
    p.save()
    p.translate(base)
    p.rotate(angle)
    leaf = QPainterPath(QPointF(0, 0))
    leaf.cubicTo(size * 0.75, -size * 0.1, size * 0.7, -size * 0.95, 0, -size)
    leaf.cubicTo(-size * 0.7, -size * 0.95, -size * 0.75, -size * 0.1, 0, 0)
    p.setPen(_pen(lw))
    p.setBrush(QColor("#86b874"))
    p.drawPath(leaf)
    p.setPen(QPen(QColor("#d7ecbd"), lw * 1.1, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(0, -size * 0.08), QPointF(0, -size * 0.9))
    for t in (0.35, 0.6):  # the splits monsteras are known for
        for side in (-1, 1):
            p.drawLine(QPointF(side * size * 0.12, -size * t), QPointF(side * size * 0.48, -size * (t - 0.1)))
    p.restore()


def _monstera(p, r, lw, _scene) -> None:
    cx, bottom = r.center().x(), r.bottom()
    stems = ((-34, 34, 30), (32, 30, 28), (-6, 46, 30))
    p.setPen(QPen(QColor("#6f9d60"), lw * 1.3, Qt.SolidLine, Qt.RoundCap))
    tops = []
    for angle, length, _size in stems:
        a = math.radians(angle)
        top = QPointF(cx + math.sin(a) * length, bottom - 30 - math.cos(a) * length)
        p.drawLine(QPointF(cx, bottom - 30), top)
        tops.append(top)
    for (angle, _length, size), top in zip(stems, tops):
        _monstera_leaf(p, top, angle * 0.8, size, lw)
    _pot(p, cx, bottom, 40, 32, lw, "#fffdf8", "#f6b3c4")


def _bonsai(p, r, lw, _scene) -> None:
    cx, bottom = r.center().x(), r.bottom()
    p.setPen(_pen(lw))
    p.setBrush(QColor("#e3b98f"))  # a little wooden stand
    p.drawRoundedRect(QRectF(cx - 26, bottom - 26, 52, 8), 3, 3)
    for side in (-1, 1):
        p.drawLine(QPointF(cx + side * 20, bottom - 18), QPointF(cx + side * 22, bottom))
    trunk = QPainterPath(QPointF(cx - 4, bottom - 40))
    trunk.cubicTo(cx - 12, bottom - 54, cx + 10, bottom - 60, cx + 2, bottom - 74)
    p.setPen(QPen(QColor("#7d5148"), lw * 3.2, Qt.SolidLine, Qt.RoundCap))
    p.setBrush(Qt.NoBrush)
    p.drawPath(trunk)
    p.drawLine(QPointF(cx - 3, bottom - 58), QPointF(cx - 16, bottom - 66))
    rng = random.Random(4)
    for dx, dy, rad in ((2, -78, 13), (-16, -68, 10), (14, -66, 10), (-6, -84, 8), (12, -82, 8)):
        p.setPen(_pen(lw * 0.8))
        p.setBrush(QColor(rng.choice(("#fbd0d7", "#f6b3c4", "#f9c2cd"))))
        p.drawEllipse(QPointF(cx + dx, bottom + dy), rad, rad * 0.86)
    p.setPen(_pen(lw))
    p.setBrush(QColor("#a7b3e3"))
    p.drawRoundedRect(QRectF(cx - 22, bottom - 42, 44, 16), 5, 5)


def _desk_frame(p, r, lw) -> float:
    """A little wooden desk with a drawer; returns where its top is."""
    x, w, bottom = r.x(), r.width(), r.bottom()
    top = bottom - 50
    p.setPen(_pen(lw))
    p.setBrush(QColor("#d9ad83"))
    for lx in (x + 10, x + w - 16):
        p.drawRoundedRect(QRectF(lx, top + 6, 6, bottom - top - 6), 2, 2)
    p.setBrush(QColor("#e8c39b"))
    p.drawRoundedRect(QRectF(x + w * 0.5, top + 6, w * 0.42, 18), 3, 3)
    p.setBrush(QColor("#fffdf8"))
    p.drawEllipse(QPointF(x + w * 0.71, top + 15), 2.2, 2.2)
    p.setBrush(QColor("#e8c39b"))
    p.drawRoundedRect(QRectF(x + 2, top, w - 4, 9), 3, 3)
    return top


def _desk(p, r, lw, _scene) -> None:
    top = _desk_frame(p, r, lw)
    x = r.x()
    p.setPen(_pen(lw))
    for i, (color, h) in enumerate((("#a9c7ee", 7), ("#f6b3c4", 6), ("#f9dc8a", 6))):  # a stack of books
        y = top - 1 - sum((7, 6, 6)[:i + 1])
        p.setBrush(QColor(color))
        p.drawRoundedRect(QRectF(x + 14 + i * 2, y, 38 - i * 4, h), 2, 2)
    mug = QRectF(x + r.width() - 40, top - 18, 14, 17)
    p.setBrush(QColor("#fffdf8"))
    p.drawArc(QRectF(mug.right() - 4, mug.top() + 4, 9, 9), -90 * 16, 180 * 16)
    p.drawRoundedRect(mug, 3, 3)
    p.setPen(QPen(QColor(91, 44, 41, 90), lw * 0.7, Qt.SolidLine, Qt.RoundCap))
    for dx in (4, 9):  # steam
        steam = QPainterPath(QPointF(mug.left() + dx, mug.top() - 3))
        steam.cubicTo(mug.left() + dx - 3, mug.top() - 7, mug.left() + dx + 3, mug.top() - 10, mug.left() + dx, mug.top() - 14)
        p.drawPath(steam)


def _study_desk(p, r, lw, scene) -> None:
    top = _desk_frame(p, r, lw)
    x, w = r.x(), r.width()
    p.setPen(_pen(lw))
    screen = QRectF(x + 16, top - 34, 50, 32)  # laptop
    p.setBrush(QColor("#fffdf8"))
    p.drawRoundedRect(screen, 4, 4)
    p.setBrush(QColor("#d4ecf7" if scene.glow < 0.5 else "#e7f1ff"))
    p.drawRoundedRect(screen.adjusted(4, 4, -4, -4), 2, 2)
    p.setPen(QPen(QColor("#9aa7df"), lw * 0.8, Qt.SolidLine, Qt.RoundCap))
    for i, length in enumerate((24, 16, 20)):
        p.drawLine(QPointF(screen.left() + 9, screen.top() + 10 + i * 5), QPointF(screen.left() + 9 + length, screen.top() + 10 + i * 5))
    p.setPen(_pen(lw))
    p.setBrush(QColor("#fffdf8"))
    p.drawRoundedRect(QRectF(screen.left() - 6, top - 4, screen.width() + 12, 5), 2, 2)
    lamp_x = x + w - 26  # desk lamp
    p.setPen(_pen(lw))
    p.setBrush(QColor("#f9dc8a"))
    p.drawRoundedRect(QRectF(lamp_x - 10, top - 4, 20, 5), 2, 2)
    p.setPen(QPen(OUTLINE, lw * 1.6, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(lamp_x, top - 3), QPointF(lamp_x - 6, top - 26))
    p.drawLine(QPointF(lamp_x - 6, top - 26), QPointF(lamp_x + 4, top - 42))
    shade = QPainterPath(QPointF(lamp_x - 6, top - 44))
    shade.lineTo(lamp_x + 10, top - 50)
    shade.lineTo(lamp_x + 20, top - 32)
    shade.lineTo(lamp_x + 2, top - 30)
    shade.closeSubpath()
    p.setPen(_pen(lw))
    p.setBrush(QColor("#f9dc8a"))
    p.drawPath(shade)
    _glow(p, QPointF(lamp_x + 12, top - 24), 26, scene.glow)


def _clock(p, r, lw, scene) -> None:
    c = r.center()
    rad = min(r.width(), r.height()) / 2 - 4
    p.setPen(_pen(lw))
    p.setBrush(QColor("#f6b3c4"))
    p.drawEllipse(c, rad, rad)
    p.setBrush(QColor("#fffdf8"))
    p.drawEllipse(c, rad * 0.8, rad * 0.8)
    p.setPen(QPen(OUTLINE, lw * 0.9, Qt.SolidLine, Qt.RoundCap))
    for k in range(12):
        a = 2 * math.pi * k / 12
        inner = rad * (0.6 if k % 3 == 0 else 0.66)
        p.drawLine(QPointF(c.x() + math.sin(a) * inner, c.y() - math.cos(a) * inner),
                   QPointF(c.x() + math.sin(a) * rad * 0.72, c.y() - math.cos(a) * rad * 0.72))
    now = scene.now
    minute = (now.minute + now.second / 60) / 60
    hour = ((now.hour % 12) + minute) / 12
    for share, length, width in ((hour, 0.4, 1.8), (minute, 0.58, 1.3)):
        a = 2 * math.pi * share
        p.setPen(QPen(OUTLINE, lw * width, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(c, QPointF(c.x() + math.sin(a) * rad * length, c.y() - math.cos(a) * rad * length))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#f0a3b1"))
    p.drawEllipse(c, 2.4, 2.4)


def _print(p, r, lw, _scene) -> None:
    frame = QRectF(r.center().x() - 27, r.y() + 4, 54, 64)
    p.setPen(_pen(lw))
    p.setBrush(QColor("#e3b98f"))
    p.drawRoundedRect(frame, 4, 4)
    art = frame.adjusted(7, 7, -7, -7)
    p.setBrush(QColor("#fde8ec"))
    p.drawRect(art)
    p.save()
    p.setClipRect(art)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#f7c59f"))
    p.drawEllipse(QPointF(art.right() - 10, art.top() + 11), 6, 6)
    p.setBrush(QColor("#a6cf92"))
    p.drawEllipse(QPointF(art.center().x(), art.bottom() + 6), art.width() * 0.75, 14)
    p.setPen(QPen(QColor("#7d5148"), 2.2, Qt.SolidLine, Qt.RoundCap))
    base = QPointF(art.center().x() - 3, art.bottom() - 6)
    p.drawLine(base, QPointF(base.x() + 2, base.y() - 20))
    p.drawLine(QPointF(base.x() + 1, base.y() - 12), QPointF(base.x() + 9, base.y() - 19))
    p.setPen(Qt.NoPen)
    for dx, dy, rad in ((2, -24, 7), (-6, -19, 5.5), (10, -20, 6)):
        p.setBrush(QColor("#f6b3c4"))
        p.drawEllipse(QPointF(base.x() + dx, base.y() + dy), rad, rad)
    p.restore()
    p.setPen(_pen(lw * 0.8))
    p.drawLine(QPointF(frame.center().x() - 12, frame.top()), QPointF(frame.center().x(), frame.top() - 8))
    p.drawLine(QPointF(frame.center().x() + 12, frame.top()), QPointF(frame.center().x(), frame.top() - 8))


def _lights(p, r, lw, scene) -> None:
    x, w, top = r.x(), r.width(), r.y() + 4
    sags = max(1, round(w / 180))
    span = w / sags
    sag = min(r.height() * 0.6, span * 0.2)
    colors = ("#f6b3c4", "#f9dc8a", "#b9dca6", "#c9c3ea")
    p.setPen(QPen(OUTLINE, lw * 0.8, Qt.SolidLine, Qt.RoundCap))
    p.setBrush(Qt.NoBrush)
    bulbs = []
    per = max(3, round(span / 30))
    for k in range(sags):
        a = QPointF(x + k * span, top)
        b = QPointF(x + (k + 1) * span, top)
        mid = QPointF((a.x() + b.x()) / 2, top + sag * 2)
        wire = QPainterPath(a)
        wire.quadTo(mid, b)
        p.drawPath(wire)
        for i in range(1, per):
            t = i / per
            bx = (1 - t) ** 2 * a.x() + 2 * (1 - t) * t * mid.x() + t ** 2 * b.x()
            by = (1 - t) ** 2 * a.y() + 2 * (1 - t) * t * mid.y() + t ** 2 * b.y()
            bulbs.append(QPointF(bx, by))
    for i, c in enumerate(bulbs):
        _glow(p, QPointF(c.x(), c.y() + 5), 13, scene.glow, colors[i % len(colors)])
    for i, c in enumerate(bulbs):
        p.setPen(_pen(lw * 0.8))
        p.setBrush(QColor(colors[i % len(colors)]))
        p.drawEllipse(QPointF(c.x(), c.y() + 5), 3.4, 4.4)


def _plank(p, r: QRectF, lw: float) -> float:
    """The shelf board and its brackets; returns the board's top."""
    y = r.bottom() - 8
    p.setPen(_pen(lw))
    p.setBrush(QColor("#d9ad83"))
    for bx in (r.x() + 12, r.right() - 18):
        bracket = QPainterPath(QPointF(bx, y + 6))
        bracket.lineTo(bx + 6, y + 6)
        bracket.lineTo(bx + 6, y + 14)
        bracket.closeSubpath()
        p.drawPath(bracket)
    p.setBrush(QColor("#e8c39b"))
    p.drawRoundedRect(QRectF(r.x(), y, r.width(), 7), 2, 2)
    return y


def _bookshelf(p, r, lw, _scene) -> None:
    y = _plank(p, r, lw)
    x = r.x() + 8
    p.setPen(_pen(lw))
    for color, w, h in (("#a9c7ee", 9, 34), ("#f6b3c4", 8, 30), ("#f9dc8a", 10, 36), ("#b9dca6", 8, 28),
                        ("#c9c3ea", 9, 33)):
        p.setBrush(QColor(color))
        p.drawRoundedRect(QRectF(x, y - h, w, h), 2, 2)
        p.drawLine(QPointF(x + 2, y - h + 6), QPointF(x + w - 2, y - h + 6))
        x += w + 1
    p.save()  # one leaning on the rest
    p.translate(x + 2, y)
    p.rotate(18)
    p.setBrush(QColor("#f7c59f"))
    p.drawRoundedRect(QRectF(0, -30, 9, 30), 2, 2)
    p.restore()
    cx = r.right() - 20
    for angle in (-28, 0, 28):
        _leaf(p, QPointF(cx, y - 14), angle, 13, 5, lw * 0.8, "#a6cf92")
    _pot(p, cx, y, 18, 14, lw * 0.9, "#fffdf8", "#f9dc8a")


def _trophies(p, r, lw, scene) -> None:
    y = _plank(p, r, lw)
    picks = top_badges(scene.badges, 3)
    spots = [r.x() + 18 + i * 26 for i in range(3)]
    for i, x in enumerate(spots):
        quest = picks[i] if i < len(picks) else quests.PERFECT
        quests.draw_badge(p, QPointF(x, y - 13), 12, quest, earned=i < len(picks))
    cx = r.right() - 22  # a little golden cup
    p.setPen(_pen(lw))
    p.setBrush(QColor("#f5d77e"))
    p.drawRoundedRect(QRectF(cx - 9, y - 4, 18, 4), 1.5, 1.5)
    p.drawRect(QRectF(cx - 2.5, y - 11, 5, 7))
    cup = QPainterPath(QPointF(cx - 11, y - 30))
    cup.lineTo(cx + 11, y - 30)
    cup.cubicTo(cx + 11, y - 16, cx + 4, y - 11, cx, y - 11)
    cup.cubicTo(cx - 4, y - 11, cx - 11, y - 16, cx - 11, y - 30)
    p.setBrush(Qt.NoBrush)
    for side in (-1, 1):
        p.drawArc(QRectF(cx + side * 11 - 5, y - 28, 10, 10), (90 if side < 0 else -90) * 16, 180 * 16)
    p.setBrush(QColor("#f5d77e"))
    p.drawPath(cup)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255, 170))
    p.drawEllipse(QPointF(cx - 5, y - 24), 2, 3.5)


def _curtain_side(p, r: QRectF, side: int, lw: float, color: str, dots: str) -> QPainterPath:
    """One drape, tied back toward its side of the window."""
    edge = r.x() + 8 if side < 0 else r.right() - 8
    inner = edge - side * r.width() * 0.2
    tie_y = r.y() + r.height() * 0.62
    drape = QPainterPath(QPointF(edge, r.y() + 8))
    drape.lineTo(inner, r.y() + 8)
    drape.cubicTo(inner, tie_y - 30, edge - side * 10, tie_y - 6, edge - side * 6, tie_y)
    drape.cubicTo(edge - side * 12, tie_y + 14, edge - side * 16, r.bottom() - 16, edge - side * 20, r.bottom())
    drape.lineTo(edge, r.bottom())
    drape.closeSubpath()
    p.setPen(_pen(lw))
    p.setBrush(QColor(color))
    p.drawPath(drape)
    p.save()
    p.setClipPath(drape)
    p.setPen(QPen(QColor(91, 44, 41, 50), lw * 0.7))
    for k in (0.3, 0.6):
        x = edge - side * r.width() * 0.2 * k
        p.drawLine(QPointF(x, r.y() + 10), QPointF(x - side * 2, r.bottom()))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(dots))
    rng = random.Random(7 + side)
    for _ in range(16):
        p.drawEllipse(QPointF(rng.uniform(r.x(), r.right()), rng.uniform(r.y(), r.bottom())), 1.6, 1.6)
    p.restore()
    p.setPen(_pen(lw * 0.9))
    p.setBrush(QColor("#fffdf8"))
    p.drawEllipse(QPointF(edge - side * 7, tie_y + 1), 4, 3)
    return drape


def _curtains_with(p, r, lw, color: str, dots: str) -> None:
    for side in (-1, 1):
        _curtain_side(p, r, side, lw, color, dots)
    p.setPen(QPen(OUTLINE, lw * 1.8, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(r.x() + 2, r.y() + 6), QPointF(r.right() - 2, r.y() + 6))
    p.setPen(_pen(lw))
    p.setBrush(QColor("#e3b98f"))
    for x in (r.x() + 2, r.right() - 2):
        p.drawEllipse(QPointF(x, r.y() + 6), 4, 4)


def _curtains(p, r, lw, _scene) -> None:
    _curtains_with(p, r, lw, "#fbd0d7", "#fffdf8")


def _star_curtains(p, r, lw, _scene) -> None:
    _curtains_with(p, r, lw, "#c9c3ea", "#f9dc8a")


def _floor_lamp(p, r, lw, scene) -> None:
    cx, bottom, top = r.center().x(), r.bottom(), r.y() + 8
    _glow(p, QPointF(cx, top + 18), 46, scene.glow)
    p.setPen(_pen(lw))
    p.setBrush(QColor("#e3b98f"))
    p.drawEllipse(QRectF(cx - 15, bottom - 7, 30, 8))
    p.setPen(QPen(OUTLINE, lw * 2.2, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(cx, bottom - 4), QPointF(cx, top + 20))
    shade = QPainterPath(QPointF(cx - 11, top))
    shade.lineTo(cx + 11, top)
    shade.lineTo(cx + 21, top + 26)
    shade.lineTo(cx - 21, top + 26)
    shade.closeSubpath()
    p.setPen(_pen(lw))
    p.setBrush(QColor("#fde8b0" if scene.glow > 0.5 else "#f9dc8a"))
    p.drawPath(shade)
    p.setPen(QPen(QColor("#f0a3b1"), lw * 1.2))
    p.drawLine(QPointF(cx - 19, top + 21), QPointF(cx + 19, top + 21))


def _fishbowl(p, r, lw, _scene) -> None:
    cx, bottom = r.center().x(), r.bottom()
    p.setPen(_pen(lw))
    p.setBrush(QColor("#d9ad83"))  # a round side table
    p.drawLine(QPointF(cx, bottom - 40), QPointF(cx, bottom - 3))
    p.drawEllipse(QRectF(cx - 12, bottom - 6, 24, 6))
    p.setBrush(QColor("#e8c39b"))
    p.drawRoundedRect(QRectF(cx - 22, bottom - 46, 44, 7), 3, 3)
    c = QPointF(cx, bottom - 66)
    bowl = QPainterPath()
    bowl.addEllipse(c, 21, 19)
    cut = QPainterPath()
    cut.addRect(QRectF(cx - 30, c.y() - 30, 60, 16))
    bowl = bowl.subtracted(cut)
    p.setBrush(QColor(201, 230, 245, 200))
    p.drawPath(bowl)
    p.save()
    p.setClipPath(bowl)
    p.setPen(QPen(QColor("#86b874"), lw * 1.4, Qt.SolidLine, Qt.RoundCap))
    weed = QPainterPath(QPointF(cx - 10, c.y() + 19))
    weed.cubicTo(cx - 15, c.y() + 8, cx - 5, c.y() + 2, cx - 10, c.y() - 6)
    p.drawPath(weed)
    p.restore()
    fish = QPointF(cx + 3, c.y() + 3)
    p.setPen(_pen(lw * 0.8))
    p.setBrush(QColor("#f7a98a"))
    tail = QPainterPath(QPointF(fish.x() + 6, fish.y()))
    tail.lineTo(fish.x() + 13, fish.y() - 5)
    tail.lineTo(fish.x() + 13, fish.y() + 5)
    tail.closeSubpath()
    p.drawPath(tail)
    p.drawEllipse(fish, 8, 5.5)
    p.setPen(Qt.NoPen)
    p.setBrush(OUTLINE)
    p.drawEllipse(QPointF(fish.x() - 4, fish.y() - 1), 1.2, 1.2)
    p.setPen(QPen(QColor(91, 44, 41, 120), lw * 0.6))
    p.setBrush(Qt.NoBrush)
    for dx, dy, rad in ((-6, -8, 1.8), (-3, -13, 1.3)):
        p.drawEllipse(QPointF(fish.x() + dx, fish.y() + dy), rad, rad)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255, 190))
    p.drawEllipse(QPointF(cx - 12, c.y() - 4), 2.5, 5)


def _cat(p, r, lw, scene) -> None:
    cx, bottom = r.center().x(), r.bottom()
    body = QRectF(cx - 22, bottom - 22, 44, 21)
    p.setPen(_pen(lw))
    p.setBrush(QColor("#fbe3c9"))
    tail = QPainterPath(QPointF(body.right() - 4, bottom - 6))
    tail.cubicTo(body.right() + 12, bottom - 6, body.right() + 8, bottom - 22, body.right() - 2, bottom - 18)
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(OUTLINE, lw * 3.6, Qt.SolidLine, Qt.RoundCap))
    p.drawPath(tail)
    p.setPen(QPen(QColor("#f7c59f"), lw * 2.0, Qt.SolidLine, Qt.RoundCap))
    p.drawPath(tail)
    p.setPen(_pen(lw))
    p.setBrush(QColor("#fbe3c9"))
    p.drawRoundedRect(body, 10, 10)
    head = QPointF(body.left() + 9, bottom - 18)
    for side in (-1, 1):
        ear = QPainterPath(QPointF(head.x() + side * 9, head.y() - 5))
        ear.lineTo(head.x() + side * 8, head.y() - 15)
        ear.lineTo(head.x() + side * 2, head.y() - 9)
        ear.closeSubpath()
        p.setBrush(QColor("#f7c59f"))
        p.drawPath(ear)
    p.setBrush(QColor("#fbe3c9"))
    p.drawEllipse(head, 11, 9.5)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#f7c59f"))  # a patch on the back
    p.drawEllipse(QPointF(body.center().x() + 8, body.top() + 6), 7, 4)
    p.setPen(QPen(OUTLINE, lw * 0.9, Qt.SolidLine, Qt.RoundCap))
    p.setBrush(Qt.NoBrush)
    for side in (-1, 1):  # closed, content eyes
        p.drawArc(QRectF(head.x() + side * 4.5 - 2.5, head.y() - 2, 5, 3.5), 200 * 16, 140 * 16)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#f0a3b1"))
    p.drawEllipse(QPointF(head.x(), head.y() + 2.4), 1.3, 1)
    if scene.glow >= 1.0:  # a little z at night
        z = QFont(FONT_FAMILY)
        z.setPixelSize(9)
        z.setWeight(QFont.DemiBold)
        p.setFont(z)
        p.setPen(QColor(91, 44, 41, 160))
        p.drawText(QPointF(head.x() + 8, head.y() - 12), "z")


_DRAW = {
    "cushion": _cushion, "beanbag": _beanbag, "cloud_sofa": _cloud_sofa,
    "round_rug": _round_rug, "heart_rug": _heart_rug,
    "succulent": _succulent, "monstera": _monstera, "bonsai": _bonsai,
    "desk": _desk, "study_desk": _study_desk,
    "clock": _clock, "print": _print,
    "lights": _lights,
    "bookshelf": _bookshelf, "trophies": _trophies,
    "curtains": _curtains, "star_curtains": _star_curtains,
    "floor_lamp": _floor_lamp, "fishbowl": _fishbowl,
    "cat": _cat,
}

SKIES = {  # part of the day -> sky top, sky bottom
    "morning": ("#cfe8f6", "#fdeed6"),
    "day": ("#b9dcf3", "#e8f5fb"),
    "evening": ("#c9b5e3", "#f9c8a8"),
    "night": ("#2f3763", "#565c92"),
}


def _paint_window(p: QPainter, r: QRectF, lw: float, scene: Scene) -> None:
    part = part_of_day(scene.now.hour)
    top, bottom = SKIES[part]
    pane = r.adjusted(6, 6, -6, -6)
    p.setPen(_pen(lw))
    p.setBrush(QColor("#fffdf8"))
    p.drawRoundedRect(r, 8, 8)
    sky = QLinearGradient(pane.topLeft(), pane.bottomLeft())
    sky.setColorAt(0, QColor(top))
    sky.setColorAt(1, QColor(bottom))
    p.setBrush(sky)
    p.drawRoundedRect(pane, 5, 5)
    p.save()
    p.setClipRect(pane)
    p.setPen(Qt.NoPen)
    if part == "night":
        p.setBrush(QColor("#fff4c9"))
        moon = QPainterPath()
        moon.addEllipse(QPointF(pane.right() - 26, pane.top() + 22), 11, 11)
        bite = QPainterPath()
        bite.addEllipse(QPointF(pane.right() - 20, pane.top() + 18), 10, 10)
        p.drawPath(moon.subtracted(bite))
        rng = random.Random(5)
        for _ in range(9):
            p.setBrush(QColor(255, 244, 201, rng.randint(150, 240)))
            p.drawEllipse(QPointF(rng.uniform(pane.left() + 6, pane.right() - 40), rng.uniform(pane.top() + 6,
                                  pane.bottom() - 26)), 1.3, 1.3)
    else:
        sun_y = {"morning": pane.bottom() - 26, "day": pane.top() + 22, "evening": pane.bottom() - 18}[part]
        sun_x = {"morning": pane.left() + 26, "day": pane.right() - 28, "evening": pane.right() - 30}[part]
        p.setBrush(QColor("#ffe7a3" if part != "evening" else "#f9b48f"))
        p.drawEllipse(QPointF(sun_x, sun_y), 11, 11)
        p.setBrush(QColor(255, 255, 255, 220))
        for dx, dy, rad in ((0, 0, 9), (10, -4, 11), (21, 0, 8)):  # a cloud
            p.drawEllipse(QPointF(pane.left() + 22 + dx, pane.top() + 30 + dy), rad, rad * 0.8)
    hills = QColor("#a6cf92" if part != "night" else "#4c6b55")
    p.setBrush(hills)
    p.drawEllipse(QPointF(pane.left() + pane.width() * 0.25, pane.bottom() + 6), pane.width() * 0.45, 20)
    hills = hills.darker(110)
    p.setBrush(hills)
    p.drawEllipse(QPointF(pane.right() - pane.width() * 0.15, pane.bottom() + 10), pane.width() * 0.45, 22)
    p.restore()
    p.setPen(_pen(lw))
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(pane, 5, 5)
    p.setPen(QPen(OUTLINE, lw * 1.4))
    p.drawLine(QPointF(pane.center().x(), pane.top()), QPointF(pane.center().x(), pane.bottom()))
    p.drawLine(QPointF(pane.left(), pane.center().y()), QPointF(pane.right(), pane.center().y()))
    p.setPen(_pen(lw))
    p.setBrush(QColor("#fffdf8"))  # the sill
    p.drawRoundedRect(QRectF(r.x() - 6, r.bottom() - 3, r.width() + 12, 7), 3, 3)


def thumbnail(p: QPainter, key: str, rect: QRectF, scene: Scene, line_px: float = 1.1) -> None:
    """An item on its own, fitted into rect (for lists and menus)."""
    box = SLOTS[ITEMS[key].slot][1]
    aspect = max(0.45, min(2.4, box.width() / box.height()))
    w = min(rect.width(), rect.height() * aspect)
    h = w / aspect
    target = QRectF(rect.center().x() - w / 2, rect.center().y() - h / 2, w, h)
    logical = QRectF(0, 0, box.height() * aspect, box.height())
    f = target.width() / logical.width()
    p.save()
    p.translate(target.topLeft())
    p.scale(f, f)
    draw_item(p, key, logical, line_px / f, scene)
    p.restore()


_THUMBS: dict[tuple, QPixmap] = {}


def thumbnail_pixmap(key: str, w: int, h: int, ratio: float) -> QPixmap:
    """An item's thumbnail, drawn once and kept: lists repaint often (scrolling, hovering)."""
    cache_key = (key, w, h, ratio)
    pixmap = _THUMBS.get(cache_key)
    if pixmap is None:
        pixmap = QPixmap(max(1, round(w * ratio)), max(1, round(h * ratio)))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(Qt.transparent)
        p = QPainter(pixmap)
        p.setRenderHint(QPainter.Antialiasing)
        thumbnail(p, key, QRectF(0, 0, w, h), Scene(datetime(2026, 1, 1, 12), {}))
        p.end()
        _THUMBS[cache_key] = pixmap
    return pixmap


class RoomWidget(QWidget):
    """The room itself: click Pomo to poke him, click any spot to choose what goes there."""

    picked = Signal(str, str)  # slot, item key ("" to clear it)
    FPS = 30

    def __init__(self, theme, parent: QWidget | None = None):
        super().__init__(parent)
        self.theme = theme
        self.placed: dict[str, str] = {}
        self.have: list[str] = []
        self.badges: dict[str, int] = {}
        self.pomo = Pomo(random.Random())
        self.now: datetime | None = None  # fixed for screenshots and tests; None = the clock
        self._hover: str | None = None
        self._back: QPixmap | None = None    # see _layers
        self._front: QPixmap | None = None
        self._layers_for: tuple = ()
        self._clock = QTimer(self)
        self._clock.setInterval(1000 // self.FPS)
        self._clock.timeout.connect(self._tick)
        self.setMinimumHeight(250)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_OpaquePaintEvent)  # the room covers every pixel: nothing behind it to repaint

    def sizeHint(self) -> QSize:
        return QSize(560, 260)

    def set_data(self, placed: dict[str, str], have: list[str], badges: dict[str, int], outfit: str) -> None:
        self.placed, self.have, self.badges = dict(placed), list(have), dict(badges)
        self.pomo.outfit = outfit
        self.update()

    def cheer(self) -> None:
        self.pomo.hop(1.8)

    # ------------------------------------------------------------ animation
    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._clock.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._clock.stop()

    def _tick(self) -> None:
        if self.window().isMinimized() or self.visibleRegion().isEmpty():
            return  # nobody's looking: no need to animate
        hour = self._now().hour
        self.pomo.set_mood("sleepy" if hour >= 23 or hour < 6 else "idle")
        cursor = None
        pos = self.mapFromGlobal(QCursor.pos())
        if self.rect().contains(pos):
            c = self._to_stage(QPointF(pos))
            anchor = self._pomo_anchor()
            cursor = ((c.x() - anchor.x()) / POMO_SIZE, (c.y() - anchor.y()) / POMO_SIZE)
        self.pomo.update(1 / self.FPS, cursor)
        if self._layers_key() != self._layers_for:
            self.update()  # a new minute on the clock, or a new part of the day
        else:
            self.update(self._pomo_rect())  # only Pomo moves

    def _now(self) -> datetime:
        return self.now or datetime.now()

    # ------------------------------------------------------------- geometry
    def _transform(self) -> tuple[float, float, float]:
        k = min(self.width() / STAGE_W, self.height() / STAGE_H)
        return k, (self.width() - STAGE_W * k) / 2, (self.height() - STAGE_H * k) / 2

    def _to_stage(self, point: QPointF) -> QPointF:
        k, ox, oy = self._transform()
        return QPointF((point.x() - ox) / k, (point.y() - oy) / k)

    def _pomo_anchor(self) -> QPointF:
        seat = self.placed.get("seat")
        box = SLOTS["seat"][1]
        sit_y = box.bottom() - SIT[seat] if seat in SIT else FLOOR_Y + 26
        return QPointF(POMO_X, sit_y - POMO_SIZE * 0.40)

    def slot_at(self, point: QPointF) -> str | None:
        """The spot under a point in the widget ('pomo' for Pomo himself), front-most first."""
        c = self._to_stage(point)
        anchor = self._pomo_anchor()
        if math.hypot((c.x() - anchor.x()) / 0.5, (c.y() - anchor.y()) / 0.42) < POMO_SIZE:
            return "pomo"
        for slot in reversed(list(SLOTS)):
            if SLOTS[slot][1].contains(c):
                return slot
        if WINDOW.contains(c):
            return "curtains"
        return None

    # -------------------------------------------------------------- painting
    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.paint_room(p)
        p.end()

    def paint_room(self, p: QPainter) -> None:
        """The cached room, Pomo (the only thing that moves), and the evening light over both."""
        back, front = self._layers()
        p.drawPixmap(0, 0, back)
        k, ox, oy = self._transform()
        p.save()
        p.setClipPath(self._clip())
        p.translate(ox, oy)
        p.scale(k, k)
        self.pomo.paint(p, self._pomo_anchor(), POMO_SIZE)
        if self._hover and self._hover != "pomo":
            box = WINDOW if self._hover == "curtains" and "curtains" not in self.placed else SLOTS[self._hover][1]
            p.setPen(QPen(QColor(self.theme.chart), 1.4, Qt.DashLine, Qt.RoundCap))
            p.setBrush(QColor(255, 255, 255, 40))
            p.drawRoundedRect(box.adjusted(-3, -3, 3, 3), 8, 8)
        p.restore()
        if front is not None:
            p.drawPixmap(0, 0, front)

    def _clip(self) -> QPainterPath:
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, self.width(), self.height()), 14, 14)
        return clip

    def _layers_key(self) -> tuple:
        return (self.width(), self.height(), self.devicePixelRatioF(), self.theme.key,
                tuple(sorted(self.placed.items())), tuple(sorted(self.badges.items())),
                self._now().strftime("%Y%m%d%H%M"))

    def _layers(self) -> tuple[QPixmap, QPixmap | None]:
        """The room behind Pomo, and the evening light in front of him. Drawing them takes a while, so
        they're drawn once and reused every frame until something in them changes (an item, the size,
        the minute on the clock)."""
        key = self._layers_key()
        if key != self._layers_for or self._back is None:
            self._layers_for = key
            scene = Scene(self._now(), self.badges)
            self._back = self._new_layer(QColor(self.theme.surface))  # the card's color, for the corners
            p = QPainter(self._back)
            p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
            self._paint_back(p, scene)
            p.end()
            self._front = None
            if scene.glow:
                self._front = self._new_layer(Qt.transparent)
                p = QPainter(self._front)
                p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
                self._paint_front(p, scene)
                p.end()
        return self._back, self._front

    def _new_layer(self, fill) -> QPixmap:
        ratio = self.devicePixelRatioF()
        layer = QPixmap(max(1, round(self.width() * ratio)), max(1, round(self.height() * ratio)))
        layer.setDevicePixelRatio(ratio)
        layer.fill(fill)
        return layer

    def _pomo_rect(self):
        """Where Pomo can be drawn (hops, hats and all), in the widget: the only part that changes."""
        k, ox, oy = self._transform()
        anchor = self._pomo_anchor()
        r = QRectF(anchor.x() - POMO_SIZE, anchor.y() - 2.2 * POMO_SIZE, 2 * POMO_SIZE, 3.1 * POMO_SIZE)
        return QRectF(ox + r.x() * k, oy + r.y() * k, r.width() * k, r.height() * k).toAlignedRect().adjusted(
            -2, -2, 2, 2)

    def _paint_back(self, p: QPainter, scene: Scene) -> None:
        k, ox, oy = self._transform()
        w, h = self.width(), self.height()
        dark = self.theme.dark
        p.save()
        p.setClipPath(self._clip())
        floor_top = oy + FLOOR_Y * k
        p.fillRect(QRectF(0, 0, w, floor_top), QColor("#433a4a" if dark else "#f8ebdc"))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 16 if dark else 90))  # polka-dot wallpaper
        step = 26 * k
        row = 0
        y = 12 * k
        while y < floor_top - 8 * k:
            x = (step / 2 if row % 2 else 0) + 10 * k
            while x < w:
                p.drawEllipse(QPointF(x, y), 2.2 * k, 2.2 * k)
                x += step
            y += step * 0.8
            row += 1
        p.fillRect(QRectF(0, floor_top, w, h - floor_top), QColor("#5a4842" if dark else "#e6c7a3"))
        p.setPen(QPen(QColor(0, 0, 0, 30 if dark else 22), max(1.0, k)))
        for i in range(1, 4):  # floorboards
            fy = floor_top + (h - floor_top) * i / 4
            p.drawLine(QPointF(0, fy), QPointF(w, fy))
        p.fillRect(QRectF(0, floor_top - 5 * k, w, 6 * k), QColor("#5f4f5c" if dark else "#fffdf8"))

        p.translate(ox, oy)
        p.scale(k, k)
        lw = 1.5
        _paint_window(p, WINDOW, lw, scene)
        for slot, (_label, box) in SLOTS.items():
            if slot == "seat":
                break
            key = self.placed.get(slot)
            if key:
                draw_item(p, key, box, lw, scene)
        seat = self.placed.get("seat")
        if seat:
            draw_item(p, seat, SLOTS["seat"][1], lw, scene)
        p.restore()

    def _paint_front(self, p: QPainter, scene: Scene) -> None:
        """Dusk: the room dims a little (Pomo too) and the lamps take over."""
        k, ox, oy = self._transform()
        p.setClipPath(self._clip())
        p.fillRect(QRectF(0, 0, self.width(), self.height()), QColor(40, 34, 90, int(46 * scene.glow)))
        p.translate(ox, oy)
        p.scale(k, k)
        for slot in ("lights", "corner", "desk"):
            key = self.placed.get(slot)
            if key in ("lights", "floor_lamp", "study_desk"):
                self._relight(p, key, scene)

    def _relight(self, p: QPainter, key: str, scene: Scene) -> None:
        """Lamps stay bright when the room dims: their glow goes on top."""
        if key == "floor_lamp":
            box = SLOTS["corner"][1]
            _glow(p, QPointF(box.center().x(), box.y() + 26), 50, scene.glow)
        elif key == "study_desk":
            box = SLOTS["desk"][1]
            _glow(p, QPointF(box.right() - 14, box.bottom() - 74), 28, scene.glow)
        else:
            draw_item(p, "lights", SLOTS["lights"][1], 1.5, scene)

    # ------------------------------------------------------------- the mouse
    def mouseMoveEvent(self, event) -> None:
        slot = self.slot_at(event.position())
        if slot != self._hover:
            self._hover = slot
            self.setCursor(Qt.PointingHandCursor if slot else Qt.ArrowCursor)
            self.update()
        if slot and slot != "pomo":
            key = self.placed.get(slot)
            text = (f"{SLOTS[slot][0]}: {ITEMS[key].name}\nClick to swap it" if key
                    else f"{SLOTS[slot][0]}: empty for now\nClick to see what goes here")
            QToolTip.showText(event.globalPosition().toPoint(), text, self)
        else:
            QToolTip.hideText()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self._hover = None
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        slot = self.slot_at(event.position())
        if slot == "pomo":
            self.pomo.poke()
        elif slot:
            self._choose(slot, event.globalPosition().toPoint())

    def _choose(self, slot: str, at) -> None:
        from .app import styled_menu

        menu = styled_menu(QMenu(self))
        current = self.placed.get(slot, "")
        head = menu.addAction(SLOTS[slot][0])
        head.setEnabled(False)
        menu.addSeparator()
        for key, item in ITEMS.items():
            if item.slot != slot:
                continue
            if key in self.have:
                act = menu.addAction(item.name)
                act.setCheckable(True)
                act.setChecked(key == current)
                act.triggered.connect(lambda _=False, k=key: self.picked.emit(slot, k))
            else:
                act = menu.addAction(f"{item.name}  ({hint(item)})")
                act.setEnabled(False)
        empty = menu.addAction("Leave it empty")
        empty.setCheckable(True)
        empty.setChecked(not current)
        empty.triggered.connect(lambda: self.picked.emit(slot, ""))
        menu.exec(at)


class RoomShelf(QWidget):
    """Every item: the ones you've earned in color (outlined if they're in the room), the rest faded.
    Click one you've earned to put it in the room."""

    picked = Signal(str, str)  # slot, item key
    CELL = 52

    def __init__(self, theme, parent: QWidget | None = None):
        super().__init__(parent)
        self.theme = theme
        self.have: list[str] = []
        self.placed: dict[str, str] = {}
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def set_items(self, have: list[str], placed: dict[str, str]) -> None:
        self.have, self.placed = list(have), dict(placed)
        self._fit()
        self.update()

    def _columns(self) -> int:
        return max(1, self.width() // self.CELL)

    def _fit(self) -> None:
        self.setFixedHeight(math.ceil(len(ITEMS) / self._columns()) * self.CELL)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit()

    def _cell(self, index: int) -> QRectF:
        cols = self._columns()
        return QRectF((index % cols) * self.CELL, (index // cols) * self.CELL, self.CELL - 6, self.CELL - 6)

    def _at(self, point: QPointF) -> str | None:
        for i, key in enumerate(ITEMS):
            if self._cell(i).contains(point):
                return key
        return None

    def paintEvent(self, _event) -> None:
        t = self.theme
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        in_room = set(self.placed.values())
        ratio = self.devicePixelRatioF()
        for i, key in enumerate(ITEMS):
            cell = self._cell(i)
            p.setPen(QPen(QColor(t.chart), 2) if key in in_room else Qt.NoPen)
            p.setBrush(QColor(t.raised))
            p.drawRoundedRect(cell.adjusted(1, 1, -1, -1), 10, 10)
            p.setOpacity(1.0 if key in self.have else 0.28)
            inner = cell.adjusted(6, 6, -6, -6)
            p.drawPixmap(inner.topLeft(), thumbnail_pixmap(key, round(inner.width()), round(inner.height()), ratio))
            p.setOpacity(1.0)
        p.end()

    def mouseMoveEvent(self, event) -> None:
        key = self._at(event.position())
        self.setCursor(Qt.PointingHandCursor if key in self.have and key not in self.placed.values()
                       else Qt.ArrowCursor)
        if key is None:
            QToolTip.hideText()
            return
        item = ITEMS[key]
        if key in self.placed.values():
            state = "In the room"
        elif key in self.have:
            state = "Earned. Click to put it in the room"
        else:
            state = f"Earned at {hint(item)}"
        note = f"\n{item.note}" if item.note else ""
        QToolTip.showText(event.globalPosition().toPoint(), f"{item.name}{note}\n{state}", self)

    def mousePressEvent(self, event) -> None:
        key = self._at(event.position())
        if event.button() == Qt.LeftButton and key in self.have and key not in self.placed.values():
            self.picked.emit(ITEMS[key].slot, key)


class RoomGoals(QWidget):
    """What's next for the room: each item to earn, with a progress bar."""

    ROW = 56

    def __init__(self, theme, parent: QWidget | None = None):
        super().__init__(parent)
        self.theme = theme
        self.rows: list[tuple[Item, float]] = []
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumHeight(self.ROW)

    def set_rows(self, rows: list[tuple[Item, float]]) -> None:
        self.rows = rows
        self.setFixedHeight(max(self.ROW, self.ROW * len(rows)))
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(320, self.ROW * max(1, len(self.rows)))

    def paintEvent(self, _event) -> None:
        t = self.theme
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        w = self.width()
        if not self.rows:
            p.setPen(QColor(t.ink_2))
            p.drawText(self.rect(), Qt.AlignCenter, "Pomo's room is complete. Every item earned!")
            p.end()
            return
        ratio = self.devicePixelRatioF()
        title = QFont(FONT_FAMILY)
        title.setPixelSize(13)
        title.setWeight(QFont.DemiBold)
        body = QFont(FONT_FAMILY)
        body.setPixelSize(12)
        for i, (item, have) in enumerate(self.rows):
            top = i * self.ROW
            tile = QRectF(0, top + 6, 44, 44)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(t.raised))
            p.drawRoundedRect(tile, 10, 10)
            p.drawPixmap(tile.adjusted(4, 4, -4, -4).topLeft(), thumbnail_pixmap(item.key, 36, 36, ratio))
            p.setFont(title)
            p.setPen(QColor(t.ink))
            p.drawText(QRectF(56, top + 8, w - 140, 18), Qt.AlignLeft | Qt.AlignVCenter, item.name)
            p.setFont(body)
            p.setPen(QColor(t.ink_2))
            p.drawText(QRectF(56, top + 27, w - 140, 18), Qt.AlignLeft | Qt.AlignVCenter,
                       QFontMetricsF(body).elidedText(f"Earned at {hint(item)}", Qt.ElideRight, w - 140))
            bar = QRectF(w - 74, top + self.ROW / 2 - 4, 66, 6)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(t.grid))
            p.drawRoundedRect(bar, 3, 3)
            share = min(1.0, have / item.at)
            if share > 0:
                p.setBrush(QColor(t.chart))
                p.drawRoundedRect(QRectF(bar.left(), bar.top(), max(6.0, bar.width() * share), bar.height()), 3, 3)
            p.setPen(QColor(t.muted))
            shown = f"{have:.1f}" if item.kind == "hours" and have < 10 else f"{int(have)}"
            p.drawText(QRectF(w - 84, top + self.ROW / 2 + 4, 76, 16), Qt.AlignRight | Qt.AlignTop,
                       f"{shown}/{item.at}")
        p.end()
