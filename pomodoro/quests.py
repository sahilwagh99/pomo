"""Daily quests and badges: three small goals a day, a badge for each one you finish, and now
and then a new outfit for Pomo (Star clip, Bunny ears, Strawberry hat).

Quests are picked each morning from the ones that fit how you use Pomo (no agent quests if you
don't use coding agents, no early-bird quest after 9). Progress comes from your session history
and a few counters for things the history doesn't hold (a breathing break, a parked thought...).
Finishing all three in a day earns the Perfect day badge.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import date, datetime

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from .config import OUTFITS, QUEST_UNLOCKS
from .storage import SessionRecord
from .theme import FONT_FAMILY

PER_DAY = 3
PERFECT_DAY = "perfect_day"
TWO_PI = 2 * math.pi


@dataclass(frozen=True)
class Quest:
    key: str
    title: str          # the badge's name
    goal: str           # what to do
    target: int         # 0: your daily goal
    color: str          # the badge
    glyph: str
    needs: str = ""     # "agents": only with coding agents; "morning": only picked early; "study": with a path or log


QUESTS: dict[str, Quest] = {q.key: q for q in (
    Quest("early_bird", "Early bird", "Finish 2 pomodoros before 10 AM", 2, "#f5d77e", "sun", "morning"),
    Quest("laser", "Laser focus", "Finish a whole session with no pauses or distractions", 1, "#a7b3e3", "bolt"),
    Quest("hat_trick", "Hat trick", "Finish 3 pomodoros in a row, none stopped early", 3, "#f0a3b1", "three"),
    Quest("deep_dive", "Deep dive", "Finish a focus session of 45 minutes or more", 1, "#9fd0d9", "wave"),
    Quest("goal", "Goal getter", "Reach your daily goal", 0, "#95c283", "star"),
    Quest("breathe", "Deep breath", "Finish Breathe with Pomo", 1, "#a6cf92", "leaf"),
    Quest("park", "Parking pro", "Park 2 stray thoughts instead of chasing them", 2, "#f7c59f", "note"),
    Quest("check_in", "Check-in", "Tell Pomo how 3 sessions went", 3, "#f6b3c4", "heart"),
    Quest("play", "Playtime", "Play a mini game on a break", 1, "#f9dc8a", "petal"),
    Quest("review", "Code reviewer", "Finish a focus session reviewing your agent's changes", 1, "#b3a4e6", "glass",
          "agents"),
    Quest("tandem", "Perfect timing", "Take a break while a coding agent works", 1, "#c3b3e6", "gear", "agents"),
    Quest("recharge", "Recharge", "Take a long break", 1, "#b0bbe8", "moon"),
    Quest("cozy", "Cozy vibes", "Finish a focus session with Pomo's focus sounds on", 1, "#eea4b4", "music"),
    Quest("path_step", "On the path", "Finish a step on your path", 1, "#9fd0b4", "flag", "study"),
    Quest("english_log", "In my words", "Write your study log in English", 1, "#f7b7a3", "pen", "study"),
)}
PERFECT = Quest(PERFECT_DAY, "Perfect day", "Finish all three of a day's quests", 1, "#f5c26b", "crown")
COUNTED = {"laser": "clean", "breathe": "breathe", "park": "park", "play": "play", "review": "review",
           "tandem": "agent_break", "cozy": "sound_session", "path_step": "path_step", "english_log": "log"}  # quests that count events, not history


def badge(key: str) -> Quest | None:
    return PERFECT if key == PERFECT_DAY else QUESTS.get(key)


# ------------------------------------------------------------------ picking
def eligible(now: datetime, uses_agents: bool, studies: bool = False) -> list[str]:
    out = []
    for q in QUESTS.values():
        if q.needs == "agents" and not uses_agents:
            continue
        if q.needs == "study" and not studies:
            continue
        if q.needs == "morning" and now.hour >= 9:
            continue
        out.append(q.key)
    return out


def pick(day: date, keys: list[str], count: int = PER_DAY) -> list[str]:
    """The day's quests: the same all day, a different mix tomorrow."""
    rng = random.Random(day.toordinal() * 7919)
    pool = sorted(keys)
    rng.shuffle(pool)
    return pool[:count]


def roll_over(settings, now: datetime, uses_agents: bool, studies: bool = False) -> bool:
    """A new day: pick fresh quests and reset the counters. True if it did."""
    today = now.date().isoformat()
    if settings.quest_day == today and settings.quests:
        return False
    settings.quest_day = today
    settings.quests = pick(now.date(), eligible(now, uses_agents, studies))
    settings.quests_done = []
    settings.quest_counts = {}
    return True


