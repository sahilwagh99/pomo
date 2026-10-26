"""Mochi Stack: a break-time game. A mochi glides back and forth above the tower; drop it so it
lands on the one below. Land it close and the tower grows (dead center is a perfect drop, worth
2); miss by more than half a mochi and it tumbles off, and the round is over. Each mochi glides
a little faster than the last.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen

from .pomo import BODY_RY, OUTLINE, Pomo, Spring

MOCHI_W = 78.0
MOCHI_H = 40.0          # each mochi adds this much height to the tower
PLATE_Y = 70.0          # the plate's distance from the bottom of the window
PERFECT_PX = 6.0
LAND_SHARE = 0.55       # land within this share of a mochi's width of the one below
FLAVORS = (("matcha", "#d7ecbd", "#bad898"), ("sakura", "#fbd0d7", "#f2b3c0"), ("ube", "#dcd0f2", "#c4b4e8"),
           ("yuzu", "#fbe7a6", "#f2d47d"), ("milk", "#fffaf0", "#eadfcc"))


@dataclass
class Mochi:
    x: float
    flavor: int
    squish: Spring = field(default_factory=lambda: Spring(300.0, 11.0))


@dataclass
class Faller:
    """A mochi off the edge, tumbling away."""
    x: float
    y: float
    vx: float
    vy: float
    spin: float
    flavor: int


class MochiStack:
    """The game's state and rules, without widgets (so it can be tested on its own)."""

    def __init__(self, width: float, height: float, rng: random.Random | None = None):
        self.w, self.h = width, height
        self.rng = rng or random.Random()
        self.reset()

    def reset(self) -> None:
        self.tower: list[Mochi] = [Mochi(self.w / 2, 0)]
        self.score = 0
        self.perfects = 0
        self.running = False
        self.camera = 0.0
        self.fallers: list[Faller] = []
        self._next()

    def start(self) -> None:
        self.reset()
        self.running = True

    @property
    def height(self) -> int:
        """Mochis stacked on the first one."""
        return len(self.tower) - 1

    @property
    def speed(self) -> float:
        return min(430.0, 130.0 + 16.0 * self.height)

    def _next(self) -> None:
        self.x = self.rng.uniform(MOCHI_W, self.w - MOCHI_W)
        self.dir = self.rng.choice((-1, 1))
        self.flavor = (self.tower[-1].flavor + self.rng.randint(1, len(FLAVORS) - 1)) % len(FLAVORS)
        self.dropping = False
        self.drop_y = 0.0     # how far it has fallen
        self.drop_v = 0.0

    def glide_height(self) -> float:
        """How far above the top of the tower the gliding mochi rides."""
        return MOCHI_H * 3.2

    def drop(self) -> bool:
        if not self.running or self.dropping:
            return False
        self.dropping = True
        self.drop_v = 0.0
        return True

    def update(self, dt: float) -> list[str]:
        """Advance; returns events: 'land', 'perfect', 'miss', 'end'."""
        dt = max(0.0, min(dt, 0.05))
        events: list[str] = []
        for m in self.tower[-6:]:
            m.squish.step(dt)
        for f in list(self.fallers):
            f.vy += 1400.0 * dt
            f.x += f.vx * dt
            f.y -= f.vy * dt
            f.spin += f.vx * dt * 0.8
            if f.y < -self.h:
                self.fallers.remove(f)
        target = max(0.0, (self.height - 5) * MOCHI_H)
        self.camera += (target - self.camera) * (1 - math.exp(-dt * 5.0))
        if not self.running:
            return events
        if not self.dropping:
            half = MOCHI_W / 2 + 6
            self.x += self.dir * self.speed * dt
            if self.x < half or self.x > self.w - half:
                self.x = max(half, min(self.w - half, self.x))
                self.dir = -self.dir
            return events
        self.drop_v += 1600.0 * dt
        self.drop_y += self.drop_v * dt
        if self.drop_y < self.glide_height():
            return events
        top = self.tower[-1]
        dx = self.x - top.x
        if abs(dx) <= MOCHI_W * LAND_SHARE:
            perfect = abs(dx) <= PERFECT_PX
            mochi = Mochi(top.x if perfect else self.x, self.flavor)
            mochi.squish.kick(min(9.0, self.drop_v / 120.0))
            top.squish.kick(3.0)
            self.tower.append(mochi)
            self.score += 2 if perfect else 1
            self.perfects += perfect
            events.append("perfect" if perfect else "land")
            self._next()
        else:
            side = 1 if dx > 0 else -1
            base = PLATE_Y + self.height * MOCHI_H + MOCHI_H
            self.fallers.append(Faller(self.x, base, side * 160.0, -150.0, 0.0, self.flavor))  # a hop, then down
            self.running = False
            events += ["miss", "end"]
        return events

    def gliding_y(self) -> float:
        """The gliding (or dropping) mochi's bottom, measured up from the plate."""
        return (self.height + 1) * MOCHI_H + self.glide_height() - self.drop_y


