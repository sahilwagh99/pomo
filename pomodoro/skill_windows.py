"""The skill tree and the big picture: your path drawn like a game's skill tree, and as a map of how it fits."""
from __future__ import annotations

import html
import math
import random
import time
from datetime import date

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import skills, studypath
from .charts import Meter
from .pomo import BODY_RY, Pomo
from .stats import plural
from .winutil import set_dark_title_bar

BURST_S = 1.6


def _label(text: str = "", role: str | None = None, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    if role:
        label.setProperty("role", role)
    label.setWordWrap(wrap)
    return label


def _alpha(color: str | QColor, alpha: int) -> QColor:
    c = QColor(color)
    c.setAlpha(alpha)
    return c


def _mix(a: str | QColor, b: str | QColor, t: float) -> QColor:
    a, b = QColor(a), QColor(b)
    return QColor(round(a.red() + (b.red() - a.red()) * t), round(a.green() + (b.green() - a.green()) * t),
                  round(a.blue() + (b.blue() - a.blue()) * t))


def _font(base: QFont, px: int, bold: bool = False) -> QFont:
    f = QFont(base)
    f.setPixelSize(px)
    f.setBold(bold)
    return f


def _check(p: QPainter, c: QPointF, r: float, color: QColor) -> None:
    path = QPainterPath(QPointF(c.x() - r * 0.42, c.y() + r * 0.02))
    path.lineTo(c.x() - r * 0.1, c.y() + r * 0.34)
    path.lineTo(c.x() + r * 0.46, c.y() - r * 0.3)
    p.setPen(QPen(color, max(2.0, r * 0.16), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)


def _lock(p: QPainter, c: QPointF, r: float, color: QColor) -> None:
    p.setPen(QPen(color, max(1.6, r * 0.1), Qt.SolidLine, Qt.RoundCap))
    p.setBrush(Qt.NoBrush)
    p.drawArc(QRectF(c.x() - r * 0.24, c.y() - r * 0.46, r * 0.48, r * 0.5), 0, 180 * 16)
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawRoundedRect(QRectF(c.x() - r * 0.36, c.y() - r * 0.2, r * 0.72, r * 0.56), r * 0.12, r * 0.12)


def _star(c: QPointF, r: float) -> QPainterPath:
    path = QPainterPath()
    for i in range(10):
        a = -math.pi / 2 + i * math.pi / 5
        rr = r if i % 2 == 0 else r * 0.45
        pt = QPointF(c.x() + rr * math.cos(a), c.y() + rr * math.sin(a))
        if i == 0:
            path.moveTo(pt)
        else:
            path.lineTo(pt)
    path.closeSubpath()
    return path


class _Animated(QWidget):
    """A canvas with a gently moving Pomo (and bursts), ticking only while it's on screen."""

    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        self.pomo = Pomo(random.Random(5))
        self.t = 0.0
        self._last = time.monotonic()
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        self.setMouseTracking(True)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._last = time.monotonic()
        self._timer.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._timer.stop()

    def _tick(self) -> None:
        now = time.monotonic()
        dt, self._last = min(0.1, now - self._last), now
        self.t += dt
        self.pomo.update(dt)
        self.update()


# ------------------------------------------------------------------ the skill tree
class TreeCanvas(_Animated):
    """Your path climbing from the first step (bottom) to your goal (top): done steps lit, today's glowing."""

    picked = Signal(int, int)

    W = 560
    R = 24
    STEP_GAP = 96
    BANNER_GAP = 90
    TOP = 150
    BOTTOM = 80
    SWAY = 118

    def __init__(self, ctrl):
        super().__init__(ctrl)
        self.nodes: list[dict] = []
        self.banners: list[dict] = []
        self.route: list[tuple[QPointF, str]] = []  # the line through the path, and how far it's lit
        self.goal = QPointF()
        self.goal_text = ""
        self.all_done = False
        self.selected: tuple[int, int] | None = None
        self.hover: tuple[int, int] | None = None
        self.bursts: dict[tuple[int, int], float] = {}
        self.setFixedSize(self.W, 400)

    def load(self, path: dict) -> None:
        self.nodes, self.banners, self.route = [], [], []
        nxt = studypath.next_step(path)
        today = (nxt[0], nxt[1]) if nxt else None
        items = []
        for mi, m in enumerate(path["milestones"]):
            items.append(("banner", mi, None, m))
            items += [("step", mi, si, st) for si, st in enumerate(m["steps"])]
        height = self.TOP + self.BOTTOM + sum(self.BANNER_GAP if kind == "banner" else self.STEP_GAP
                                              for kind, *_rest in items)
        self.setFixedSize(self.W, max(420, height))
        height = self.height()
        cx = self.W / 2
        y = height - self.BOTTOM
        self.route.append((QPointF(cx, y + 34), "lit"))
        k = 0
        done_before = True  # every step so far is done (so the next milestone's banner is reached)
        for kind, mi, si, item in items:
            gap = self.BANNER_GAP if kind == "banner" else self.STEP_GAP
            centre_y = y - gap / 2
            y -= gap
            if kind == "banner":
                d, t = studypath.milestone_progress(item)
                rect = QRectF(cx - 150, centre_y - 21, 300, 42)
                self.banners.append({"mi": mi, "rect": rect, "title": item["title"], "done": d, "total": t})
                self.route.append((rect.center(), "lit" if done_before else "locked"))
                continue
            pos = QPointF(cx + self.SWAY * math.sin(k * 1.05 + 0.4), centre_y)
            k += 1
            state = "done" if item["done"] else "today" if (mi, si) == today else "locked"
            self.nodes.append({"mi": mi, "si": si, "step": item, "pos": pos, "state": state})
            self.route.append((pos, "lit" if state == "done" else "active" if state == "today" else "locked"))
            done_before = done_before and item["done"]
        self.goal = QPointF(cx, self.TOP - 52)
        self.goal_text = path["goal"]
        self.all_done = bool(self.nodes) and all(n["state"] == "done" for n in self.nodes)
        self.route.append((self.goal, "lit" if self.all_done else "locked"))
        if self.selected not in {(n["mi"], n["si"]) for n in self.nodes}:
            self.selected = today or ((self.nodes[-1]["mi"], self.nodes[-1]["si"]) if self.nodes else None)
        self.update()

    def node(self, key: tuple[int, int] | None) -> dict | None:
        return next((n for n in self.nodes if (n["mi"], n["si"]) == key), None)

    def burst(self, mi: int, si: int, delay: float = 0.0) -> None:
        self.bursts[(mi, si)] = time.monotonic() + delay

    def _at(self, pos: QPointF) -> tuple[int, int] | None:
        for n in self.nodes:
            if math.hypot(pos.x() - n["pos"].x(), pos.y() - n["pos"].y()) <= self.R + 8:
                return n["mi"], n["si"]
        return None

    def mouseMoveEvent(self, event) -> None:
        hover = self._at(event.position())
        if hover != self.hover:
            self.hover = hover
            self.setCursor(Qt.PointingHandCursor if hover else Qt.ArrowCursor)

    def mousePressEvent(self, event) -> None:
        key = self._at(event.position())
        if key is not None and event.button() == Qt.LeftButton:
            self.selected = key
            self.picked.emit(*key)

    # ------------------------------------------------------------- drawing
    def paintEvent(self, _event) -> None:
        th = self.ctrl.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.fillRect(self.rect(), QColor(th.page))
        self._paint_dots(p, th)
        self._paint_route(p, th)
        self._paint_goal(p, th)
        for b in self.banners:
            self._paint_banner(p, th, b)
        now = time.monotonic()
        for n in self.nodes:
            self._paint_node(p, th, n, now)
        today = next((n for n in self.nodes if n["state"] == "today"), None)
        if today is not None:  # Pomo stands beside today's step, on the side away from its words
            outside = 1 if today["pos"].x() > self.W / 2 else -1
            size = 46
            ground = today["pos"].y() + self.R
            self.pomo.paint(p, QPointF(today["pos"].x() + outside * (self.R + 48), ground - size * BODY_RY), size)
        for key in [k for k, start in self.bursts.items() if now - start > BURST_S]:
            del self.bursts[key]
        p.end()

    def _paint_dots(self, p: QPainter, th) -> None:
        p.setPen(Qt.NoPen)
        p.setBrush(_alpha(th.axis, 90))
        for y in range(24, self.height(), 32):
            for x in range(24 + (y // 32 % 2) * 16, self.W, 32):
                p.drawEllipse(QPointF(x, y), 1.3, 1.3)

    def _paint_route(self, p: QPainter, th) -> None:
        for (a, _sa), (b, sb) in zip(self.route, self.route[1:]):
            path = QPainterPath(a)
            mid = (a.y() + b.y()) / 2
            path.cubicTo(QPointF(a.x(), mid), QPointF(b.x(), mid), b)
            p.setBrush(Qt.NoBrush)
            if sb == "lit":
                p.setPen(QPen(_alpha(th.chart, 70), 16, Qt.SolidLine, Qt.RoundCap))
                p.drawPath(path)
                p.setPen(QPen(QColor(th.chart), 7, Qt.SolidLine, Qt.RoundCap))
            elif sb == "active":
                pen = QPen(QColor(th.focus), 7, Qt.DashLine, Qt.RoundCap)
                pen.setDashOffset(-self.t * 6)
                p.setPen(pen)
            else:
                p.setPen(QPen(QColor(th.axis), 5, Qt.DotLine, Qt.RoundCap))
            p.drawPath(path)

    def _paint_goal(self, p: QPainter, th) -> None:
        c, r = self.goal, self.R * 1.5
        glow = 0.5 + 0.5 * math.sin(self.t * 2.0)
        p.setPen(Qt.NoPen)
        if self.all_done:
            p.setBrush(_alpha(th.focus, int(50 + 50 * glow)))
            p.drawEllipse(c, r + 12, r + 12)
        p.setBrush(QColor(th.surface))
        p.setPen(QPen(QColor(th.chart) if self.all_done else QColor(th.axis), 3))
        p.drawEllipse(c, r, r)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(th.chart) if self.all_done else _alpha(th.muted, 160))
        p.drawPath(_star(c, r * 0.58))
        p.setPen(QColor(th.muted))
        p.setFont(_font(self.font(), 11, bold=True))
        p.drawText(QRectF(c.x() - 200, c.y() - r - 46, 400, 16), Qt.AlignHCenter, "YOUR GOAL")
        p.setPen(QColor(th.ink))
        p.setFont(_font(self.font(), 15, bold=True))
        p.drawText(QRectF(c.x() - 220, c.y() - r - 30, 440, 24), Qt.AlignHCenter,
                   QFontMetricsF(p.font()).elidedText(self.goal_text, Qt.ElideRight, 430))

    def _paint_banner(self, p: QPainter, th, b: dict) -> None:
        rect, complete = b["rect"], b["total"] > 0 and b["done"] == b["total"]
        p.setPen(QPen(QColor(th.chart) if complete else th.border, 1.5))
        p.setBrush(_mix(th.surface, th.focus, 0.28) if complete else QColor(th.surface))
        p.drawRoundedRect(rect, 21, 21)
        p.setFont(_font(self.font(), 13, bold=True))
        p.setPen(QColor(th.ink))
        count = f"{b['done']}/{b['total']}"
        text_rect = rect.adjusted(40 if complete else 18, 0, -58, 0)
        p.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft,
                   QFontMetricsF(p.font()).elidedText(b["title"], Qt.ElideRight, text_rect.width()))
        p.setPen(QColor(th.muted))
        p.drawText(rect.adjusted(0, 0, -18, 0), Qt.AlignVCenter | Qt.AlignRight, count)
        if complete:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(th.chart))
            p.drawPath(_star(QPointF(rect.left() + 22, rect.center().y()), 9))

    def _paint_node(self, p: QPainter, th, n: dict, now: float) -> None:
        c, r, state = n["pos"], self.R, n["state"]
        key = (n["mi"], n["si"])
        age = now - self.bursts[key] if key in self.bursts else -1.0
        scale = 1.0
        if 0 <= age < BURST_S:
            scale = 1 + 0.28 * math.sin(min(1.0, age / 0.45) * math.pi)
        elif key == self.hover:
            scale = 1.08
        rr = r * scale
        p.setPen(Qt.NoPen)
        if state == "today":
            pulse = 0.5 + 0.5 * math.sin(self.t * 3.0)
            p.setBrush(_alpha(th.focus, int(60 + 70 * pulse)))
            p.drawEllipse(c, rr + 9 + 4 * pulse, rr + 9 + 4 * pulse)
        elif state == "done":
            p.setBrush(_alpha(th.chart, 50))
            p.drawEllipse(c, rr + 7, rr + 7)
        if key == self.selected:
            p.setPen(QPen(_alpha(th.ink, 150), 2, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(c, rr + 15, rr + 15)
        fill = {"done": QColor(th.chart), "today": QColor(th.focus), "locked": QColor(th.raised)}[state]
        edge = {"done": _mix(th.chart, th.ink, 0.25), "today": QColor(th.chart), "locked": QColor(th.axis)}[state]
        p.setPen(QPen(edge, 3 if state != "locked" else 2))
        p.setBrush(fill)
        p.drawEllipse(c, rr, rr)
        if state == "done":
            _check(p, c, rr, QColor("#ffffff"))
        elif state == "today":
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(th.on_accent))
            p.drawPath(_star(c, rr * 0.5))
        else:
            _lock(p, c, rr, QColor(th.muted))
        if 0 <= age < BURST_S:
            self._paint_burst(p, th, c, age)
        self._paint_label(p, th, n)

    def _paint_burst(self, p: QPainter, th, c: QPointF, age: float) -> None:
        f = age / BURST_S
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(_alpha(th.short, int(220 * (1 - f))), 3))
        p.drawEllipse(c, self.R + 70 * f, self.R + 70 * f)
        p.setPen(Qt.NoPen)
        colors = (th.short, th.focus, th.long, th.chart)
        for i in range(14):
            a = i * math.tau / 14 + 0.3
            dist = self.R + 8 + 78 * (1 - (1 - f) ** 2)
            pt = QPointF(c.x() + dist * math.cos(a), c.y() + dist * math.sin(a) + 18 * f * f)
            p.setBrush(_alpha(colors[i % 4], int(255 * (1 - f))))
            p.save()
            p.translate(pt)
            p.rotate(math.degrees(a) + age * 220)
            p.drawEllipse(QPointF(0, 0), 6 * (1 - f * 0.5), 3.2 * (1 - f * 0.5))
            p.restore()

    def _paint_label(self, p: QPainter, th, n: dict) -> None:
        c, st, state = n["pos"], n["step"], n["state"]
        left = c.x() > self.W / 2  # the words go toward the middle (Pomo stands on the outside)
        width = 178
        x = c.x() - self.R - 16 - width if left else c.x() + self.R + 16
        align = (Qt.AlignRight if left else Qt.AlignLeft) | Qt.TextWordWrap
        title_font = _font(self.font(), 13, bold=state == "today")
        fm = QFontMetricsF(title_font)
        title = st["title"]
        lines = fm.boundingRect(QRectF(0, 0, width, 200), align, title).height()
        if lines > fm.lineSpacing() * 2.2:  # at most two lines
            title = fm.elidedText(title, Qt.ElideRight, width * 1.85)
        box = fm.boundingRect(QRectF(0, 0, width, 200), align, title)
        top = c.y() - (box.height() + 16) / 2
        p.setFont(title_font)
        p.setPen(QColor(th.ink) if state != "locked" else QColor(th.ink_2))
        p.drawText(QRectF(x, top, width, box.height() + 2), align, title)
        n_pom = st["pomodoros"]
        note = ("today's step" if state == "today" else "done" if state == "done" else "to unlock")
        note += f"  ·  {min(st['spent'], n_pom)}/{n_pom} pomodoros" if state != "done" else ""
        p.setFont(_font(self.font(), 11))
        p.setPen(QColor(th.chart) if state == "today" else QColor(th.muted))
        p.drawText(QRectF(x, top + box.height() + 1, width, 16), (Qt.AlignRight if left else Qt.AlignLeft), note)


# ------------------------------------------------------------------ the big picture
class MapCanvas(_Animated):
    """Milestones as islands along a road, steps inside them, and lines for how steps connect."""

    picked = Signal(str)

    COLS = 3
    ISLAND_W = 220
    GAP_X = 42
    GAP_Y = 70
    MARGIN = 40
    HEAD = 52
    CHIP_H = 30
    CHIP_GAP = 6

    def __init__(self, ctrl):
        super().__init__(ctrl)
        self.islands: list[dict] = []
        self.chips: dict[str, dict] = {}
        self.links: list[list[str]] = []
        self.today = ""
        self.selected = ""
        self.hover = ""
        self.setFixedSize(self.MARGIN * 2 + self.COLS * self.ISLAND_W + (self.COLS - 1) * self.GAP_X, 400)
        self.pomo.reading = True

    def load(self, path: dict, pmap: dict) -> None:
        self.islands, self.chips = [], {}
        nxt = studypath.next_step(path)
        self.today = nxt[2]["title"] if nxt else ""
        milestones = path["milestones"]
        heights = [self.HEAD + max(1, len(m["steps"])) * (self.CHIP_H + self.CHIP_GAP) + 12 for m in milestones]
        rows = [list(range(i, min(i + self.COLS, len(milestones)))) for i in range(0, len(milestones), self.COLS)]
        y = self.MARGIN + 26
        palette = ("focus", "short", "long", "focus", "short", "long", "focus", "short")
        for r, row in enumerate(rows):
            row_h = max(heights[mi] for mi in row)
            for col, mi in enumerate(row):
                col = col if r % 2 == 0 else self.COLS - 1 - col  # the road snakes back and forth
                x = self.MARGIN + col * (self.ISLAND_W + self.GAP_X)
                m = milestones[mi]
                rect = QRectF(x, y, self.ISLAND_W, heights[mi])
                d, t = studypath.milestone_progress(m)
                self.islands.append({"mi": mi, "rect": rect, "title": m["title"], "done": d, "total": t,
                                     "color": getattr(self.ctrl.theme, palette[mi % len(palette)])})
                for si, st in enumerate(m["steps"]):
                    chip = QRectF(x + 12, y + self.HEAD + si * (self.CHIP_H + self.CHIP_GAP), self.ISLAND_W - 24,
                                  self.CHIP_H)
                    state = "done" if st["done"] else "today" if st["title"] == self.today else "todo"
                    self.chips.setdefault(st["title"], {"rect": chip, "mi": mi, "col": col, "state": state})
            y += row_h + self.GAP_Y
        self.setFixedHeight(int(max(420, y - self.GAP_Y + self.MARGIN)))
        self.links = [ln for ln in (pmap or {}).get("links", []) if ln[0] in self.chips and ln[1] in self.chips]
        if self.selected not in self.chips:
            self.selected = self.today or next(iter(self.chips), "")
        self.update()

    def _at(self, pos: QPointF) -> str:
        return next((title for title, c in self.chips.items() if c["rect"].contains(pos)), "")

    def mouseMoveEvent(self, event) -> None:
        hover = self._at(event.position())
        if hover != self.hover:
            self.hover = hover
            self.setCursor(Qt.PointingHandCursor if hover else Qt.ArrowCursor)
            self.update()

    def mousePressEvent(self, event) -> None:
        title = self._at(event.position())
        if title and event.button() == Qt.LeftButton:
            self.selected = title
            self.picked.emit(title)

    def paintEvent(self, _event) -> None:
        th = self.ctrl.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.fillRect(self.rect(), QColor(th.page))
        self._paint_legend(p, th)
        self._paint_road(p, th)
        for isl in self.islands:
            self._paint_island(p, th, isl)
        focus = self.hover or self.selected
        for a, b, _why in self.links:
            if focus not in (a, b):
                self._paint_link(p, th, a, b, strong=False)
        for a, b, _why in self.links:
            if focus in (a, b):
                self._paint_link(p, th, a, b, strong=True)
        for title, chip in self.chips.items():
            self._paint_chip(p, th, title, chip)
        today = self.chips.get(self.today)
        if today is not None:
            self._paint_here(p, th, today["rect"])
        p.end()

    def _paint_road(self, p: QPainter, th) -> None:
        order = sorted(self.islands, key=lambda i: i["mi"])
        for a, b in zip(order, order[1:]):
            ca, cb = a["rect"].center(), b["rect"].center()
            path = QPainterPath(ca)
            if abs(a["rect"].top() - b["rect"].top()) < 1:  # same row
                path.lineTo(cb)
            else:  # down to the next row, on the side the road turns
                side = a["rect"].right() + self.GAP_X / 2 if cb.x() >= self.width() / 2 else a["rect"].left() - self.GAP_X / 2
                path.cubicTo(QPointF(side, ca.y()), QPointF(side, cb.y()), cb)
            reached = a["total"] > 0 and a["done"] == a["total"]
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(_alpha(th.focus, 150) if reached else QColor(th.raised), 18, Qt.SolidLine, Qt.RoundCap))
            p.drawPath(path)
            p.setPen(QPen(QColor(th.surface), 2.5, Qt.DashLine, Qt.RoundCap))
            p.drawPath(path)

    def _paint_island(self, p: QPainter, th, isl: dict) -> None:
        rect, color = isl["rect"], isl["color"]
        complete = isl["total"] > 0 and isl["done"] == isl["total"]
        p.setPen(Qt.NoPen)
        p.setBrush(th.shadow(int(th.shadow_alpha * 0.5)))
        p.drawRoundedRect(rect.translated(0, 4), 22, 22)
        p.setPen(QPen(_alpha(color, 200), 1.5))
        p.setBrush(_mix(th.surface, color, 0.2))
        p.drawRoundedRect(rect, 22, 22)
        p.setFont(_font(self.font(), 13, bold=True))
        p.setPen(QColor(th.ink))
        head = QRectF(rect.left() + 16, rect.top() + 8, rect.width() - 70, self.HEAD - 14)
        p.drawText(head, Qt.AlignVCenter | Qt.AlignLeft,
                   QFontMetricsF(p.font()).elidedText(isl["title"], Qt.ElideRight, head.width()))
        p.setPen(QColor(th.ink_2))
        p.setFont(_font(self.font(), 12))
        p.drawText(QRectF(rect.right() - 60, rect.top() + 8, 44, self.HEAD - 14), Qt.AlignVCenter | Qt.AlignRight,
                   f"{isl['done']}/{isl['total']}")
        if complete:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(th.chart))
            p.drawPath(_star(QPointF(rect.right() - 8, rect.top() + 8), 10))

    def _link_path(self, a: QRectF, b: QRectF, col: int) -> tuple[QPainterPath, QPointF, float]:
        if abs(a.center().x() - b.center().x()) > self.ISLAND_W / 2:
            right = b.center().x() > a.center().x()
            p1 = QPointF(a.right() if right else a.left(), a.center().y())
            p2 = QPointF(b.left() if right else b.right(), b.center().y())
            pull = (p2.x() - p1.x()) * 0.45
            c1, c2 = QPointF(p1.x() + pull, p1.y()), QPointF(p2.x() - pull, p2.y())
            angle = 0.0 if right else math.pi
        else:  # same column, different rows: out to the side and back in (the outer side for the edge columns)
            out = -1 if col == 0 else 1
            x1, x2 = (a.left(), b.left()) if out < 0 else (a.right(), b.right())
            p1, p2 = QPointF(x1, a.center().y()), QPointF(x2, b.center().y())
            bulge = out * (self.MARGIN - 2 if col in (0, self.COLS - 1) else self.GAP_X - 4)
            c1, c2 = QPointF(p1.x() + bulge * 1.4, p1.y()), QPointF(p2.x() + bulge * 1.4, p2.y())
            angle = 0.0 if out < 0 else math.pi
        path = QPainterPath(p1)
        path.cubicTo(c1, c2, p2)
        return path, p2, angle

    def _paint_link(self, p: QPainter, th, a: str, b: str, strong: bool) -> None:
        path, tip, angle = self._link_path(self.chips[a]["rect"], self.chips[b]["rect"], self.chips[a]["col"])
        color = _alpha(th.chart, 235 if strong else 85)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(color, 3 if strong else 1.8, Qt.SolidLine, Qt.RoundCap))
        p.drawPath(path)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        head = QPainterPath(tip)
        for da in (2.6, -2.6):
            head.lineTo(QPointF(tip.x() + 9 * math.cos(angle + da), tip.y() + 9 * math.sin(angle + da)))
        head.closeSubpath()
        p.drawPath(head)

    def _paint_chip(self, p: QPainter, th, title: str, chip: dict) -> None:
        rect, state = chip["rect"], chip["state"]
        fill = {"done": _mix(th.surface, th.chart, 0.14), "today": QColor(th.focus), "todo": QColor(th.surface)}[state]
        p.setBrush(fill)
        if title == self.selected:
            p.setPen(QPen(QColor(th.ink), 2))
        elif title == self.hover:
            p.setPen(QPen(QColor(th.chart), 1.6))
        else:
            p.setPen(QPen(th.border, 1))
        p.drawRoundedRect(rect, 9, 9)
        text_rect = rect.adjusted(30 if state == "done" else 10, 0, -40 if state == "today" else -10, 0)
        if state == "done":
            _check(p, QPointF(rect.left() + 16, rect.center().y()), 9, QColor(th.chart))
        p.setFont(_font(self.font(), 12, bold=state == "today"))
        p.setPen(QColor(th.on_accent) if state == "today" else QColor(th.muted) if state == "done" else QColor(th.ink))
        p.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft,
                   QFontMetricsF(p.font()).elidedText(title, Qt.ElideRight, text_rect.width()))

    def _paint_here(self, p: QPainter, th, rect: QRectF) -> None:
        """Pomo sits on the end of today's step, reading: you are here."""
        size = 32
        self.pomo.paint(p, QPointF(rect.right() - 20, rect.bottom() - 3 - size * BODY_RY), size, ground_shadow=False)

    def _paint_legend(self, p: QPainter, th) -> None:
        p.setFont(_font(self.font(), 12))
        p.setPen(QColor(th.muted))
        text = "Pomo sits on today's step. "
        text += ("Arrows show how steps help each other: click one to see why." if self.links
                 else "The road runs from one milestone to the next.")
        p.drawText(QRectF(self.MARGIN, self.MARGIN - 14, self.width() - 2 * self.MARGIN, 20),
                   Qt.AlignLeft | Qt.AlignVCenter, text)