def count(settings, counter: str, n: int = 1) -> None:
    settings.quest_counts[counter] = int(settings.quest_counts.get(counter, 0)) + n


# ------------------------------------------------------------------ progress
def _today(records: list[SessionRecord], day: date) -> list[SessionRecord]:
    return [r for r in records if r.day == day]


def target_of(quest: Quest, settings) -> int:
    return quest.target or max(1, int(settings.daily_goal))


def progress(quest: Quest, settings, records: list[SessionRecord], day: date) -> int:
    """How far along today's quest is (compare with target_of)."""
    if quest.key in COUNTED:
        return int(settings.quest_counts.get(COUNTED[quest.key], 0))
    focus = [r for r in _today(records, day) if r.mode == "focus"]
    done = [r for r in focus if r.completed]
    if quest.key == "early_bird":
        return sum(1 for r in done if r.started_at.hour < 10)
    if quest.key == "hat_trick":
        best = run = 0
        for r in sorted(focus, key=lambda r: r.started_at):
            run = run + 1 if r.completed else 0
            best = max(best, run)
        return best
    if quest.key == "deep_dive":
        return int(any(r.actual_sec >= 45 * 60 - 30 for r in done))
    if quest.key == "goal":
        return len(done)
    if quest.key == "check_in":
        return sum(1 for r in focus if r.rating)
    if quest.key == "recharge":
        return int(any(r.mode == "long" and r.completed for r in _today(records, day)))
    return 0


def check(settings, records: list[SessionRecord], day: date) -> list[Quest]:
    """Mark today's newly finished quests (badges, totals). Returns them, Perfect day last."""
    fresh: list[Quest] = []
    for key in settings.quests:
        quest = QUESTS.get(key)
        if quest is None or key in settings.quests_done:
            continue
        if progress(quest, settings, records, day) >= target_of(quest, settings):
            settings.quests_done.append(key)
            settings.badges[key] = int(settings.badges.get(key, 0)) + 1
            settings.quests_total += 1
            fresh.append(quest)
    if fresh and settings.quests and set(settings.quests) <= set(settings.quests_done):
        settings.badges[PERFECT_DAY] = int(settings.badges.get(PERFECT_DAY, 0)) + 1
        fresh.append(PERFECT)
    return fresh


def unlocked_by(before: int, after: int) -> list[str]:
    """Outfits the quests between two totals unlocked."""
    return [key for key, at in QUEST_UNLOCKS.items() if before < at <= after]


def next_outfit_text(total: int) -> str:
    ahead = sorted((at, key) for key, at in QUEST_UNLOCKS.items() if at > total)
    if not ahead:
        return "Every quest outfit unlocked!"
    at, key = ahead[0]
    left = at - total
    return f"{left} more {'quest' if left == 1 else 'quests'} to unlock the {OUTFITS[key].lower()}."


def status_line(quest: Quest, settings, records, day: date) -> str:
    """'Hat trick: 1/3' or 'Hat trick: done' for menus."""
    if quest.key in settings.quests_done:
        return f"{quest.title}: done"
    value, target = progress(quest, settings, records, day), target_of(quest, settings)
    return f"{quest.title}: {min(value, target)}/{target}"


# ------------------------------------------------------------------ drawing
def draw_badge(p: QPainter, c: QPointF, r: float, quest: Quest, earned: bool = True) -> None:
    """A little rosette: a scalloped medal in the badge's color with its picture in the middle."""
    color = QColor(quest.color if earned else "#d9d2c6")
    ink = QColor("#5b2c29" if earned else "#a39a90")
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    rosette = QPainterPath()
    bumps = 12
    for i in range(bumps * 8 + 1):
        a = TWO_PI * i / (bumps * 8)
        rr = r * (0.9 + 0.1 * math.cos(a * bumps))
        point = QPointF(c.x() + math.cos(a) * rr, c.y() + math.sin(a) * rr)
        rosette.moveTo(point) if i == 0 else rosette.lineTo(point)
    rosette.closeSubpath()
    p.setPen(QPen(ink, max(1.0, r * 0.07)))
    p.setBrush(color)
    p.drawPath(rosette)
    p.setBrush(QColor(255, 255, 255, 150 if earned else 90))
    p.setPen(Qt.NoPen)
    p.drawEllipse(c, r * 0.64, r * 0.64)
    _glyph(p, quest.glyph, c, r * 0.5, ink, color)
    p.restore()