def paint_mochi(p: QPainter, center: QPointF, w: float, h: float, flavor: int, squish: float = 0.0,
                face: bool = True, spin: float = 0.0) -> None:
    """A round mochi with a sleepy little face."""
    _name, body, shade = FLAVORS[flavor % len(FLAVORS)]
    q = max(-0.4, min(0.4, squish))
    w, h = w * (1 + 0.22 * q), h * (1 - 0.25 * q)
    p.save()
    p.translate(center)
    p.rotate(spin)
    path = QPainterPath(QPointF(0, h / 2))
    path.cubicTo(w * 0.32, h / 2, w / 2, h * 0.38, w / 2, h * 0.08)
    path.cubicTo(w / 2, -h * 0.36, w * 0.3, -h / 2, 0, -h / 2)
    path.cubicTo(-w * 0.3, -h / 2, -w / 2, -h * 0.36, -w / 2, h * 0.08)
    path.cubicTo(-w / 2, h * 0.38, -w * 0.32, h / 2, 0, h / 2)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(shade))
    p.drawPath(path)
    p.save()
    p.setClipPath(path)
    p.setBrush(QColor(body))
    p.drawEllipse(QPointF(-w * 0.04, -h * 0.08), w * 0.5, h * 0.48)
    p.restore()
    p.setPen(QPen(OUTLINE, 1.6))
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255, 200))
    p.drawEllipse(QPointF(-w * 0.28, -h * 0.18), w * 0.05, h * 0.09)
    if face:
        p.setPen(QPen(OUTLINE, 1.5, Qt.SolidLine, Qt.RoundCap))
        for side in (-1, 1):
            p.drawArc(QRectF(side * w * 0.14 - 3, -h * 0.02 - 2, 6, 4), 200 * 16, 140 * 16)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#f7b0b4"))
        for side in (-1, 1):
            p.drawEllipse(QPointF(side * w * 0.26, h * 0.12), w * 0.06, h * 0.06)
    p.restore()