# ------------------------------------------------------------------ the window
class PathMapWindow(QWidget):
    """Two views of your path: the skill tree (with your level) and the big picture."""

    def __init__(self, ctrl):
        super().__init__(None, Qt.Window)
        self.ctrl = ctrl
        self.job = None
        self._auto_drawn = False
        self._scroll_pending = False
        self.setWindowTitle("Pomo · Skill tree")
        self.setWindowIcon(ctrl.icon)
        self.resize(900, 820)
        page = QWidget()
        page.setObjectName("page")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(page)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(22, 16, 22, 16)
        lay.setSpacing(10)

        tabs = QHBoxLayout()
        self.tree_tab = QPushButton("Skill tree")
        self.map_tab = QPushButton("Big picture")
        for i, b in enumerate((self.tree_tab, self.map_tab)):
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _c=False, i=i: self.show_tab(i))
            tabs.addWidget(b)
        tabs.addStretch(1)
        path_btn = QPushButton("My path")
        path_btn.setObjectName("ghost")
        path_btn.clicked.connect(ctrl.open_path)
        tabs.addWidget(path_btn)
        lay.addLayout(tabs)

        self.empty = _label("", "secondary", wrap=True)
        self.empty.setTextFormat(Qt.RichText)
        self.empty.setText("<b>No path yet.</b><br><br>Make one with Ask Pomo (or in My path), and it shows up "
                           "here as a skill tree to climb and a map of how its steps fit together.")
        lay.addWidget(self.empty)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._tree_page())
        self.stack.addWidget(self._map_page())
        lay.addWidget(self.stack, 1)
        self.show_tab(0)

    # ------------------------------------------------------------- pages
    def _scroll(self, canvas: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidget(canvas)
        scroll.setAlignment(Qt.AlignHCenter)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        return scroll

    def _tree_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        head = QFrame()
        head.setObjectName("card")
        row = QHBoxLayout(head)
        row.setContentsMargins(16, 12, 16, 12)
        row.setSpacing(16)
        self.level = _label("", "h1")
        self.level.setMinimumWidth(110)
        bar = QVBoxLayout()
        bar.setSpacing(4)
        self.rank = _label("", "title")
        self.meter = Meter()
        self.xp_note = _label("", "muted", wrap=True)
        bar.addWidget(self.rank)
        bar.addWidget(self.meter)
        bar.addWidget(self.xp_note)
        row.addWidget(self.level)
        row.addLayout(bar, 1)
        lay.addWidget(head)
        self.tree = TreeCanvas(self.ctrl)
        self.tree.picked.connect(lambda *_k: self._show_node())
        self.tree_scroll = self._scroll(self.tree)
        lay.addWidget(self.tree_scroll, 1)

        info = QFrame()
        info.setObjectName("card")
        box = QVBoxLayout(info)
        box.setContentsMargins(16, 12, 16, 12)
        box.setSpacing(4)
        self.node_title = _label("", "title", wrap=True)
        self.node_note = _label("", "secondary", wrap=True)
        buttons = QHBoxLayout()
        self.small_btn = QPushButton()
        self.small_btn.setObjectName("primary")
        self.small_btn.clicked.connect(lambda: self._start(small=True))
        self.full_btn = QPushButton("Full session")
        self.full_btn.setObjectName("ghost")
        self.full_btn.clicked.connect(lambda: self._start(small=False))
        self.teach_btn = QPushButton("Teach Pomo")
        self.teach_btn.setObjectName("ghost")
        self.teach_btn.clicked.connect(self._teach)
        for b in (self.small_btn, self.full_btn, self.teach_btn):
            buttons.addWidget(b)
        buttons.addStretch(1)
        box.addWidget(self.node_title)
        box.addWidget(self.node_note)
        box.addSpacing(4)
        box.addLayout(buttons)
        lay.addWidget(info)
        return page

    def _map_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.map = MapCanvas(self.ctrl)
        self.map.picked.connect(lambda _t: self._show_map_info())
        self.map_scroll = self._scroll(self.map)
        lay.addWidget(self.map_scroll, 1)
        info = QFrame()
        info.setObjectName("card")
        box = QVBoxLayout(info)
        box.setContentsMargins(16, 12, 16, 12)
        box.setSpacing(6)
        self.map_info = _label("", "secondary", wrap=True)
        self.map_info.setTextFormat(Qt.RichText)
        row = QHBoxLayout()
        self.map_status = _label("", "muted", wrap=True)
        self.draw_btn = QPushButton("Draw the big picture")
        self.draw_btn.setObjectName("primary")
        self.draw_btn.clicked.connect(self.draw_map)
        row.addWidget(self.map_status, 1)
        row.addWidget(self.draw_btn)
        box.addWidget(self.map_info)
        box.addLayout(row)
        lay.addWidget(info)
        return page

    # ------------------------------------------------------------- showing
    def showEvent(self, event) -> None:
        super().showEvent(event)
        set_dark_title_bar(self, self.ctrl.theme.dark)
        self._scroll_pending = True
        self.refresh()

    def show_tab(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.tree_tab.setChecked(index == 0)
        self.map_tab.setChecked(index == 1)
        for b, on in ((self.tree_tab, index == 0), (self.map_tab, index == 1)):
            b.setObjectName("primary" if on else "ghost")
            b.style().unpolish(b)
            b.style().polish(b)
        self.setWindowTitle("Pomo · Skill tree" if index == 0 else "Pomo · Big picture")
        self.refresh()
        if index == 1 and self.isVisible():
            self._maybe_auto_draw()

    def refresh(self) -> None:
        s = self.ctrl.settings
        path = s.study_path
        has = bool(path) and bool(studypath.steps(path))
        self.empty.setVisible(not has)
        self.stack.setVisible(has)
        self._refresh_xp()
        if not has:
            return
        self.tree.load(path)
        fresh = self._celebrate_new(path)
        if self._scroll_pending:  # just opened: show the step that just unlocked, or else today's
            self._scroll_pending = False
            target = fresh or next((n for n in self.tree.nodes if n["state"] == "today"), None)
            if target is not None:
                QTimer.singleShot(0, lambda: self.tree_scroll.ensureVisible(int(target["pos"].x()),
                                                                            int(target["pos"].y()), 0, 230))
        self.map.load(path, s.path_map)
        self._show_node()
        self._show_map_info()

    def _refresh_xp(self) -> None:
        s = self.ctrl.settings
        level, into, need = skills.level_for(max(0, s.xp))
        self.level.setText(f"Level {level}")
        self.rank.setText(skills.rank(level))
        self.meter.set(into, need, QColor(self.ctrl.theme.chart))
        today = f"  ·  +{s.xp_today} XP today" if s.xp_day == date.today().isoformat() and s.xp_today else ""
        self.xp_note.setText(f"{into} of {need} XP to level {level + 1}{today}\nFocus earns 1 XP a minute; a step "
                             f"done {skills.STEP_XP}, teaching Pomo {skills.TEACH_XP}, a review card you get right "
                             f"{skills.CARD_XP}.")

    def _celebrate_new(self, path: dict) -> dict | None:
        """Steps finished since the tree last saw them: each unlocks with a little burst. Returns the last one."""
        s = self.ctrl.settings
        seen = set(s.tree_seen)
        fresh = [(mi, si, st) for mi, si, st in studypath.steps(path)
                 if st["done"] and skills.step_key(st["title"]) not in seen]
        if not fresh or not self.isVisible():
            return None
        for i, (mi, si, _st) in enumerate(fresh[-6:]):
            self.tree.burst(mi, si, delay=0.35 + 0.4 * i)
        s.tree_seen = (s.tree_seen + [skills.step_key(st["title"]) for _mi, _si, st in fresh])[-300:]
        self.ctrl.save_settings()
        return self.tree.node((fresh[-1][0], fresh[-1][1]))

    def _show_node(self) -> None:
        node = self.tree.node(self.tree.selected)
        if node is None:
            for w in (self.node_title, self.node_note, self.small_btn, self.full_btn, self.teach_btn):
                w.hide()
            return
        path = self.ctrl.settings.study_path
        st, state = node["step"], node["state"]
        milestone = path["milestones"][node["mi"]]["title"]
        self.node_title.setText(st["title"])
        self.node_title.show()
        self.node_note.show()
        n = st["pomodoros"]
        if state == "done":
            when = date.fromisoformat(st["done_on"]).strftime("%B %d").replace(" 0", " ") if st["done_on"] else ""
            note = f"Unlocked{' on ' + when if when else ''}  ·  {milestone}"
        elif state == "today":
            note = f"Today's step  ·  {min(st['spent'], n)} of {plural(n, 'pomodoro')}  ·  {milestone}"
        else:
            order = [(mi, si) for mi, si, _st in studypath.steps(path)]
            nxt = studypath.next_step(path)
            ahead = order.index((node["mi"], node["si"])) - order.index((nxt[0], nxt[1])) if nxt else 0
            note = (f"To unlock  ·  {plural(ahead, 'step')} after today's  ·  {milestone}" if ahead > 0
                    else f"To unlock  ·  {milestone}")
        self.node_note.setText(note)
        self.small_btn.setText(f"Start with {self.ctrl.settings.starter_min} minutes")
        self.small_btn.setVisible(state != "done")
        self.full_btn.setVisible(state != "done")
        self.teach_btn.setVisible(state == "done" and self.ctrl.settings.learn_loop)

    def _start(self, small: bool) -> None:
        key = self.tree.selected
        if key is not None:
            self.ctrl.start_step(key[0], key[1], small=small)

    def _teach(self) -> None:
        node = self.tree.node(self.tree.selected)
        if node is not None:
            self.ctrl.open_teach(node["step"]["title"])

    # ------------------------------------------------------------- the big picture
    def _show_map_info(self) -> None:
        s = self.ctrl.settings
        path, pmap = s.study_path, s.path_map
        title = self.map.selected
        parts = []
        if pmap.get("summary"):
            parts.append(f"<b>The big picture.</b> {html.escape(pmap['summary'])}")
        if title:
            head = f"<b>{html.escape(title)}</b>" + ("  ·  today's step" if title == self.map.today else "")
            role = pmap.get("roles", {}).get(title, "") if pmap else ""
            if role:
                head += f"<br>{html.escape(role)}"
            parts.append(head)
            before, after = skills.around(title, path, pmap)
            if before:
                parts.append("Builds on: " + "; ".join(f"<b>{html.escape(a)}</b> ({html.escape(w)})" for a, w in before))
            if after:
                parts.append("Leads to: " + "; ".join(f"<b>{html.escape(b)}</b> ({html.escape(w)})" for b, w in after))
        self.map_info.setText("<br><br>".join(parts))
        has_key = self.ctrl.has_ai_key()
        stale = bool(pmap) and pmap.get("sig") != skills.signature(path)
        busy = self.job is not None
        self.draw_btn.setEnabled(has_key and not busy)
        self.draw_btn.setText("Redraw" if pmap else "Draw the big picture")
        if busy:
            self.map_status.setText("Pomo is drawing your map…")
        elif not has_key:
            self.map_status.setText("With an AI key in Ask Pomo, Pomo draws how your steps connect and what each "
                                    "one is for. For now, the map shows your path in order.")
        elif stale:
            self.map_status.setText("Your path changed since this map was drawn. Redraw it?")
        elif pmap:
            self.map_status.setText("Click a step to see what it builds on and what it leads to.")
        else:
            self.map_status.setText("Pomo can draw how your steps connect, and why each one matters for what's next.")

    def _maybe_auto_draw(self) -> None:
        s = self.ctrl.settings
        if not self._auto_drawn and not s.path_map and s.study_path and self.ctrl.has_ai_key():
            self._auto_drawn = True
            self.draw_map()

    def draw_map(self) -> None:
        path = self.ctrl.settings.study_path
        if self.job is not None or not path or not self.ctrl.has_ai_key():
            return
        from .learn_windows import OneShot

        sig = skills.signature(path)
        self.job = OneShot(self.ctrl, skills.MAP_PROMPT, [{"role": "user", "content": skills.map_request(path)}], self)

        def drawn(text: str) -> None:
            self.job = None
            current = self.ctrl.settings.study_path
            pmap = skills.parse_map(text, current) if current else {}
            if not (pmap.get("links") or pmap.get("roles") or pmap.get("summary")):
                self._show_map_info()
                self.map_status.setText("Pomo couldn't draw it this time. Try again?")
                return
            self.ctrl.settings.path_map = {**pmap, "sig": sig}
            self.ctrl.save_settings()
            self.refresh()

        def failed(message: str) -> None:
            self.job = None
            self._show_map_info()
            self.map_status.setText(f"Pomo couldn't draw it: {message}")

        self.job.done.connect(drawn)
        self.job.failed.connect(failed)
        self.job.start()
        self._show_map_info()

    def closeEvent(self, event) -> None:
        if self.job is not None:
            self.job.worker.cancel()
            self.job = None
        super().closeEvent(event)
