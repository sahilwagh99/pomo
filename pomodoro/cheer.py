"""A little cheer from Pomo during focus: a few warm words at halfway, with 5 minutes left and in the
last minute, in a small speech bubble beside the timer that fades after a few seconds.

Kept gentle on purpose: no sound, no card, no notification, and the bubble lets clicks through.
Each moment comes once per session, only in sessions long enough for it to mean something.
"""
from __future__ import annotations

import random

from PySide6.QtCore import QPoint, QPropertyAnimation, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from .theme import FONT_FAMILY

# moment -> (shortest session it's for, in minutes)
MOMENTS = {"halfway": 4, "five_left": 15, "last_minute": 8}
LINES = {
    "halfway": ("Halfway there. Nice and steady!", "Halfway through. You've got this!",
                "Half done already. Keep going!", "Halfway! Look at you go."),
    "five_left": ("5 minutes to go. Finish strong!", "Almost there: 5 more minutes.",
                  "5 minutes left, then a break!", "The home stretch. 5 minutes!"),
    "last_minute": ("Last minute! You did it.", "One more minute. Nearly there!",
                    "Final minute. So proud of you!", "Just a minute left. Great focus!"),
}
TASK_LINES = {"halfway": "Halfway through {task}!", "five_left": "5 more minutes on {task}!"}
SHOW_MS = 5500


def due(total: float, remaining: float, done: set[str]) -> str | None:
    """The moment to cheer now, if any (the latest one reached, so jumping ahead skips the rest)."""
    if total <= 0:
        return None
    reached = []
    if total >= MOMENTS["halfway"] * 60 and remaining <= total / 2:
        reached.append("halfway")
    if total >= MOMENTS["five_left"] * 60 and remaining <= 300:
        reached.append("five_left")
    if total >= MOMENTS["last_minute"] * 60 and remaining <= 60:
        reached.append("last_minute")
    fresh = [m for m in reached if m not in done]
    if not fresh:
        return None
    done.update(reached)  # anything you skipped past is done too
    return fresh[-1]


def line(moment: str, task: str = "", rng: random.Random | None = None) -> str:
    rng = rng or random.Random()
    task = task.strip()
    if task and moment in TASK_LINES and len(task) <= 28 and rng.random() < 0.4:
        return TASK_LINES[moment].format(task=task)
    return rng.choice(LINES[moment])


class Whisper(QWidget):
    """A small speech bubble from Pomo that fades in, stays a few seconds and fades out. Clicks go through it."""

    PAD_X, PAD_Y, TAIL = 12, 8, 7

    def __init__(self, ctrl):
        super().__init__(None)
        self.ctrl = ctrl
        self.text = ""
        self.tail_up = True  # the tail points up at Pomo (under the island) or down (above the orb)
        self.tail_x = 24.0
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint
                            | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._font = QFont(FONT_FAMILY)
        self._font.setPixelSize(13)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.finished.connect(self._faded)
        self._hold = QTimer(self)
        self._hold.setSingleShot(True)
        self._hold.timeout.connect(self.fade_out)

    def say(self, text: str, pomo: QRect) -> None:
        """Show text beside Pomo (his body, in screen coordinates)."""
        self.text = text
        width = QFontMetrics(self._font).horizontalAdvance(text) + 2 * self.PAD_X + 2
        height = QFontMetrics(self._font).height() + 2 * self.PAD_Y + self.TAIL + 2
        self.setFixedSize(width, height)
        screen = QGuiApplication.screenAt(pomo.center()) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        # above Pomo (the orb), unless he's at the top of the screen (the island): then below him
        self.tail_up = pomo.top() - geo.top() <= height + 40
        x = pomo.center().x() - 24
        y = pomo.bottom() + 6 if self.tail_up else pomo.top() - height - 4
        x = max(geo.left() + 4, min(x, geo.right() - width - 4))
        y = max(geo.top() + 2, min(y, geo.bottom() - height - 2))
        self.tail_x = max(14.0, min(width - 14.0, pomo.center().x() - x))
        self.move(QPoint(x, y))
        self._fade.stop()
        if not self.isVisible():
            self.setWindowOpacity(0.0)
            self.show()
        self._fade.setDuration(260)
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(1.0)
        self._fade.start()
        self._hold.start(SHOW_MS)
        self.update()

    def fade_out(self) -> None:
        if not self.isVisible():
            return
        self._hold.stop()
        self._fade.stop()
        self._fade.setDuration(500)
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _faded(self) -> None:
        if self.windowOpacity() <= 0.01:
            self.hide()

    def paintEvent(self, _event) -> None:
        t = self.ctrl.theme
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        w, h, tail = self.width(), self.height(), self.TAIL
        body = QRectF(1, 1 + (tail if self.tail_up else 0), w - 2, h - 2 - tail)
        path = QPainterPath()
        path.addRoundedRect(body, body.height() / 2, body.height() / 2)
        pointer = QPainterPath()
        x = self.tail_x
        if self.tail_up:
            pointer.moveTo(x - 6, body.top() + 1)
            pointer.lineTo(x, 1)
            pointer.lineTo(x + 6, body.top() + 1)
        else:
            pointer.moveTo(x - 6, body.bottom() - 1)
            pointer.lineTo(x, h - 1)
            pointer.lineTo(x + 6, body.bottom() - 1)
        pointer.closeSubpath()
        shape = path.united(pointer)
        p.setPen(QPen(t.border, 1))
        p.setBrush(QColor(t.surface))
        p.drawPath(shape)
        p.setFont(self._font)
        p.setPen(QColor(t.ink_2))
        p.drawText(body, Qt.AlignCenter, self.text)
        p.end()
