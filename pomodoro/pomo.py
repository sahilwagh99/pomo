"""Pomo, the focus buddy: a little matcha mochi with a sakura blossom, drawn and
animated entirely in code.

Pomo is an original character. The idea of a companion that reacts to what
you are doing was inspired by Coucou (github.com/Louis-CFM/coucou), but no
Coucou code, artwork or sounds are used here.

Units: Pomo's simulation works in "body sizes" (1.0 = his width), so the same
character can be painted at any size. Screen y grows downwards.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen

from .engine import State

BODY = QColor("#d7ecbd")         # matcha mochi
BODY_SHADE = QColor("#bad898")
OUTLINE = QColor("#5b2c29")      # cocoa line art
EYE = QColor("#4a2420")
MOUTH = QColor("#7a3a35")
TONGUE = QColor("#f4a3a8")
SHINE = QColor(255, 255, 255, 235)
BLUSH = QColor("#f7b0b4")
BLUSH_LINE = QColor("#e8838d")
BLUSH_ANGRY = QColor("#f28a8a")
PETAL = QColor("#fbd0d7")
PETAL_HEART = QColor("#f6e2a0")
SPARK = QColor("#f5d77e")
SNOOZE = QColor("#a7b3e3")
ANGER = QColor("#e8707c")
SHADOW = QColor(63, 42, 38)
CONFETTI = ("#f0a3b1", "#95c283", "#f5d77e", "#a7b3e3", "#f7c59f")

BODY_RY = 0.40         # half-height of the body, in body widths
POKE_WINDOW = 2.5      # seconds in which pokes add up
GRAVITY = 9.0          # body sizes / s^2
SLEEPY_AFTER = 180.0   # idle seconds before Pomo dozes off
MAX_PARTICLES = 80

# face per mood/reaction: (eyes, mouth, brows)
FACES = {
    "idle": ("open", "smile", None),
    "focus": ("narrow", "flat", "focus"),
    "break": ("happy", "open", None),
    "paused": ("half", "meh", None),
    "ready": ("big", "o", None),
    "sleepy": ("sleep", "snore", None),
    "happy": ("happy", "grin", None),
    "ouch": ("squeeze", "wobble", None),
    "grumpy": ("open", "frown", "angry"),
    "dizzy": ("spiral", "wobble", None),
}


def mood_for(state: State, is_break: bool, idle_seconds: float) -> str:
    """Pomo's resting mood for the current timer state."""
    if state is State.RUNNING:
        return "break" if is_break else "focus"
    if state is State.PAUSED:
        return "paused"
    if state is State.FINISHED:
        return "ready"
    return "sleepy" if idle_seconds >= SLEEPY_AFTER else "idle"


class Spring:
    """Damped spring around 0, used for squash-and-stretch and the bobbing blossom."""

    def __init__(self, stiffness: float, damping: float):
        self.k, self.d = stiffness, damping
        self.x = 0.0
        self.v = 0.0

    def kick(self, velocity: float) -> None:
        self.v += velocity

    def step(self, dt: float) -> None:
        steps = int(dt / 0.008) + 1  # sub-step so long frames stay stable
        h = dt / steps
        for _ in range(steps):
            self.v += (-self.k * self.x - self.d * self.v) * h
            self.x += self.v * h


@dataclass
class Particle:
    kind: str  # spark | confetti | z | anger
    x: float
    y: float
    vx: float
    vy: float
    life: float
    age: float = 0.0
    spin: float = 0.0
    color: QColor = field(default_factory=lambda: QColor(SPARK))


