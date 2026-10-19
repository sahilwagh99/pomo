"""Petal Catch: a tiny break-time game with Pomo.

Sakura petals drift down; move Pomo (mouse or arrow keys) to catch them before
the 30-second round ends. Golden petals are worth 3. The best score is saved.
"""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from . import APP_NAME
from .icons import draw_glyph
from .pomo import BODY_RY, Pomo
from .winutil import set_dark_title_bar

ROUND_SECONDS = 30.0
POMO_SIZE = 64.0
PETAL_PINK = QColor("#f6a9bd")
PETAL_GOLD = QColor("#f5d77e")
OUTLINE = QColor("#5b2c29")


@dataclass
class Petal:
    x: float
    y: float
    vy: float
    sway: float
    phase: float
    golden: bool
    spin: float

    @property
    def draw_x(self) -> float:
        return self.x + math.sin(self.phase) * self.sway


class PetalGame:
    """Game state and rules, with no widgets, so it can be tested on its own."""

    def __init__(self, width: float, height: float, rng: random.Random | None = None):
        self.w, self.h = width, height
        self.rng = rng or random.Random()
        self.reset()

    def reset(self) -> None:
        self.petals: list[Petal] = []
        self.score = 0
        self.caught = 0
        self.time_left = ROUND_SECONDS
        self.running = False
        self._spawn_in = 0.3
        self.pomo_x = self.target_x = self.w / 2

    def start(self) -> None:
        self.reset()
        self.running = True

    @property
    def pomo_y(self) -> float:
        """Pomo's resting center."""
        return self.h - 34 - POMO_SIZE * BODY_RY

    def move_to(self, x: float) -> None:
        half = POMO_SIZE / 2
        self.target_x = max(half, min(self.w - half, x))

    def update(self, dt: float) -> list[tuple[str, Petal | None]]:
        """Advance the game; returns events: ('catch', petal), ('miss', petal), ('end', None)."""
        if not self.running:
            return []
        dt = max(0.0, min(dt, 0.05))
        self.time_left -= dt
        self.pomo_x += (self.target_x - self.pomo_x) * (1.0 - math.exp(-dt * 14.0))
        progress = 1.0 - max(0.0, self.time_left) / ROUND_SECONDS

        self._spawn_in -= dt
        if self._spawn_in <= 0.0:
            self._spawn_in = self.rng.uniform(0.35, 0.75) * (1.0 - 0.45 * progress)
            golden = self.rng.random() < 0.1
            speed = self.rng.uniform(70.0, 120.0) * (1.0 + 0.6 * progress) * (1.25 if golden else 1.0)
            self.petals.append(Petal(self.rng.uniform(24.0, self.w - 24.0), -20.0, speed,
                                     self.rng.uniform(12.0, 36.0), self.rng.uniform(0.0, 6.28), golden,
                                     self.rng.uniform(0.0, 360.0)))

        events: list[tuple[str, Petal | None]] = []
        head = self.pomo_y - POMO_SIZE * BODY_RY
        for petal in list(self.petals):
            petal.y += petal.vy * dt
            petal.phase += dt * 2.2
            petal.spin += dt * 90.0
            if head - 12 <= petal.y <= head + 20 and abs(petal.draw_x - self.pomo_x) <= POMO_SIZE * 0.55:
                self.petals.remove(petal)
                self.score += 3 if petal.golden else 1
                self.caught += 1
                events.append(("catch", petal))
            elif petal.y > self.h + 20:
                self.petals.remove(petal)
                events.append(("miss", petal))
        if self.time_left <= 0.0:
            self.time_left = 0.0
            self.running = False
            events.append(("end", None))
        return events

    def next_petal(self) -> Petal | None:
        """The petal Pomo should be watching: the lowest one still above him."""
        above = [p for p in self.petals if p.y < self.pomo_y]
        return max(above, key=lambda p: p.y, default=None)


