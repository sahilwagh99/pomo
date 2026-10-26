"""Breathe with Pomo: a short guided break. Box breathing, then rest your eyes, then a stretch.

Pomo is the guide: he inflates as you breathe in, holds, and deflates as you breathe out.
"""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .pomo import BODY_RY, Pomo
from .theme import FONT_FAMILY, mode_color
from .winutil import set_dark_title_bar

BREATH_S = 4.0
CYCLES = 4
EYES_S = 20.0
STRETCH_S = 12.0
POMO_SIZE = 120.0


def build_steps() -> list[tuple[str, str, float, str]]:
    """(title, hint, seconds, kind): kind is in / hold_in / out / hold_out / eyes / stretch."""
    steps = []
    for cycle in range(CYCLES):
        round_note = f"Round {cycle + 1} of {CYCLES}"
        steps += [("Breathe in", round_note, BREATH_S, "in"),
                  ("Hold", round_note, BREATH_S, "hold_in"),
                  ("Breathe out", round_note, BREATH_S, "out"),
                  ("Hold", round_note, BREATH_S, "hold_out")]
    steps.append(("Rest your eyes", "Look at something far away, at least 6 meters off", EYES_S, "eyes"))
    steps.append(("Stretch", "Roll your shoulders and stretch your arms up high", STRETCH_S, "stretch"))
    return steps


def _ease(x: float) -> float:
    return 0.5 - 0.5 * math.cos(math.pi * max(0.0, min(1.0, x)))


class BreatheWindow(QWidget):
    def __init__(self, ctrl):
        super().__init__(None, Qt.Window)
        self.ctrl = ctrl
        self.setWindowTitle("Breathe with Pomo")
        self.setWindowIcon(ctrl.icon)
        self.setFixedSize(380, 460)
        self.setMouseTracking(True)
        self.pomo = Pomo()
        self.steps = build_steps()
        self.total = sum(step[2] for step in self.steps)
        self._t = 0.0
        self._last = time.monotonic()
        self._done = False
        self._frames = QTimer(self)
        self._frames.setInterval(33)
        self._frames.timeout.connect(self._tick)

    def start(self) -> None:
        self._t, self._done = 0.0, False
        self.pomo.particles.clear()
        self.pomo._next_fidget = 1e9  # no hops or dances: he's concentrating
        self.show()
        self.raise_()
        self.activateWindow()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        set_dark_title_bar(self, self.ctrl.theme.dark)
        self._last = time.monotonic()
        self._frames.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._frames.stop()

    # ---------------------------------------------------------------- timing
    def current(self) -> tuple[int, float]:
        """(step index, progress 0..1 within it); index == len(steps) when finished."""
        t = self._t
        for i, (_title, _hint, seconds, _kind) in enumerate(self.steps):
            if t < seconds:
                return i, t / seconds
            t -= seconds
        return len(self.steps), 1.0

    def inflate(self) -> float:
        index, x = self.current()
        if index >= len(self.steps):
            return 0.0
        kind = self.steps[index][3]
        return {"in": _ease(x), "hold_in": 1.0, "out": 1.0 - _ease(x)}.get(kind, 0.0)

    def _tick(self) -> None:
        now = time.monotonic()
        dt, self._last = min(0.1, now - self._last), now
        if not self._done:
            self._t += dt
            index, _x = self.current()
            if index >= len(self.steps):
                self._done = True
                self.pomo.celebrate()
                self.pomo.outfit = self.ctrl.current_outfit()
        index, _x = self.current()
        kind = self.steps[index][3] if index < len(self.steps) else "done"
        p = self.pomo
        p.outfit = self.ctrl.current_outfit()
        p.set_mood("break" if kind in ("out", "hold_out", "done") else "idle")
        if kind == "stretch" and p.t > p._dance_until:
            p._dance_until = p.t + STRETCH_S
        look = (0.95, -0.35) if kind == "eyes" else None  # gazing into the distance
        p.update(dt, look)
        self.update()

    # ----------------------------------------------------------------- input
    def mousePressEvent(self, event) -> None:
        if self._done:
            self.start()  # once more
        else:
            super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    # ----------------------------------------------------------------- paint
    def _font(self, px: float, weight=QFont.Normal) -> QFont:
        f = QFont(FONT_FAMILY)
        f.setPixelSize(round(px))
        f.setWeight(weight)
        return f

    def paintEvent(self, _event) -> None:
        t = self.ctrl.theme
        accent = mode_color(t, self.ctrl.settings, "short")
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        p.fillRect(self.rect(), QColor(t.page))
        w = self.width()
        index, x = self.current()
        if index < len(self.steps):
            title, hint, seconds, kind = self.steps[index]
            left = math.ceil(seconds * (1.0 - x))
        else:
            title, hint, seconds, kind, left = "All done", "Enjoy the rest of your break. Click to go again.", 0, "done", 0

        # the breath: a soft halo that swells with Pomo
        inflate = self.inflate()
        center = QPointF(w / 2, 250)
        halo = QColor(accent)
        for k, alpha in ((1.0, 34), (0.82, 46)):
            halo.setAlpha(alpha)
            p.setPen(Qt.NoPen)
            p.setBrush(halo)
            r = (92 + 48 * inflate) * k
            p.drawEllipse(center, r, r)
        size = POMO_SIZE * (0.8 + 0.32 * inflate)
        self.pomo.paint(p, QPointF(center.x(), center.y() + 34 - size * BODY_RY), size)

        p.setPen(QColor(t.ink))
        p.setFont(self._font(26, QFont.DemiBold))
        p.drawText(QRectF(0, 36, w, 36), Qt.AlignCenter, title)
        p.setPen(QColor(t.muted))
        p.setFont(self._font(13))
        p.drawText(QRectF(24, 74, w - 48, 40), Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap, hint)
        if kind != "done":
            p.setPen(QColor(t.ink_2))
            p.setFont(self._font(15, QFont.DemiBold))
            p.drawText(QRectF(0, 372, w, 24), Qt.AlignCenter, str(left))

        # progress through the whole exercise
        bar = QRectF(48, 412, w - 96, 5)
        track = QColor(accent)
        track.setAlpha(60)
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(bar, 2.5, 2.5)
        done = min(1.0, self._t / self.total)
        if done > 0:
            p.setBrush(accent)
            p.drawRoundedRect(QRectF(bar.left(), bar.top(), max(5.0, bar.width() * done), bar.height()), 2.5, 2.5)
        p.setPen(QPen(QColor(t.muted)))
        p.setFont(self._font(11))
        p.drawText(QRectF(0, 424, w, 20), Qt.AlignCenter, "Esc to close")
        p.end()