_BADGES: dict[tuple, QPixmap] = {}


def paint_badge(p: QPainter, c: QPointF, r: float, quest: Quest, earned: bool, ratio: float) -> None:
    """draw_badge, from a picture drawn once and kept: shelves repaint often (scrolling, hovering)."""
    side = 2 * r + 4
    key = (quest.key, earned, r, ratio)
    pixmap = _BADGES.get(key)
    if pixmap is None:
        pixmap = QPixmap(round(side * ratio), round(side * ratio))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        draw_badge(painter, QPointF(side / 2, side / 2), r, quest, earned)
        painter.end()
        _BADGES[key] = pixmap
    p.drawPixmap(QPointF(c.x() - side / 2, c.y() - side / 2), pixmap)


def _glyph(p: QPainter, name: str, c: QPointF, s: float, ink: QColor, fill: QColor) -> None:
    pen = QPen(ink, max(1.0, s * 0.16), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    x, y = c.x(), c.y()
    if name == "sun":
        p.setBrush(fill)
        p.drawEllipse(c, s * 0.38, s * 0.38)
        for k in range(8):
            a = TWO_PI * k / 8
            p.drawLine(QPointF(x + math.cos(a) * s * 0.58, y + math.sin(a) * s * 0.58),
                       QPointF(x + math.cos(a) * s * 0.82, y + math.sin(a) * s * 0.82))
    elif name == "bolt":
        path = QPainterPath(QPointF(x + s * 0.15, y - s * 0.8))
        for px, py in ((-0.45, 0.1), (-0.02, 0.1), (-0.18, 0.8), (0.45, -0.15), (0.02, -0.15)):
            path.lineTo(x + px * s, y + py * s)
        path.closeSubpath()
        p.setBrush(fill)
        p.drawPath(path)
    elif name == "three":
        for dx in (-0.5, 0.0, 0.5):
            p.setBrush(ink)
            p.drawEllipse(QPointF(x + dx * s, y), s * 0.16, s * 0.16)
    elif name == "wave":
        for dy in (-0.3, 0.3):
            path = QPainterPath(QPointF(x - s * 0.7, y + dy * s))
            path.cubicTo(x - s * 0.35, y + (dy - 0.35) * s, x, y + (dy + 0.35) * s, x + s * 0.35, y + dy * s)
            path.quadTo(x + s * 0.55, y + (dy - 0.2) * s, x + s * 0.7, y + dy * s)
            p.drawPath(path)
    elif name in ("star", "crown"):
        path = QPainterPath()
        if name == "star":
            for i in range(10):
                a = -math.pi / 2 + i * math.pi / 5
                rr = s * (0.8 if i % 2 == 0 else 0.36)
                point = QPointF(x + math.cos(a) * rr, y + math.sin(a) * rr)
                path.moveTo(point) if i == 0 else path.lineTo(point)
        else:
            path.moveTo(x - s * 0.7, y + s * 0.45)
            for px, py in ((-0.7, -0.35), (-0.35, 0.05), (0.0, -0.6), (0.35, 0.05), (0.7, -0.35), (0.7, 0.45)):
                path.lineTo(x + px * s, y + py * s)
        path.closeSubpath()
        p.setBrush(fill)
        p.drawPath(path)
    elif name == "leaf":
        path = QPainterPath(QPointF(x - s * 0.6, y + s * 0.6))
        path.cubicTo(x - s * 0.7, y - s * 0.4, x + s * 0.2, y - s * 0.8, x + s * 0.7, y - s * 0.7)
        path.cubicTo(x + s * 0.6, y + s * 0.1, x, y + s * 0.6, x - s * 0.6, y + s * 0.6)
        p.setBrush(fill)
        p.drawPath(path)
        p.drawLine(QPointF(x - s * 0.6, y + s * 0.6), QPointF(x + s * 0.3, y - s * 0.3))
    elif name == "note":
        p.setBrush(fill)
        p.drawRoundedRect(QRectF(x - s * 0.6, y - s * 0.6, s * 1.2, s * 1.2), s * 0.15, s * 0.15)
        for dy in (-0.2, 0.15):
            p.drawLine(QPointF(x - s * 0.32, y + dy * s), QPointF(x + s * 0.32, y + dy * s))
    elif name == "heart":
        path = QPainterPath(QPointF(x, y + s * 0.7))
        path.cubicTo(x - s * 1.0, y + s * 0.0, x - s * 0.55, y - s * 0.85, x, y - s * 0.3)
        path.cubicTo(x + s * 0.55, y - s * 0.85, x + s * 1.0, y + s * 0.0, x, y + s * 0.7)
        p.setBrush(fill)
        p.drawPath(path)
    elif name == "petal":
        path = QPainterPath(QPointF(x, y + s * 0.75))
        path.cubicTo(x - s * 0.85, y + s * 0.1, x - s * 0.6, y - s * 0.8, x - s * 0.15, y - s * 0.65)
        path.lineTo(x, y - s * 0.4)
        path.lineTo(x + s * 0.15, y - s * 0.65)
        path.cubicTo(x + s * 0.6, y - s * 0.8, x + s * 0.85, y + s * 0.1, x, y + s * 0.75)
        p.setBrush(QColor("#f6a9bd"))
        p.drawPath(path)
    elif name == "glass":
        p.drawEllipse(QPointF(x - s * 0.12, y - s * 0.12), s * 0.42, s * 0.42)
        p.drawLine(QPointF(x + s * 0.2, y + s * 0.2), QPointF(x + s * 0.65, y + s * 0.65))
    elif name == "gear":
        p.drawEllipse(c, s * 0.42, s * 0.42)
        for k in range(8):
            a = TWO_PI * k / 8 + 0.2
            p.drawLine(QPointF(x + math.cos(a) * s * 0.5, y + math.sin(a) * s * 0.5),
                       QPointF(x + math.cos(a) * s * 0.75, y + math.sin(a) * s * 0.75))
    elif name == "moon":
        path = QPainterPath()
        path.addEllipse(c, s * 0.65, s * 0.65)
        cut = QPainterPath()
        cut.addEllipse(QPointF(x + s * 0.32, y - s * 0.2), s * 0.55, s * 0.55)
        p.setBrush(fill)
        p.drawPath(path.subtracted(cut))
    elif name == "flag":
        p.drawLine(QPointF(x - s * 0.45, y + s * 0.8), QPointF(x - s * 0.45, y - s * 0.75))
        flag = QPainterPath(QPointF(x - s * 0.45, y - s * 0.75))
        flag.lineTo(x + s * 0.6, y - s * 0.45)
        flag.lineTo(x - s * 0.45, y - s * 0.12)
        flag.closeSubpath()
        p.setBrush(fill)
        p.drawPath(flag)
    elif name == "pen":
        p.save()
        p.translate(c)
        p.rotate(-40)
        body = QRectF(-s * 0.2, -s * 0.75, s * 0.4, s * 1.15)
        p.setBrush(fill)
        p.drawRect(body)
        tip = QPainterPath(QPointF(-s * 0.2, s * 0.4))
        tip.lineTo(0, s * 0.8)
        tip.lineTo(s * 0.2, s * 0.4)
        tip.closeSubpath()
        p.drawPath(tip)
        p.restore()
    elif name == "music":
        p.setBrush(ink)
        for dx in (-0.4, 0.35):
            p.drawEllipse(QPointF(x + dx * s, y + s * 0.45), s * 0.2, s * 0.16)
        p.drawLine(QPointF(x - s * 0.22, y + s * 0.45), QPointF(x - s * 0.22, y - s * 0.55))
        p.drawLine(QPointF(x + s * 0.53, y + s * 0.45), QPointF(x + s * 0.53, y - s * 0.7))
        p.drawLine(QPointF(x - s * 0.22, y - s * 0.55), QPointF(x + s * 0.53, y - s * 0.7))


class QuestList(QWidget):
    """Today's quests for the dashboard: a badge, the goal, and a progress bar or a check."""

    ROW = 52

    def __init__(self, theme, parent: QWidget | None = None):
        super().__init__(parent)
        self.theme = theme
        self.rows: list[tuple[Quest, int, int, bool]] = []  # quest, value, target, done
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumHeight(self.ROW * PER_DAY)

    def set_rows(self, rows: list[tuple[Quest, int, int, bool]]) -> None:
        self.rows = rows
        self.setFixedHeight(max(self.ROW, self.ROW * len(rows)))
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(360, self.ROW * max(1, len(self.rows)))

    def paintEvent(self, _event) -> None:
        t = self.theme
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        w = self.width()
        if not self.rows:
            p.setPen(QColor(t.muted))
            p.drawText(self.rect(), Qt.AlignCenter, "Quests are off. Turn them on in Settings > Focus.")
            p.end()
            return
        for i, (quest, value, target, done) in enumerate(self.rows):
            top = i * self.ROW
            paint_badge(p, QPointF(22, top + self.ROW / 2), 18, quest, done, self.devicePixelRatioF())
            title = QFont(FONT_FAMILY)
            title.setPixelSize(13)
            title.setWeight(QFont.DemiBold)
            p.setFont(title)
            p.setPen(QColor(t.ink))
            p.drawText(QRectF(52, top + 6, w - 130, 18), Qt.AlignLeft | Qt.AlignVCenter, quest.title)
            body = QFont(FONT_FAMILY)
            body.setPixelSize(12)
            p.setFont(body)
            p.setPen(QColor(t.ink_2))
            p.drawText(QRectF(52, top + 24, w - 130, 18), Qt.AlignLeft | Qt.AlignVCenter,
                       QFontMetricsF(body).elidedText(quest.goal, Qt.ElideRight, w - 130))
            bar = QRectF(w - 70, top + self.ROW / 2 - 3, 62, 6)
            if done:
                p.setPen(QColor(t.success))
                p.setFont(title)
                p.drawText(QRectF(w - 78, top, 70, self.ROW), Qt.AlignRight | Qt.AlignVCenter, "✓ Done")
                continue
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(t.grid))
            p.drawRoundedRect(bar, 3, 3)
            share = min(1.0, value / max(1, target))
            if share > 0:
                p.setBrush(QColor(t.chart))
                p.drawRoundedRect(QRectF(bar.left(), bar.top(), max(6.0, bar.width() * share), bar.height()), 3, 3)
            p.setPen(QColor(t.muted))
            p.setFont(body)
            p.drawText(QRectF(w - 70, top + self.ROW / 2 + 4, 62, 16), Qt.AlignRight | Qt.AlignTop,
                       f"{min(value, target)}/{target}")
        p.end()