class GameWindow(QWidget):
    WIDTH, HEIGHT = 420, 540

    def __init__(self, ctrl):
        super().__init__(None, Qt.Window)
        self.ctrl = ctrl
        self.setWindowTitle(f"{APP_NAME} · Petal Catch")
        self.setWindowIcon(ctrl.icon)
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.game = PetalGame(self.WIDTH, self.HEIGHT)
        self.pomo = Pomo()
        self.state = "ready"  # ready | playing | over
        self.new_best = False
        self._keys: set[int] = set()
        self._last = time.monotonic()
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    # ------------------------------------------------------------- lifecycle
    def showEvent(self, event) -> None:
        super().showEvent(event)
        set_dark_title_bar(self, self.ctrl.theme.dark)
        self._last = time.monotonic()
        self._timer.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._timer.stop()
        if self.state == "playing":
            self.game.running = False
            self.state = "ready"

    def _start(self) -> None:
        self.game.start()
        self.state = "playing"
        self.new_best = False
        self.pomo.hello()

    def _tick(self) -> None:
        now = time.monotonic()
        dt, self._last = now - self._last, now
        if self._keys:
            step = 420.0 * dt * ((Qt.Key_Right in self._keys) - (Qt.Key_Left in self._keys))
            self.game.move_to(self.game.target_x + step)
        for kind, petal in self.game.update(dt):
            if kind == "catch":
                self.pomo.squish.kick(3.5 if petal.golden else 2.5)
                self.pomo._burst("spark", 4 if petal.golden else 2, 1.2, 0.5)
                if petal.golden:
                    self.pomo._react("happy", 0.6)
            elif kind == "end":
                self._finish()
        s = self.ctrl.settings
        self.pomo.outfit = s.pomo_outfit
        self.pomo.listening = self.ctrl.pomo_listening()
        self.pomo.set_mood("focus" if self.state == "playing" else "idle")
        target = self.game.next_petal()
        anchor = QPointF(self.game.pomo_x, self.game.pomo_y)
        look = None
        if target is not None:
            look = ((target.draw_x - anchor.x()) / POMO_SIZE, (target.y - anchor.y()) / POMO_SIZE)
        self.pomo.update(dt, look)
        self.update()

    def _finish(self) -> None:
        self.state = "over"
        s = self.ctrl.settings
        if self.game.score > s.game_best:
            s.game_best = self.game.score
            self.ctrl.save_settings()
            self.new_best = True
            self.pomo.celebrate(big=True)
        else:
            self.pomo.celebrate()

    # ----------------------------------------------------------------- input
    def mouseMoveEvent(self, event) -> None:
        self.game.move_to(event.position().x())

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.state != "playing":
            self._start()

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (Qt.Key_Left, Qt.Key_Right):
            self._keys.add(key)
        elif key in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter) and self.state != "playing":
            self._start()
        elif key == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:
        self._keys.discard(event.key())

    # -------------------------------------------------------------- painting
    def _font(self, px: float, weight=QFont.Normal) -> QFont:
        f = QFont("Segoe UI")
        f.setPixelSize(round(px))
        f.setWeight(weight)
        return f

    def paintEvent(self, _event) -> None:
        t = self.ctrl.theme
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        w, h = self.width(), self.height()
        sky = QLinearGradient(0, 0, 0, h)
        sky.setColorAt(0.0, QColor(t.surface))
        sky.setColorAt(1.0, QColor(t.page))
        p.fillRect(self.rect(), sky)
        ground = QRectF(0, h - 34, w, 34)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(t.raised))
        p.drawRect(ground)
        p.setPen(QPen(QColor(t.orb_edge), 1))
        p.drawLine(QPointF(0, ground.top()), QPointF(w, ground.top()))

        for petal in self.game.petals:
            self._paint_petal(p, petal)
        self.pomo.paint(p, QPointF(self.game.pomo_x, self.game.pomo_y), POMO_SIZE)

        # HUD: score, time, best
        draw_glyph(p, "petal", QPointF(26, 26), 22, QColor(t.ink_2))
        p.setPen(QColor(t.ink))
        p.setFont(self._font(20, QFont.DemiBold))
        p.drawText(QRectF(42, 10, 120, 32), Qt.AlignLeft | Qt.AlignVCenter, str(self.game.score))
        secs = math.ceil(self.game.time_left)
        p.drawText(QRectF(w - 130, 10, 116, 32), Qt.AlignRight | Qt.AlignVCenter, f"0:{secs:02d}")
        p.setFont(self._font(12))
        p.setPen(QColor(t.muted))
        p.drawText(QRectF(0, 14, w, 24), Qt.AlignHCenter | Qt.AlignVCenter, f"Best {self.ctrl.settings.game_best}")

        if self.state != "playing":
            self._paint_overlay(p, t, w, h)
        p.end()

    def _paint_petal(self, p: QPainter, petal: Petal) -> None:
        x, y, size = petal.draw_x, petal.y, 15.0 if not petal.golden else 18.0
        p.save()
        p.translate(x, y)
        p.rotate(petal.spin)
        shape = QPainterPath(QPointF(0, size * 0.55))
        shape.cubicTo(-size * 0.6, size * 0.1, -size * 0.45, -size * 0.6, -size * 0.12, -size * 0.48)
        shape.lineTo(0, -size * 0.3)
        shape.lineTo(size * 0.12, -size * 0.48)
        shape.cubicTo(size * 0.45, -size * 0.6, size * 0.6, size * 0.1, 0, size * 0.55)
        p.setPen(QPen(OUTLINE, 1.2))
        p.setBrush(PETAL_GOLD if petal.golden else PETAL_PINK)
        p.drawPath(shape)
        p.restore()
        if petal.golden:  # a little glint
            p.setPen(QPen(QColor(255, 255, 255, 200), 1.5))
            p.drawLine(QPointF(x + 10, y - 12), QPointF(x + 10, y - 6))
            p.drawLine(QPointF(x + 7, y - 9), QPointF(x + 13, y - 9))

    def _paint_overlay(self, p: QPainter, t, w: int, h: int) -> None:
        card = QRectF(40, 120, w - 80, 190)
        p.setPen(QPen(QColor(t.orb_edge), 1))
        p.setBrush(QColor(t.surface))
        p.drawRoundedRect(card, 18, 18)
        p.setPen(QColor(t.ink))
        p.setFont(self._font(22, QFont.DemiBold))
        if self.state == "ready":
            title = "Petal Catch"
            lines = ["Catch the falling sakura petals with Pomo.", "Golden petals are worth 3.",
                     "Move with your mouse or the arrow keys."]
            if self.ctrl.engine.is_running and not self.ctrl.engine.mode.is_break:
                lines = ["Psst, it's focus time.", "Maybe save this for your break?", ""]
            action = "Click to play"
        else:
            title = "New best!" if self.new_best else "Time's up!"
            lines = [f"You caught {self.game.caught} petals for {self.game.score} points.",
                     f"Best: {self.ctrl.settings.game_best}", ""]
            action = "Click to play again"
        p.drawText(QRectF(card.left(), card.top() + 18, card.width(), 30), Qt.AlignCenter, title)
        p.setFont(self._font(13))
        p.setPen(QColor(t.ink_2))
        for i, line in enumerate(lines):
            p.drawText(QRectF(card.left(), card.top() + 62 + i * 22, card.width(), 22), Qt.AlignCenter, line)
        chip = QRectF(card.center().x() - 80, card.bottom() - 46, 160, 32)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(t.focus))
        p.drawRoundedRect(chip, 16, 16)
        p.setPen(QColor(t.on_accent))
        p.setFont(self._font(13, QFont.DemiBold))
        p.drawText(chip, Qt.AlignCenter, action)