class Pomo:
    def __init__(self, rng: random.Random | None = None):
        self.rng = rng or random.Random()
        self.mood = "idle"
        self.reaction: str | None = None
        self.t = 0.0
        self._reaction_until = 0.0
        self._pokes: list[float] = []
        self._blink_start = -1.0
        self._next_blink = 1.2
        self._double_blink = False
        self.ex = 0.0  # where the eyes look, -1..1
        self.ey = 0.0
        self._eye_speed = 0.0
        self._wander = (0.0, 0.0)
        self._next_wander = 0.0
        self._last_cursor: tuple[float, float] | None = None
        self._cursor_moved_at = -100.0
        self.squish = Spring(320.0, 13.0)  # + squash, - stretch
        self.leaf = Spring(140.0, 5.0)     # blossom tilt, degrees
        self.hop_y = 0.0
        self.hop_v = 0.0
        self._queued_hops: list[tuple[float, float]] = []
        self._next_fidget = 3.0
        self.particles: list[Particle] = []
        self.signal: str | None = None  # coding agents: "busy" (thought bubble) or "alert" ("!")

    # ---------------------------------------------------------------- events
    @property
    def face(self) -> str:
        return self.reaction or self.mood

    def set_mood(self, mood: str) -> None:
        if mood == self.mood:
            return
        previous, self.mood = self.mood, mood
        self._next_fidget = self.t + 0.4
        if mood in ("focus", "break") and previous in ("idle", "sleepy", "paused", "ready"):
            self.hop(1.5)
        self.leaf.kick(70.0)

    def hop(self, strength: float = 2.0) -> None:
        if self.hop_y > 0.0 or self.hop_v > 0.0:
            self._queued_hops.append((self.t + 0.05, strength))
            return
        self.hop_v = strength
        self.hop_y = 1e-4
        self.squish.kick(-4.0)  # stretch on take-off

    def celebrate(self, big: bool = False) -> None:
        self._react("happy", 2.4 if big else 1.6)
        self.hop(2.6 if big else 2.2)
        self._burst("spark", 7, 1.7, 0.75)
        if big:
            self._queued_hops.append((self.t + 0.8, 2.3))
            self._burst("confetti", 28, 2.8, 1.8)
        self.leaf.kick(180.0)

    def hello(self) -> None:
        self._react("happy", 1.1)
        self.hop(2.0)
        self._burst("spark", 4, 1.3, 0.6)

    def poke(self) -> str:
        now = self.t
        self._pokes = [p for p in self._pokes if now - p < POKE_WINDOW] + [now]
        self.squish.kick(7.0)
        self.leaf.kick(-140.0)
        if self.reaction == "dizzy":
            self._reaction_until = max(self._reaction_until, now + 1.0)
        elif len(self._pokes) >= 6:
            self._react("dizzy", 2.8)
            self._pokes.clear()
        elif len(self._pokes) >= 3:
            self._react("grumpy", 1.6)
            if not any(p.kind == "anger" for p in self.particles):
                self.particles.append(Particle("anger", -0.42, -0.36, 0.0, 0.0, 1.5))
        else:
            self._react("ouch", 0.5)
        return self.reaction or ""

    def _react(self, name: str, seconds: float) -> None:
        self.reaction = name
        self._reaction_until = self.t + seconds

    def _burst(self, kind: str, count: int, speed: float, life: float) -> None:
        for _ in range(count):
            angle = self.rng.uniform(-math.pi * 0.92, -math.pi * 0.08)  # upwards fan
            v = speed * self.rng.uniform(0.55, 1.0)
            color = QColor(self.rng.choice(CONFETTI)) if kind == "confetti" else QColor(SPARK)
            self.particles.append(Particle(
                kind, self.rng.uniform(-0.2, 0.2), -0.3, math.cos(angle) * v, math.sin(angle) * v,
                life * self.rng.uniform(0.7, 1.15), spin=self.rng.uniform(0, 6.28), color=color))
        del self.particles[:-MAX_PARTICLES]

    # ------------------------------------------------------------ simulation
    def update(self, dt: float, cursor: tuple[float, float] | None = None) -> None:
        """Advance by dt seconds. cursor: pointer position relative to Pomo, in body sizes."""
        dt = max(0.0, min(dt, 0.1))
        self.t += dt
        t = self.t
        if self.reaction and t >= self._reaction_until:
            self.reaction = None

        self._update_eyes(dt, cursor)
        self._update_blink()

        for item in list(self._queued_hops):
            if t >= item[0] and self.hop_y <= 0.0 and self.hop_v <= 0.0:
                self._queued_hops.remove(item)
                self.hop(item[1])
        if self.hop_y > 0.0 or self.hop_v > 0.0:
            self.hop_v -= GRAVITY * dt
            self.hop_y += self.hop_v * dt
            if self.hop_y <= 0.0:
                impact = -self.hop_v
                self.hop_y = self.hop_v = 0.0
                self.squish.kick(impact * 2.6)  # squash on landing
                self.leaf.kick(impact * 45.0)

        if self.reaction is None and t >= self._next_fidget:
            self._fidget()

        self.squish.step(dt)
        self.leaf.step(dt)
        self._update_particles(dt)

    def _update_eyes(self, dt: float, cursor: tuple[float, float] | None) -> None:
        t = self.t
        if cursor is not None:
            last = self._last_cursor
            if last is None or abs(cursor[0] - last[0]) + abs(cursor[1] - last[1]) > 0.02:
                self._cursor_moved_at = t
            self._last_cursor = cursor
        if cursor is not None and t - self._cursor_moved_at < 4.0:
            dx, dy = cursor
            dist = math.hypot(dx, dy) or 1.0
            reach = min(1.0, dist / 3.0)
            target = (dx / dist * reach, dy / dist * reach)
        else:
            if t >= self._next_wander:
                self._next_wander = t + self.rng.uniform(1.5, 4.0)
                self._wander = ((self.rng.uniform(-0.7, 0.7), self.rng.uniform(-0.4, 0.5))
                                if self.rng.random() < 0.7 else (0.0, 0.0))
            target = self._wander
        k = 1.0 - math.exp(-dt * 12.0)
        dx, dy = (target[0] - self.ex) * k, (target[1] - self.ey) * k
        self.ex += dx
        self.ey += dy
        self._eye_speed = abs(dx) + abs(dy)

    def _update_blink(self) -> None:
        t = self.t
        if self._blink_start >= 0.0:
            if t - self._blink_start >= 0.15:
                self._blink_start = -1.0
                if self._double_blink:
                    self._double_blink = False
                    self._next_blink = t + 0.1
                else:
                    self._next_blink = t + self.rng.uniform(2.2, 5.5)
        elif t >= self._next_blink:
            self._blink_start = t
            self._double_blink = self.rng.random() < 0.15

    def is_calm(self) -> bool:
        """True when only slow breathing is going on, so a low frame rate looks the same."""
        return (self.reaction is None and not self.particles and not self._queued_hops
                and self.signal != "alert"  # the "!" pulses; approvals are short-lived
                and self.hop_y <= 0.0 and self.hop_v <= 0.0
                and abs(self.squish.x) + abs(self.squish.v) < 0.03
                and abs(self.leaf.x) + abs(self.leaf.v) < 1.5
                and self._eye_speed < 0.004
                and self._blink_start < 0.0 and self._next_blink - self.t > 0.12
                and self._next_fidget - self.t > 0.12)

    @property
    def blink(self) -> float:
        if self._blink_start < 0.0:
            return 0.0
        phase = (self.t - self._blink_start) / 0.15
        return max(0.0, 1.0 - abs(2.0 * phase - 1.0))

    def _fidget(self) -> None:
        mood = self.mood
        if mood == "ready":
            self.hop(1.3)
            self._next_fidget = self.t + 1.2
        elif mood == "sleepy":
            self.particles.append(Particle("z", 0.34, -0.42, 0.22, -0.32, 2.2))
            self._next_fidget = self.t + 1.8
        else:
            roll = self.rng.random()
            if mood == "idle" and roll < 0.3:
                self.hop(1.2)
            elif roll < 0.7:
                self.leaf.kick(self.rng.choice((-1, 1)) * 90.0)
            self._next_fidget = self.t + self.rng.uniform(5.0, 10.0)

    def _update_particles(self, dt: float) -> None:
        alive = []
        for p in self.particles:
            p.age += dt
            if p.age >= p.life:
                continue
            if p.kind == "confetti":
                p.vy += 3.4 * dt
                p.vx *= 1.0 - 0.8 * dt
            elif p.kind == "spark":
                p.vx *= 1.0 - 2.5 * dt
                p.vy *= 1.0 - 2.5 * dt
            p.x += p.vx * dt
            p.y += p.vy * dt
            p.spin += dt * 7.0
            alive.append(p)
        self.particles = alive

    # --------------------------------------------------------------- drawing
    def body_center(self, anchor: QPointF, size: float) -> QPointF:
        """Where Pomo's body is drawn right now (anchor = his resting center)."""
        sy = self._scale()[1]
        ry = size * BODY_RY
        return QPointF(anchor.x(), anchor.y() + ry - ry * sy - self.hop_y * size)

    def _scale(self) -> tuple[float, float]:
        sleepy = self.mood == "sleepy" and self.reaction is None
        rate, depth = (0.18, 0.03) if sleepy else (0.3, 0.018)
        breath = math.sin(self.t * 2.0 * math.pi * rate) * depth
        q = max(-0.45, min(0.45, self.squish.x))
        return 1.0 + 0.25 * q - breath * 0.6, 1.0 - 0.3 * q + breath

    def paint(self, p: QPainter, anchor: QPointF, size: float, ground_shadow: bool = True) -> None:
        s = size
        sx, sy = self._scale()
        rx, ry = s * 0.5 * sx, s * BODY_RY * sy
        base_bottom = anchor.y() + s * BODY_RY
        c = self.body_center(anchor, s)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)

        if ground_shadow:
            lift = max(0.25, 1.0 - self.hop_y * 2.2)
            shadow = QColor(SHADOW)
            shadow.setAlpha(int(55 * lift))
            p.setPen(Qt.NoPen)
            p.setBrush(shadow)
            p.drawEllipse(QPointF(anchor.x(), base_bottom + s * 0.012), s * 0.4 * lift, s * 0.05 * lift)

        p.save()
        if self.face == "dizzy":
            p.translate(c.x(), base_bottom)
            p.rotate(math.sin(self.t * 9.0) * 6.0)
            p.translate(-c.x(), -base_bottom)
        self._paint_body(p, c, rx, ry, s)
        self._paint_face(p, c, rx, ry, s)
        self._paint_blossom(p, QPointF(c.x() + rx * 0.56, c.y() - ry * 0.74), s)
        p.restore()

        if self.signal:
            self._paint_bubble(p, c, rx, ry, s)
        self._paint_particles(p, anchor, s)
        p.restore()

    def _paint_bubble(self, p: QPainter, c: QPointF, rx: float, ry: float, s: float) -> None:
        """A thought bubble over his head: three cycling dots while agents work, '!' when one needs you."""
        bw, bh = s * 0.42, s * 0.26
        center = QPointF(c.x() - rx * 0.62, c.y() - ry * 1.45)
        line = QPen(OUTLINE, max(0.9, s * 0.022))
        p.setPen(line)
        p.setBrush(QColor("#fffdf8"))
        for k, r in ((0.0, 0.035), (1.0, 0.05)):  # little trail toward his head
            p.drawEllipse(QPointF(c.x() - rx * (0.3 + 0.12 * k), c.y() - ry * (0.98 + 0.18 * k)), s * r, s * r)
        p.drawRoundedRect(QRectF(center.x() - bw / 2, center.y() - bh / 2, bw, bh), bh / 2, bh / 2)
        p.setPen(Qt.NoPen)
        if self.signal == "alert":
            pulse = 1.0 + 0.12 * math.sin(self.t * 8.0)
            p.setBrush(ANGER)
            w, h = s * 0.035 * pulse, s * 0.1 * pulse
            p.drawRoundedRect(QRectF(center.x() - w / 2, center.y() - h * 0.62, w, h * 0.78), w / 2, w / 2)
            p.drawEllipse(QPointF(center.x(), center.y() + h * 0.38), w * 0.55, w * 0.55)
            return
        lit = int(self.t * 2.5) % 3
        for i in range(3):
            color = QColor(OUTLINE if i == lit else "#c9b8ae")
            p.setBrush(color)
            p.drawEllipse(QPointF(center.x() + (i - 1) * bw * 0.26, center.y()), s * 0.028, s * 0.028)

    @staticmethod
    def _body_path(x: float, y: float, rx: float, ry: float) -> QPainterPath:
        # a mochi dome: broad round top, softly flattened bottom, widest just below the middle
        path = QPainterPath(QPointF(x, y + ry))
        path.cubicTo(x + rx * 0.62, y + ry, x + rx, y + ry * 0.86, x + rx, y + ry * 0.28)
        path.cubicTo(x + rx, y - ry * 0.52, x + rx * 0.58, y - ry, x, y - ry)
        path.cubicTo(x - rx * 0.58, y - ry, x - rx, y - ry * 0.52, x - rx, y + ry * 0.28)
        path.cubicTo(x - rx, y + ry * 0.86, x - rx * 0.62, y + ry, x, y + ry)
        return path

    def _paint_body(self, p: QPainter, c: QPointF, rx: float, ry: float, s: float) -> None:
        x, y = c.x(), c.y()
        body = self._body_path(x, y, rx, ry)
        # shade first, then the lit body nudged up-left: a soft crescent stays along the bottom right
        p.setPen(Qt.NoPen)
        p.setBrush(BODY_SHADE)
        p.drawPath(body)
        p.save()
        p.setClipPath(body)
        p.setBrush(BODY)
        p.drawPath(self._body_path(x - rx * 0.07, y - ry * 0.1, rx * 0.97, ry * 0.97))
        p.restore()
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(OUTLINE, max(1.2, s * 0.036), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(body)

        # glossy shine: a tilted oval and a dot on the upper left
        p.setPen(Qt.NoPen)
        p.setBrush(SHINE)
        p.save()
        p.translate(x - rx * 0.58, y - ry * 0.16)
        p.rotate(32)
        p.drawEllipse(QPointF(0, 0), rx * 0.11, ry * 0.26)
        p.restore()
        p.drawEllipse(QPointF(x - rx * 0.38, y - ry * 0.6), s * 0.038, s * 0.038)

    def _paint_face(self, p: QPainter, c: QPointF, rx: float, ry: float, s: float) -> None:
        eyes, mouth, brows = FACES.get(self.face, FACES["idle"])
        lx, ly = self.ex * rx * 0.1, self.ey * ry * 0.1
        if eyes in ("happy", "sleep", "squeeze", "spiral"):
            lx *= 0.4
            ly *= 0.4
        x, y = c.x(), c.y()

        # striped blush
        angry = self.face == "grumpy"
        bw, bh = rx * 0.16, ry * 0.12
        for side in (-1, 1):
            bc = QPointF(x + side * rx * 0.6 + lx * 0.5, y + ry * 0.38 + ly * 0.5)
            p.setPen(Qt.NoPen)
            p.setBrush(BLUSH_ANGRY if angry else BLUSH)
            p.drawEllipse(bc, bw, bh)
            p.setPen(QPen(BLUSH_LINE, max(0.7, s * 0.012), Qt.SolidLine, Qt.RoundCap))
            for i in (-1, 0, 1):
                ox = bc.x() + i * bw * 0.45
                p.drawLine(QPointF(ox + bw * 0.12, bc.y() - bh * 0.45), QPointF(ox - bw * 0.12, bc.y() + bh * 0.45))

        r = s * 0.064
        for side in (-1, 1):
            center = QPointF(x + side * rx * 0.34 + lx, y + ry * 0.1 + ly)
            self._paint_eye(p, eyes, center, r, side, s)
            if brows:
                self._paint_brow(p, brows, center, r, side, s)
        self._paint_mouth(p, mouth, QPointF(x + lx * 0.5, y + ry * 0.34 + ly * 0.5), s)

    def _paint_eye(self, p: QPainter, kind: str, c: QPointF, r: float, side: int, s: float) -> None:
        line = QPen(EYE, max(1.0, s * 0.034), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        x, y = c.x(), c.y()
        if kind in ("open", "narrow", "big", "half"):
            scale_r, scale_v = {"open": (1.0, 1.0), "narrow": (1.0, 0.8), "big": (1.14, 1.06), "half": (1.0, 0.5)}[kind]
            er = r * scale_r
            ev = er * scale_v * (1.0 - 0.9 * self.blink)
            if kind == "half":
                y += r * 0.35
            p.setPen(Qt.NoPen)
            p.setBrush(EYE)
            p.drawEllipse(QPointF(x, y), er, max(ev, s * 0.008))
            if kind == "half":
                p.setPen(line)
                p.drawLine(QPointF(x - er * 1.15, y - ev), QPointF(x + er * 1.15, y - ev))
            elif ev > er * 0.5:
                p.setBrush(QColor(255, 255, 255, 240))
                p.drawEllipse(QPointF(x - er * 0.32, y - ev * 0.36), er * 0.36, er * 0.36)
                p.drawEllipse(QPointF(x + er * 0.34, y + ev * 0.34), er * 0.14, er * 0.14)
            return
        p.setPen(line)
        p.setBrush(Qt.NoBrush)
        path = QPainterPath()
        w = r * 1.1
        if kind == "happy":    # closed, curving up
            path.moveTo(x - w, y + r * 0.3)
            path.quadTo(x, y - r * 1.1, x + w, y + r * 0.3)
        elif kind == "sleep":  # closed, curving down
            path.moveTo(x - w, y)
            path.quadTo(x, y + r * 0.9, x + w, y)
        elif kind == "squeeze":  # > <
            tip = x - side * w * 0.55
            back = x + side * w * 0.8
            path.moveTo(back, y - r * 0.75)
            path.lineTo(tip, y)
            path.lineTo(back, y + r * 0.75)
        elif kind == "spiral":
            turn = self.t * 8.0 * side
            for i in range(26):
                a = i * 0.42 + turn
                rr = r * (0.1 + i * 0.036)
                point = QPointF(x + math.cos(a) * rr, y + math.sin(a) * rr)
                path.moveTo(point) if i == 0 else path.lineTo(point)
            line.setWidthF(max(0.8, s * 0.022))
            p.setPen(line)
        p.drawPath(path)

    def _paint_brow(self, p: QPainter, kind: str, c: QPointF, r: float, side: int, s: float) -> None:
        p.setPen(QPen(EYE, max(1.0, s * 0.03), Qt.SolidLine, Qt.RoundCap))
        outer, inner = (1.75, 1.4) if kind == "focus" else (2.1, 1.2)
        p.drawLine(QPointF(c.x() + side * r * 1.2, c.y() - r * outer),
                   QPointF(c.x() - side * r * 0.9, c.y() - r * inner))

    def _paint_mouth(self, p: QPainter, kind: str, c: QPointF, s: float) -> None:
        x, y = c.x(), c.y()
        w = s * 0.13
        line = QPen(OUTLINE, max(1.0, s * 0.03), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        path = QPainterPath()
        if kind in ("open", "grin"):
            k = 1.0 if kind == "open" else 1.25
            path.moveTo(x - w * 0.42 * k, y - s * 0.012)
            path.quadTo(x, y + s * 0.1 * k, x + w * 0.42 * k, y - s * 0.012)
            path.closeSubpath()
            p.setPen(QPen(OUTLINE, max(0.8, s * 0.022), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(MOUTH)
            p.drawPath(path)
            p.save()
            p.setClipPath(path)
            p.setPen(Qt.NoPen)
            p.setBrush(TONGUE)
            p.drawEllipse(QPointF(x, y + s * 0.055 * k), w * 0.24 * k, s * 0.028 * k)
            p.restore()
            return
        if kind in ("o", "snore"):
            r = s * 0.03
            if kind == "snore":
                r *= 0.8 + 0.35 * (0.5 + 0.5 * math.sin(self.t * 2.0 * math.pi * 0.18))
            p.setPen(QPen(OUTLINE, max(0.8, s * 0.02)))
            p.setBrush(MOUTH)
            p.drawEllipse(QPointF(x, y + s * 0.02), r, r * 1.15)
            return
        p.setPen(line)
        p.setBrush(Qt.NoBrush)
        if kind == "smile":
            path.moveTo(x - w * 0.45, y - s * 0.008)
            path.quadTo(x, y + s * 0.07, x + w * 0.45, y - s * 0.008)
        elif kind == "flat":
            path.moveTo(x - w * 0.28, y + s * 0.014)
            path.quadTo(x, y + s * 0.026, x + w * 0.28, y + s * 0.014)
        elif kind == "meh":
            path.moveTo(x - w * 0.26, y + s * 0.024)
            path.quadTo(x, y + s * 0.01, x + w * 0.26, y + s * 0.024)
        elif kind == "frown":
            path.moveTo(x - w * 0.36, y + s * 0.045)
            path.quadTo(x, y - s * 0.018, x + w * 0.36, y + s * 0.045)
        elif kind == "wobble":
            for i in range(13):
                f = i / 12
                point = QPointF(x - w * 0.42 + f * w * 0.84, y + s * 0.02 + math.sin(f * math.pi * 4) * s * 0.013)
                path.moveTo(point) if i == 0 else path.lineTo(point)
        p.drawPath(path)

    def _paint_blossom(self, p: QPainter, center: QPointF, s: float) -> None:
        sway_amount, sway_rate = (9.0, 1.1) if self.mood == "break" else (4.0, 1.6)
        angle = self.leaf.x + math.sin(self.t * sway_rate) * sway_amount
        if self.face == "sleepy":
            angle += 14.0  # tips over while he dozes
        radius = s * 0.16
        p.save()
        p.translate(center)
        p.rotate(angle)
        petals = QPainterPath()
        for k in range(5):
            a = math.radians(-90 + k * 72)
            petals.addEllipse(QPointF(math.cos(a) * radius * 0.52, math.sin(a) * radius * 0.52),
                              radius * 0.5, radius * 0.5)
        petals = petals.simplified()  # one outline around the whole flower
        p.setPen(QPen(OUTLINE, max(1.0, s * 0.026), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(PETAL)
        p.drawPath(petals)
        p.setPen(Qt.NoPen)
        p.setBrush(PETAL_HEART)
        p.drawEllipse(QPointF(0, 0), radius * 0.3, radius * 0.3)
        p.restore()

    def _paint_particles(self, p: QPainter, anchor: QPointF, s: float) -> None:
        for part in self.particles:
            fade = 1.0 - part.age / part.life
            x, y = anchor.x() + part.x * s, anchor.y() + part.y * s
            if part.kind == "spark":
                r = s * 0.075 * (0.4 + 0.6 * fade)
                star = QPainterPath(QPointF(x, y - r))
                for i in range(1, 8):
                    a = -math.pi / 2 + i * math.pi / 4
                    rr = r if i % 2 == 0 else r * 0.3
                    star.lineTo(x + math.cos(a) * rr, y + math.sin(a) * rr)
                star.closeSubpath()
                color = QColor(part.color)
                color.setAlphaF(min(1.0, fade * 1.5))
                p.setPen(Qt.NoPen)
                p.setBrush(color)
                p.drawPath(star)
            elif part.kind == "confetti":
                color = QColor(part.color)
                color.setAlphaF(min(1.0, fade * 2.0))
                p.save()
                p.translate(x, y)
                p.rotate(math.degrees(part.spin))
                p.setPen(Qt.NoPen)
                p.setBrush(color)
                p.drawRoundedRect(QRectF(-s * 0.026, -s * 0.013, s * 0.052, s * 0.026), s * 0.008, s * 0.008)
                p.restore()
            elif part.kind == "z":
                font = QFont("Segoe UI")
                font.setBold(True)
                font.setPixelSize(max(6, round(s * 0.18 * (0.7 + 0.35 * part.age / part.life))))
                color = QColor(SNOOZE)
                color.setAlphaF(min(1.0, fade * 1.4))
                p.setFont(font)
                p.setPen(color)
                p.drawText(QPointF(x, y), "z")
            elif part.kind == "anger":
                pulse = 1.0 + 0.15 * math.sin(part.age * 14.0)
                r, d = s * 0.035 * pulse, s * 0.04 * pulse
                color = QColor(ANGER)
                color.setAlphaF(min(1.0, fade * 2.0))
                p.setPen(QPen(color, max(1.0, s * 0.025), Qt.SolidLine, Qt.RoundCap))
                p.setBrush(Qt.NoBrush)
                for qx, qy in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
                    toward_center = math.degrees(math.atan2(qy, -qx))
                    p.drawArc(QRectF(x + qx * d - r, y + qy * d - r, 2 * r, 2 * r),
                              int((toward_center - 55) * 16), int(110 * 16))