class BadgeShelf(QWidget):
    """Every badge, earned ones in color with how many times; the rest waiting in grey."""

    CELL = 74

    def __init__(self, theme, parent: QWidget | None = None):
        super().__init__(parent)
        self.theme = theme
        self.badges: dict[str, int] = {}
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def set_badges(self, badges: dict[str, int]) -> None:
        self.badges = dict(badges)
        self._fit()
        self.update()

    def _all(self) -> list[Quest]:
        return [PERFECT, *QUESTS.values()]

    def _columns(self) -> int:
        return max(1, self.width() // self.CELL)

    def _fit(self) -> None:
        rows = math.ceil(len(self._all()) / self._columns())
        self.setFixedHeight(rows * self.CELL + 4)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit()

    def _cell(self, index: int) -> QRectF:
        cols = self._columns()
        return QRectF((index % cols) * self.CELL, (index // cols) * self.CELL, self.CELL, self.CELL)

    def paintEvent(self, _event) -> None:
        t = self.theme
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        font = QFont(FONT_FAMILY)
        font.setPixelSize(10)
        font.setWeight(QFont.DemiBold)
        for i, quest in enumerate(self._all()):
            cell = self._cell(i)
            times = self.badges.get(quest.key, 0)
            center = QPointF(cell.center().x(), cell.top() + 26)
            paint_badge(p, center, 19, quest, times > 0, self.devicePixelRatioF())
            if times > 1:
                dot = QPointF(center.x() + 15, center.y() - 15)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(t.short))
                p.drawEllipse(dot, 9, 9)
                p.setPen(QColor(t.on_accent))
                p.setFont(font)
                p.drawText(QRectF(dot.x() - 9, dot.y() - 9, 18, 18), Qt.AlignCenter, f"{min(times, 99)}")
            p.setPen(QColor(t.ink_2 if times else t.muted))
            small = QFont(FONT_FAMILY)
            small.setPixelSize(10)
            p.setFont(small)
            p.drawText(QRectF(cell.left() + 1, cell.top() + 48, cell.width() - 2, 14), Qt.AlignCenter,
                       QFontMetricsF(small).elidedText(quest.title, Qt.ElideRight, cell.width() - 4))
        p.end()

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        for i, quest in enumerate(self._all()):
            if self._cell(i).contains(pos):
                times = self.badges.get(quest.key, 0)
                earned = f"Earned {times} {'time' if times == 1 else 'times'}" if times else "Not earned yet"
                QToolTip.showText(event.globalPosition().toPoint(), f"{quest.title}\n{quest.goal}\n{earned}", self)
                return
        QToolTip.hideText()