class StackView:
    """Mochi Stack inside the game window."""

    key = "stack"
    title = "Mochi Stack"
    blurb = ("Drop each mochi onto the tower.", "A dead-center drop is worth 2.", "Click or press Space to drop.")

    def __init__(self, host):
        self.host = host
        self.game = MochiStack(host.WIDTH, host.HEIGHT)
        self.pomo = Pomo()
        self.state = "ready"
        self.new_best = False
        self._flash = 0.0

    def best_text(self) -> str:
        best = self.host.ctrl.settings.stack_best
        return f"Best tower: {best}" if best else "No tower yet"

    def start(self) -> None:
        self.game.start()
        self.state = "playing"
        self.new_best = False
        self.pomo.hello()

    def stop(self) -> None:
        if self.state == "playing":
            self.game.running = False
            self.state = "ready"

    def tick(self, dt: float) -> None:
        for event in self.game.update(dt):
            if event == "perfect":
                self._flash = 0.6
                self.pomo._react("happy", 0.8)
                self.pomo._burst("spark", 5, 1.4, 0.6)
            elif event == "land":
                self.pomo.squish.kick(2.0)
            elif event == "end":
                self._finish()
        self._flash = max(0.0, self._flash - dt)
        ctrl = self.host.ctrl
        self.pomo.outfit = ctrl.current_outfit()
        self.pomo.set_mood("focus" if self.state == "playing" else "idle")
        anchor = self._pomo_anchor()
        target = QPointF(self.game.x, self._screen_y(self.game.gliding_y()))
        self.pomo.update(dt, ((target.x() - anchor.x()) / 50.0, (target.y() - anchor.y()) / 50.0))

    def _finish(self) -> None:
        self.state = "over"
        s = self.host.ctrl.settings
        if self.game.score > s.stack_best:
            s.stack_best = self.game.score
            self.host.ctrl.save_settings()
            self.new_best = True
            self.pomo.celebrate(big=True)
        else:
            self.pomo._react("ouch", 1.0)
        self.host.round_over(self.key)

    def _screen_y(self, height: float) -> float:
        return self.host.HEIGHT - PLATE_Y - height + self.game.camera

    def _pomo_anchor(self) -> QPointF:
        return QPointF(42, self.host.HEIGHT - 28 - 50 * BODY_RY)

    def mouse_press(self, pos: QPointF) -> None:
        if self.state == "playing":
            self.game.drop()
        else:
            self.start()

    def mouse_move(self, pos: QPointF) -> None:
        pass

    def key_press(self, key) -> bool:
        if key in (Qt.Key_Space, Qt.Key_Down, Qt.Key_Return, Qt.Key_Enter):
            self.mouse_press(QPointF())
            return True
        return False

    def key_release(self, key) -> None:
        pass

    def paint(self, p: QPainter, t, w: int, h: int) -> None:
        g = self.game
        plate_y = h - PLATE_Y + g.camera
        p.setPen(QPen(QColor(t.orb_edge), 1.5))
        p.setBrush(QColor(t.raised))
        p.drawRoundedRect(QRectF(w / 2 - 70, plate_y - 2, 140, 12), 6, 6)
        for i, m in enumerate(g.tower):
            y = plate_y - i * MOCHI_H - MOCHI_H / 2
            if y < -MOCHI_H or y > h + MOCHI_H:
                continue
            paint_mochi(p, QPointF(m.x, y), MOCHI_W, MOCHI_H + 6, m.flavor, m.squish.x, face=i == len(g.tower) - 1)
        for f in g.fallers:
            paint_mochi(p, QPointF(f.x, h - f.y + g.camera), MOCHI_W, MOCHI_H + 6, f.flavor, spin=f.spin)
        if g.running:
            y = self._screen_y(g.gliding_y()) - MOCHI_H / 2
            if not g.dropping:  # a faint guide straight down
                guide = QColor(t.muted)
                guide.setAlpha(70)
                p.setPen(QPen(guide, 1, Qt.DashLine))
                p.drawLine(QPointF(g.x, y + MOCHI_H / 2 + 4), QPointF(g.x, self._screen_y((g.height + 1) * MOCHI_H)))
            paint_mochi(p, QPointF(g.x, y), MOCHI_W, MOCHI_H + 6, g.flavor)
        self.pomo.paint(p, self._pomo_anchor(), 50)
        if self._flash > 0:
            p.setPen(QColor(t.ink))
            font = QFont("Segoe UI")
            font.setPixelSize(18)
            font.setWeight(QFont.DemiBold)
            p.setFont(font)
            p.setOpacity(min(1.0, self._flash / 0.3))
            p.drawText(QRectF(0, 70, w, 30), Qt.AlignCenter, "Perfect! +2")
            p.setOpacity(1.0)

    def hud(self) -> tuple[str, str]:
        return str(self.game.score), f"{self.game.height} high"

    def overlay(self) -> tuple[str, list[str], str]:
        s = self.host.ctrl.settings
        if self.state == "ready":
            return self.title, list(self.blurb), "Click to play"
        title = "New best!" if self.new_best else "It tumbled!"
        return title, [f"A tower of {self.game.height} for {self.game.score} points.",
                       f"{self.game.perfects} perfect drops  ·  Best: {s.stack_best}", ""], "Click to play again"
