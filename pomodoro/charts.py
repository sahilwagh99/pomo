"""Hand-painted dashboard charts (no plotting dependency keeps the .exe small).

Mark specs: bars at most 24px thick with 4px rounded data ends and a square
baseline, hairline solid grid one step off the surface, a hover tooltip on every
mark, and text in ink tokens rather than series colors.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QToolTip, QWidget

from .stats import DayStat, fmt_day, fmt_hm, plural
from .theme import FONT_FAMILY, Theme

MAX_BAR = 24.0
RADIUS = 4.0


@dataclass
class Bar:
    label: str
    value: float
    tip: str
    value_text: str = ""


def ui_font(px: float, weight=QFont.Normal) -> QFont:
    f = QFont(FONT_FAMILY)
    f.setPixelSize(round(px))
    f.setWeight(weight)
    return f


def minute_axis(peak: float) -> tuple[float, float]:
    """(step, top) for a minutes axis with at most ~4 clean intervals."""
    if peak <= 0:
        return 15.0, 60.0
    for step in (5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240, 300, 360, 480, 600, 720, 960, 1200, 1800, 2400):
        if peak / step <= 4:
            break
    else:
        step = math.ceil(peak / 4 / 600) * 600
    return float(step), math.ceil(peak / step) * float(step)


def fmt_minutes_tick(minutes: float) -> str:
    if minutes == 0:
        return "0"
    if minutes < 60:
        return f"{minutes:g}m"
    return f"{minutes / 60:g}h"


def _top_rounded(x: float, y: float, w: float, h: float, r: float) -> QPainterPath:
    r = min(r, w / 2, h)
    path = QPainterPath(QPointF(x, y + h))
    path.lineTo(x, y + r)
    path.quadTo(x, y, x + r, y)
    path.lineTo(x + w - r, y)
    path.quadTo(x + w, y, x + w, y + r)
    path.lineTo(x + w, y + h)
    path.closeSubpath()
    return path


def _right_rounded(x: float, y: float, w: float, h: float, r: float) -> QPainterPath:
    r = min(r, h / 2, w)
    path = QPainterPath(QPointF(x, y))
    path.lineTo(x + w - r, y)
    path.quadTo(x + w, y, x + w, y + r)
    path.lineTo(x + w, y + h - r)
    path.quadTo(x + w, y + h, x + w - r, y + h)
    path.lineTo(x, y + h)
    path.closeSubpath()
    return path


class ChartBase(QWidget):
    empty_text = "No focus time in this range yet"

    def __init__(self, theme: Theme, parent: QWidget | None = None):
        super().__init__(parent)
        self.theme = theme
        self._hover = -1
        self.setMouseTracking(True)

    def _set_hover(self, index: int, tip: str = "", global_pos=None) -> None:
        if index != self._hover:
            self._hover = index
            self.update()
        if index >= 0 and tip and global_pos is not None:
            QToolTip.showText(global_pos, tip, self)
        else:
            QToolTip.hideText()

    def leaveEvent(self, event) -> None:
        self._set_hover(-1)
        super().leaveEvent(event)

    def _paint_empty(self, p: QPainter) -> None:
        p.setFont(ui_font(13))
        p.setPen(QColor(self.theme.muted))
        p.drawText(self.rect(), Qt.AlignCenter, self.empty_text)


class ColumnChart(ChartBase):
    """Vertical bars over a minutes axis, with an optional reference line."""

    def __init__(self, theme: Theme, height: int = 220, parent: QWidget | None = None):
        super().__init__(theme, parent)
        self.setMinimumHeight(height)
        self.bars: list[Bar] = []
        self.color = QColor(theme.focus)
        self.ref: float | None = None
        self.ref_label = ""
        self.label_every: int | None = None

    def set_data(self, bars: list[Bar], color: QColor, ref: float | None = None,
                 ref_label: str = "", label_every: int | None = None) -> None:
        self.bars, self.color, self.ref, self.ref_label, self.label_every = bars, QColor(color), ref, ref_label, label_every
        self._hover = -1
        self.update()

    def _geometry(self):
        peak = max([b.value for b in self.bars] + [self.ref or 0, 0])
        step, top = minute_axis(peak)
        ticks = [i * step for i in range(int(round(top / step)) + 1)]
        fm = QFontMetricsF(ui_font(11))
        left = max(fm.horizontalAdvance(fmt_minutes_tick(v)) for v in ticks) + 12
        plot = QRectF(left, 12, max(10.0, self.width() - left - 6), max(10.0, self.height() - 12 - 28))
        return plot, ticks, top

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        t = self.theme
        if not self.bars or all(b.value <= 0 for b in self.bars):
            self._paint_empty(p)
            return
        plot, ticks, top = self._geometry()
        n = len(self.bars)
        slot = plot.width() / n
        bar_w = min(MAX_BAR, max(2.0, slot * 0.64))

        if 0 <= self._hover < n:
            p.fillRect(QRectF(plot.left() + slot * self._hover, plot.top(), slot, plot.height()), QColor(t.hover))

        font = ui_font(11)
        p.setFont(font)
        for v in ticks:
            y = plot.bottom() - v / top * plot.height()
            if v:
                p.setPen(QPen(QColor(t.grid), 1))
                p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(QColor(t.muted))
            p.drawText(QRectF(0, y - 8, plot.left() - 8, 16), Qt.AlignRight | Qt.AlignVCenter, fmt_minutes_tick(v))

        p.setPen(Qt.NoPen)
        for i, bar in enumerate(self.bars):
            if bar.value <= 0:
                continue
            h = max(2.0, bar.value / top * plot.height())
            x = plot.left() + slot * i + (slot - bar_w) / 2
            p.fillPath(_top_rounded(x, plot.bottom() - h, bar_w, h, RADIUS), self.color)

        p.setPen(QPen(QColor(t.axis), 1))
        p.drawLine(QPointF(plot.left(), plot.bottom()), QPointF(plot.right(), plot.bottom()))

        if self.ref and self.ref <= top:
            y = plot.bottom() - self.ref / top * plot.height()
            p.setPen(QPen(QColor(t.ink_2), 1))
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            fm = QFontMetricsF(font)
            w = fm.horizontalAdvance(self.ref_label) + 10
            label_rect = QRectF(plot.right() - w, y - 18, w, 16)
            p.fillRect(label_rect, QColor(t.surface))
            p.setPen(QColor(t.ink_2))
            p.drawText(label_rect, Qt.AlignRight | Qt.AlignVCenter, self.ref_label)

        fm = QFontMetricsF(font)
        p.setPen(QColor(t.muted))
        if self.label_every:
            indices = range(0, n, self.label_every)
        else:
            widest = max(fm.horizontalAdvance(b.label) for b in self.bars)
            every = max(1, math.ceil((widest + 12) / slot))
            indices = range(n - 1, -1, -every)
        for i in indices:
            label = self.bars[i].label
            w = fm.horizontalAdvance(label) + 4
            cx = plot.left() + slot * (i + 0.5)
            x = max(0.0, min(cx - w / 2, self.width() - w))
            p.drawText(QRectF(x, plot.bottom() + 7, w, 16), Qt.AlignCenter, label)

    def mouseMoveEvent(self, event) -> None:
        if not self.bars:
            return
        plot, _, _ = self._geometry()
        pos = event.position()
        if plot.left() <= pos.x() <= plot.right() and pos.y() <= plot.bottom() + 24:
            i = min(len(self.bars) - 1, int((pos.x() - plot.left()) / (plot.width() / len(self.bars))))
            self._set_hover(i, self.bars[i].tip, event.globalPosition().toPoint())
        else:
            self._set_hover(-1)


class HBarChart(ChartBase):
    """Ranked horizontal bars with the label on the left and value at the tip."""

    ROW = 30
    THICK = 12.0

    def __init__(self, theme: Theme, parent: QWidget | None = None):
        super().__init__(theme, parent)
        self.rows: list[Bar] = []
        self.color = QColor(theme.focus)
        self.setMinimumHeight(150)

    def set_data(self, rows: list[Bar], color: QColor) -> None:
        self.rows, self.color = rows, QColor(color)
        self._hover = -1
        self.setMinimumHeight(max(150, len(rows) * self.ROW + 8))
        self.update()

    def _columns(self) -> tuple[float, float, float]:
        fm = QFontMetricsF(ui_font(12))
        label_w = min(self.width() * 0.4, max(fm.horizontalAdvance(r.label) for r in self.rows) + 6)
        value_w = max(fm.horizontalAdvance(r.value_text) for r in self.rows) + 10
        x0 = label_w + 12
        return label_w, x0, max(x0 + 10, self.width() - value_w - 4)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        t = self.theme
        if not self.rows or all(r.value <= 0 for r in self.rows):
            self._paint_empty(p)
            return
        label_w, x0, x1 = self._columns()
        peak = max(r.value for r in self.rows)
        font = ui_font(12)
        fm = QFontMetricsF(font)
        p.setFont(font)
        for i, row in enumerate(self.rows):
            y = 4 + i * self.ROW
            cy = y + self.ROW / 2
            if i == self._hover:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(t.hover))
                p.drawRoundedRect(QRectF(0, y, self.width(), self.ROW), 6, 6)
            p.setPen(QColor(t.ink_2))
            p.drawText(QRectF(4, y, label_w, self.ROW), Qt.AlignLeft | Qt.AlignVCenter,
                       fm.elidedText(row.label, Qt.ElideRight, label_w - 4))
            w = max(2.0, row.value / peak * (x1 - x0))
            p.fillPath(_right_rounded(x0, cy - self.THICK / 2, w, self.THICK, RADIUS), self.color)
            p.setPen(QColor(t.ink))
            p.drawText(QRectF(x0 + w + 8, y, 200, self.ROW), Qt.AlignLeft | Qt.AlignVCenter, row.value_text)

    def mouseMoveEvent(self, event) -> None:
        i = int((event.position().y() - 4) // self.ROW)
        if 0 <= i < len(self.rows):
            self._set_hover(i, self.rows[i].tip, event.globalPosition().toPoint())
        else:
            self._set_hover(-1)


class CalendarHeatmap(ChartBase):
    """One square per day, weeks as columns (Mon at top), shaded by share of the daily goal."""

    CELL = 13.0
    GAP = 3.0
    LEFT = 32.0
    TOP = 20.0

    def __init__(self, theme: Theme, parent: QWidget | None = None):
        super().__init__(theme, parent)
        self.days: dict[date, DayStat] = {}
        self.today = date.today()
        self.goal_sec = 8 * 25 * 60.0
        self.setMinimumHeight(int(self.TOP + 7 * (self.CELL + self.GAP) + 30))

    def set_data(self, days: dict[date, DayStat], today: date, goal_sec: float) -> None:
        self.days, self.today, self.goal_sec = days, today, max(60.0, goal_sec)
        self._hover = -1
        self.update()

    def _weeks(self) -> tuple[int, date]:
        pitch = self.CELL + self.GAP
        weeks = max(4, min(53, int((self.width() - self.LEFT) // pitch)))
        last_monday = self.today - timedelta(days=self.today.weekday())
        return weeks, last_monday - timedelta(weeks=weeks - 1)

    def _level(self, seconds: float) -> int:
        if seconds <= 0:
            return 0
        ratio = seconds / self.goal_sec
        return 1 + sum(ratio >= cut for cut in (0.25, 0.5, 0.75, 1.0))

    def _color(self, level: int) -> QColor:
        return QColor(self.theme.heat_empty if level == 0 else self.theme.heat[level - 1])

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        t = self.theme
        weeks, start = self._weeks()
        pitch = self.CELL + self.GAP
        p.setFont(ui_font(10.5))
        p.setPen(QColor(t.muted))
        for row, name in ((0, "Mon"), (2, "Wed"), (4, "Fri")):
            p.drawText(QRectF(0, self.TOP + row * pitch, self.LEFT - 6, self.CELL), Qt.AlignRight | Qt.AlignVCenter, name)
        last_label_x = -100.0
        for w in range(weeks):
            monday = start + timedelta(weeks=w)
            x = self.LEFT + w * pitch
            month_start = next((monday + timedelta(days=i) for i in range(7) if (monday + timedelta(days=i)).day == 1), None)
            if (month_start or w == 0) and x - last_label_x > 30:
                label_day = month_start or monday
                p.setPen(QColor(t.muted))
                p.drawText(QRectF(x, 0, 40, self.TOP - 4), Qt.AlignLeft | Qt.AlignVCenter, f"{label_day:%b}")
                last_label_x = x
            for d in range(7):
                day = monday + timedelta(days=d)
                if day > self.today:
                    continue
                stat = self.days.get(day)
                rect = QRectF(x, self.TOP + d * pitch, self.CELL, self.CELL)
                p.setPen(Qt.NoPen)
                p.setBrush(self._color(self._level(stat.focus_sec if stat else 0)))
                p.drawRoundedRect(rect, 3, 3)
                if self._hover == w * 7 + d:
                    p.setPen(QPen(QColor(t.ink), 1.5))
                    p.setBrush(Qt.NoBrush)
                    p.drawRoundedRect(rect.adjusted(-1.5, -1.5, 1.5, 1.5), 4, 4)

        # legend: Less [] [] [] [] [] More
        y = self.TOP + 7 * pitch + 10
        fm = QFontMetricsF(p.font())
        more_w = fm.horizontalAdvance("More")
        x = self.LEFT + weeks * pitch - self.GAP - more_w
        p.setPen(QColor(t.muted))
        p.drawText(QRectF(x, y - 2, more_w + 2, self.CELL + 4), Qt.AlignLeft | Qt.AlignVCenter, "More")
        x -= 6
        for level in range(5, -1, -1):
            x -= self.CELL
            p.setPen(Qt.NoPen)
            p.setBrush(self._color(level))
            p.drawRoundedRect(QRectF(x, y, self.CELL, self.CELL), 3, 3)
            x -= self.GAP
        less_w = fm.horizontalAdvance("Less")
        p.setPen(QColor(t.muted))
        p.drawText(QRectF(x - less_w - 4, y - 2, less_w + 2, self.CELL + 4), Qt.AlignRight | Qt.AlignVCenter, "Less")

    def mouseMoveEvent(self, event) -> None:
        weeks, start = self._weeks()
        pitch = self.CELL + self.GAP
        pos = event.position()
        w = int((pos.x() - self.LEFT) // pitch)
        d = int((pos.y() - self.TOP) // pitch)
        if 0 <= w < weeks and 0 <= d < 7:
            day = start + timedelta(weeks=w, days=d)
            if day <= self.today:
                stat = self.days.get(day)
                sec = stat.focus_sec if stat else 0
                pomos = stat.pomodoros if stat else 0
                tip = f"{fmt_day(day)}\n{fmt_hm(sec)} focused · {plural(pomos, 'pomodoro')}"
                self._set_hover(w * 7 + d, tip, event.globalPosition().toPoint())
                return
        self._set_hover(-1)


class Meter(QWidget):
    """Thin progress bar; the track is a light step of the fill's own hue."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedHeight(8)
        self.value = 0.0
        self.maximum = 1.0
        self.color = QColor("#d95926")

    def set(self, value: float, maximum: float, color: QColor) -> None:
        self.value, self.maximum, self.color = value, max(1e-9, maximum), QColor(color)
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.height() / 2
        track = QColor(self.color)
        track.setAlpha(55)
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(self.rect()), r, r)
        frac = min(1.0, self.value / self.maximum)
        if frac > 0:
            p.setBrush(self.color)
            p.drawRoundedRect(QRectF(0, 0, max(self.height(), frac * self.width()), self.height()), r, r)
