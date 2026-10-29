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

from . import ultra
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
NOTES = ("#ee9aae", "#86b874", "#9aa7df")  # music notes: sakura, matcha, lavender

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
    "love": ("happy", "grin", None),
    "munch": ("happy", "chomp", None),
    "sideeye": ("half", "flat", None),  # caught you on YouTube during focus
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
    kind: str  # spark | confetti | z | anger | heart | crumb | note
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
        self.outfit = "blossom"         # see config.OUTFITS
        self._rub_dir = 0
        self._rub_turns: list[float] = []
        self._spin_start = -10.0
        self._dance_until = -1.0
        self.listening = False          # music is playing: headphones, a gentle sway and floating notes
        self._next_note = 0.0
        self.talking = False            # reading a reply aloud: the mouth moves
        self.reading = False            # Study with Pomo: he reads his own book while you focus
        self.ultra = False              # Ultra focus: golden aura, spiky hair, fire in his eyes (see ultra.py)
        self._ultra_at: float | None = None
        self._ultra_down_at: float | None = None
        self.aura_strength = 1.0        # a little less on the small timer
        self.hair_scale = 1.0           # and shorter hair, so it fits under the top of the screen
        self._look = ultra.Look()

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

    def cheer(self) -> None:
        """A small, quiet "you've got this" during focus: a little hop and a few sparks."""
        self._react("happy", 0.9)
        self.hop(1.4)
        self._burst("spark", 3, 1.1, 0.5)

    def rub(self, direction: int) -> bool:
        """Cursor moving over him; back-and-forth rubbing pets him. Returns True when he loves it."""
        if direction == 0 or direction == self._rub_dir:
            return False
        self._rub_dir = direction
        now = self.t
        self._rub_turns = [t for t in self._rub_turns if now - t < 1.4] + [now]
        if len(self._rub_turns) >= 4 and self.reaction not in ("love", "munch", "dizzy"):
            self._rub_turns.clear()
            self._react("love", 1.8)
            self.squish.kick(3.0)
            self._burst("heart", 4, 1.0, 1.4)
            return True
        return False

    def twirl(self) -> None:
        self._react("happy", 1.3)
        self.hop(2.6)
        self._spin_start = self.t
        self._burst("spark", 5, 1.5, 0.7)

    def munch(self) -> None:
        self._react("munch", 1.6)
        self.squish.kick(4.0)
        self._queued_hops.append((self.t + 1.4, 1.6))
        self._burst("crumb", 6, 0.9, 0.9)
        self._burst("heart", 2, 0.8, 1.6)

    def new_song(self) -> None:
        """A song starts: a little bop and a few notes."""
        self.hop(1.3)
        self.leaf.kick(110.0)
        for i, side in enumerate((-1, 1, -1)):
            self._emit_note(side, delay=i * 0.28)
        self._next_note = self.t + 1.2

    def _emit_note(self, side: int | None = None, delay: float = 0.0) -> None:
        side = side or self.rng.choice((-1, 1))
        self.particles.append(Particle(
            "note", side * self.rng.uniform(0.36, 0.5), self.rng.uniform(-0.3, -0.12),
            side * self.rng.uniform(0.04, 0.14), -self.rng.uniform(0.26, 0.36), self.rng.uniform(1.7, 2.2),
            age=-delay, spin=self.rng.uniform(0, 6.28), color=QColor(self.rng.choice(NOTES))))
        del self.particles[:-MAX_PARTICLES]

    def side_eye(self) -> None:
        """A distracting app is in front during focus: a long, unimpressed look."""
        self._react("sideeye", 3.2)
        self.leaf.kick(-60.0)
        self.squish.kick(1.5)

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
        if self.listening and t >= self._next_note:
            if self.face != "sleepy":
                self._emit_note()
            self._next_note = t + self.rng.uniform(1.2, 2.0)

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
        if self.reaction == "sideeye":  # a long look at whatever you just opened
            target = (0.95, 0.1)
        elif cursor is not None and t - self._cursor_moved_at < 4.0:
            dx, dy = cursor
            dist = math.hypot(dx, dy) or 1.0
            reach = min(1.0, dist / 3.0)
            target = (dx / dist * reach, dy / dist * reach)
        elif self.reading:  # eyes on the page, moving along each line
            target = (-0.45 + 0.9 * ((t * 0.28) % 1.0), 0.8)
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
        return (not self.listening and not self.reading and not self.ultra_look().showing
                and self._settled(notes_ok=False))

    def is_grooving(self) -> bool:
        """True when only a gentle loop moves (the music groove, or the ultra aura's flicker): a medium frame
        rate is plenty."""
        steady_ultra = self._ultra_settled()
        return (self.listening or self.reading or steady_ultra) and self._settled(notes_ok=True)

    def _settled(self, notes_ok: bool) -> bool:
        particles = all(p.kind == "note" for p in self.particles) if notes_ok else not self.particles
        return (self.reaction is None and particles and not self._queued_hops and not self.talking
                and self.signal != "alert"  # the "!" pulses; approvals are short-lived
                and self.t > self._dance_until and self.t - self._spin_start > 0.7
                and self.hop_y <= 0.0 and self.hop_v <= 0.0
                and abs(self.squish.x) + abs(self.squish.v) < 0.03
                and abs(self.leaf.x) + abs(self.leaf.v) < 1.5
                and (self.reading or self._eye_speed < 0.004)  # reading eyes glide along the lines
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
        if mood == "break" and self.rng.random() < 0.45:
            self._dance_until = self.t + 3.2  # a little break-time dance
            self._next_fidget = self.t + self.rng.uniform(8.0, 14.0)
            return
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
            if p.kind in ("confetti", "crumb"):
                p.vy += 3.4 * dt
                p.vx *= 1.0 - 0.8 * dt
            elif p.kind == "heart":
                p.vx *= 1.0 - 2.0 * dt
                p.vy = -0.35
            elif p.kind == "spark":
                p.vx *= 1.0 - 2.5 * dt
                p.vy *= 1.0 - 2.5 * dt
            elif p.kind == "note":
                if p.age < 0.0:  # not out yet
                    alive.append(p)
                    continue
                p.vx *= 1.0 - 0.7 * dt
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
        look = self._look = self.ultra_look()
        if look.charge:  # powering up: he crouches
            sx, sy = sx * (1 + 0.1 * look.charge), sy * (1 - 0.12 * look.charge)
        rx, ry = s * 0.5 * sx, s * BODY_RY * sy
        base_bottom = anchor.y() + s * BODY_RY
        c = QPointF(anchor.x(), anchor.y() + s * BODY_RY * (1 - sy) - self.hop_y * s)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        if look.aura:
            ultra.paint_aura(p, c, rx, ry, s, self.t, look.aura * self.aura_strength)

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
        if self.t < self._dance_until:  # sway side to side, tilting with the beat
            beat = math.sin(self.t * 7.0)
            p.translate(c.x() + beat * s * 0.05, base_bottom)
            p.rotate(beat * 7.0)
            p.translate(-c.x(), -base_bottom)
        elif self.listening and self.face != "sleepy":  # nodding along to the music, gently
            beat = math.sin(self.t * 5.2)
            p.translate(c.x() + beat * s * 0.018, base_bottom)
            p.rotate(beat * 3.5)
            p.translate(-c.x(), -base_bottom)
        spin = (self.t - self._spin_start) / 0.6
        if 0.0 <= spin < 1.0:  # twirl: one full turn while he hops
            p.translate(c)
            p.rotate(360.0 * (1 - (1 - spin) ** 2))
            p.translate(-c)
        if look.charge:  # trembling with power
            p.translate(math.sin(self.t * 85.0) * s * 0.018 * look.charge, 0)
        if look.aura:
            ultra.paint_rim(p, self._body_path(c.x(), c.y(), rx, ry), s, min(1.0, look.aura) * self.aura_strength)
        self._paint_body(p, c, rx, ry, s)
        self._paint_face(p, c, rx, ry, s)
        if self.reading and self.face != "sleepy":
            self._paint_book(p, c, rx, ry, s)
        if look.hair:
            ultra.paint_hair(p, c, rx, ry, s, self.t, look.hair * self.hair_scale)  # instead of any hat
        else:
            if self.listening and self.outfit != "headphones":
                self._paint_headphones(p, c, rx, ry, s)  # under any hat
            self._paint_outfit(p, c, rx, ry, s)
        p.restore()

        if self.signal:
            self._paint_bubble(p, c, rx, ry, s)
        self._paint_particles(p, anchor, s)
        ultra.paint_flash(p, c, s, look.flash)
        p.restore()

    def _paint_book(self, p: QPainter, c: QPointF, rx: float, ry: float, s: float) -> None:
        """Study with Pomo: an open book held in front of him, with a page turning now and then."""
        cx, cy = c.x(), c.y() + ry * 0.74
        w, h = rx * 0.7, ry * 0.42  # each page
        lw = max(1.0, s * 0.024)
        p.save()
        p.setPen(QPen(OUTLINE, lw, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(QColor("#a9b6e6"))  # the cover, peeking out under the pages
        p.drawRoundedRect(QRectF(cx - w - s * 0.02, cy - h * 0.45, 2 * w + s * 0.04, h * 1.0), s * 0.03, s * 0.03)
        for side in (-1, 1):
            page = QPainterPath(QPointF(cx, cy - h * 0.4))
            page.quadTo(QPointF(cx + side * w * 0.5, cy - h * 0.62), QPointF(cx + side * w, cy - h * 0.48))
            page.lineTo(cx + side * w, cy + h * 0.42)
            page.quadTo(QPointF(cx + side * w * 0.5, cy + h * 0.3), QPointF(cx, cy + h * 0.48))
            page.closeSubpath()
            p.setBrush(QColor("#fffdf8"))
            p.drawPath(page)
            p.save()
            p.setPen(QPen(QColor(91, 44, 41, 70), max(0.6, s * 0.012), Qt.SolidLine, Qt.RoundCap))
            for k in range(3):  # lines of text
                y = cy - h * 0.18 + k * h * 0.2
                p.drawLine(QPointF(cx + side * w * 0.18, y), QPointF(cx + side * w * 0.8, y))
            p.restore()
        flip = (self.t % 9.0) / 0.6  # every 9 seconds, a page turns over
        if flip < 1.0:
            x = cx + w * math.cos(math.pi * flip)
            lift = math.sin(math.pi * flip) * h * 0.5
            turning = QPainterPath(QPointF(cx, cy - h * 0.4))
            turning.quadTo(QPointF((cx + x) / 2, cy - h * 0.62 - lift), QPointF(x, cy - h * 0.48 - lift * 0.6))
            turning.lineTo(x, cy + h * 0.42 - lift * 0.6)
            turning.quadTo(QPointF((cx + x) / 2, cy + h * 0.3 - lift), QPointF(cx, cy + h * 0.48))
            turning.closeSubpath()
            p.setBrush(QColor("#f6efe2"))
            p.drawPath(turning)
        p.restore()

    # ------------------------------------------------------------ ultra focus
    def power_up(self, instant: bool = False) -> None:
        """Ultra focus on: the transformation plays (or, instant, he's simply powered up already)."""
        self.ultra = True
        self._ultra_down_at = None
        self._ultra_at = self.t - (ultra.TRANSFORM_S if instant else 0.0)
        if not instant:
            self._queued_hops.append((self.t + ultra.CHARGE_S, 2.4))  # the burst lifts him off his feet

    def power_down(self) -> None:
        if self.ultra:
            self.ultra = False
            self._ultra_down_at = self.t
            self.leaf.kick(-60.0)

    def ultra_look(self) -> ultra.Look:
        since = self.t - self._ultra_at if self.ultra and self._ultra_at is not None else None
        down = self.t - self._ultra_down_at if not self.ultra and self._ultra_down_at is not None else None
        return ultra.look_at(since, down)

    def _ultra_settled(self) -> bool:
        """Powered up and done transforming: only the aura's steady flicker moves."""
        return self.ultra and self._ultra_at is not None and self.t - self._ultra_at >= ultra.TRANSFORM_S

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
        look = self._look
        fire = 0.0
        if look.charge:
            eyes, mouth, brows = "squeeze", "wobble", None  # straining as the power builds
        elif look.showing and self.reaction is None and self.mood != "sleepy":
            eyes, mouth, brows = "narrow", "smile" if look.flash else "flat", "focus"  # determined
            fire = min(1.0, look.aura)
        if self.talking and self.reaction is None:
            mouth = "o" if int(self.t * 7.0) % 3 else "smile"
        lx, ly = self.ex * rx * 0.1, self.ey * ry * 0.1
        if eyes in ("happy", "sleep", "squeeze", "spiral"):
            lx *= 0.4
            ly *= 0.4
        x, y = c.x(), c.y()

        # striped blush
        angry = self.face in ("grumpy", "love")  # rosier cheeks
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
            if fire:
                ultra.paint_eye_fire(p, center, r, side, s, self.t, fire)
            if brows:
                self._paint_brow(p, brows, center, r, side, s)
        self._paint_mouth(p, mouth, QPointF(x + lx * 0.5, y + ry * 0.34 + ly * 0.5), s)
        if self.outfit == "glasses":
            self._paint_glasses(p, QPointF(x + lx, y + ry * 0.1 + ly), rx, r * 1.55, s)

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
        elif kind == "chomp":
            if int(self.t * 7.0) % 2:  # mouth opens and closes while he chews
                p.setPen(QPen(OUTLINE, max(0.8, s * 0.022)))
                p.setBrush(MOUTH)
                p.drawEllipse(QPointF(x, y + s * 0.02), w * 0.24, s * 0.035)
                return
            path.moveTo(x - w * 0.3, y + s * 0.01)
            path.quadTo(x, y + s * 0.05, x + w * 0.3, y + s * 0.01)
        elif kind == "wobble":
            for i in range(13):
                f = i / 12
                point = QPointF(x - w * 0.42 + f * w * 0.84, y + s * 0.02 + math.sin(f * math.pi * 4) * s * 0.013)
                path.moveTo(point) if i == 0 else path.lineTo(point)
        p.drawPath(path)

    # ---------------------------------------------------------------- outfits
    def _paint_outfit(self, p: QPainter, c: QPointF, rx: float, ry: float, s: float) -> None:
        outfit = self.outfit
        if outfit in ("blossom", "glasses"):
            self._paint_blossom(p, QPointF(c.x() + rx * 0.56, c.y() - ry * 0.74), s)
            return
        if outfit == "headphones":
            self._paint_headphones(p, c, rx, ry, s)
            return
        if outfit not in ("beanie", "party", "crown", "bow", "catears", "flowercrown", "gradcap", "wizard",
                          "hardhat", "nightcap", "headband", "sunhat", "chefhat", "beret", "starclip", "bunnyears",
                          "strawberry"):
            return
        # hats wobble a little with the same spring as the blossom
        base = QPointF(c.x(), c.y() - ry * 0.85)
        p.save()
        p.translate(base)
        p.rotate(self.leaf.x * 0.35)
        p.translate(-base)
        getattr(self, f"_paint_{outfit}")(p, c, rx, ry, s)
        p.restore()

    @staticmethod
    def _line(s: float, width: float = 0.026) -> QPen:
        return QPen(OUTLINE, max(1.0, s * width), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)

    def _paint_headphones(self, p: QPainter, c: QPointF, rx: float, ry: float, s: float) -> None:
        band = QRectF(c.x() - rx * 0.98, c.y() - ry * 1.27, rx * 1.96, ry * 1.94)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(OUTLINE, max(2.0, s * 0.085), Qt.SolidLine, Qt.RoundCap))
        p.drawArc(band, 8 * 16, 164 * 16)
        p.setPen(QPen(QColor("#c9c3ea"), max(1.0, s * 0.045), Qt.SolidLine, Qt.RoundCap))
        p.drawArc(band, 8 * 16, 164 * 16)
        w, h = s * 0.14, s * 0.25
        for side in (-1, 1):
            cup = QRectF(c.x() + side * rx * 0.98 - w / 2, c.y() - ry * 0.32, w, h)
            p.setPen(self._line(s))
            p.setBrush(QColor("#c9c3ea"))
            p.drawRoundedRect(cup, w * 0.45, w * 0.45)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#f6b3c4"))
            p.drawEllipse(cup.center(), w * 0.2, h * 0.22)

    def _paint_beanie(self, p: QPainter, c: QPointF, rx: float, ry: float, s: float) -> None:
        x, y = c.x(), c.y()
        hat = QPainterPath(QPointF(x - rx * 0.86, y - ry * 0.4))
        hat.cubicTo(x - rx * 0.86, y - ry * 1.34, x + rx * 0.86, y - ry * 1.34, x + rx * 0.86, y - ry * 0.4)
        hat.closeSubpath()
        p.setPen(self._line(s))
        p.setBrush(QColor("#a9c7ee"))
        p.drawPath(hat)
        p.setPen(QPen(QColor(91, 44, 41, 60), max(0.8, s * 0.014)))
        for k in (-0.45, -0.15, 0.15, 0.45):  # knit ribs
            p.drawLine(QPointF(x + rx * k, y - ry * 0.5), QPointF(x + rx * k * 0.9, y - ry * 1.05))
        cuff = QRectF(x - rx * 0.93, y - ry * 0.6, rx * 1.86, ry * 0.3)
        p.setPen(self._line(s))
        p.setBrush(QColor("#8fb2e3"))
        p.drawRoundedRect(cuff, ry * 0.12, ry * 0.12)
        p.setBrush(QColor("#fffdf8"))
        p.drawEllipse(QPointF(x, y - ry * 1.2), s * 0.075, s * 0.075)

    def _paint_party(self, p: QPainter, c: QPointF, rx: float, ry: float, s: float) -> None:
        x, y = c.x(), c.y()
        left, right, apex = QPointF(x - rx * 0.34, y - ry * 0.9), QPointF(x + rx * 0.36, y - ry * 0.86), QPointF(x + rx * 0.12, y - ry * 2.0)
        cone = QPainterPath(left)
        cone.lineTo(apex)
        cone.lineTo(right)
        cone.quadTo(x, y - ry * 0.78, left.x(), left.y())
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#f9dc8a"))
        p.drawPath(cone)
        p.save()
        p.setClipPath(cone)
        p.setPen(QPen(QColor("#f3a7b8"), s * 0.05))
        for t in (0.3, 0.6):  # stripes across the cone
            a = QPointF(left.x() + (apex.x() - left.x()) * t, left.y() + (apex.y() - left.y()) * t)
            b = QPointF(right.x() + (apex.x() - right.x()) * t, right.y() + (apex.y() - right.y()) * t)
            p.drawLine(a, b)
        p.restore()
        p.setPen(self._line(s))
        p.setBrush(Qt.NoBrush)
        p.drawPath(cone)
        p.setBrush(QColor("#f3a7b8"))
        p.drawEllipse(apex, s * 0.05, s * 0.05)

    def _paint_crown(self, p: QPainter, c: QPointF, rx: float, ry: float, s: float) -> None:
        w, h = rx * 0.8, ry * 0.6
        base, left, right = c.y() - ry * 0.84, c.x() - rx * 0.4, c.x() + rx * 0.4
        crown = QPainterPath(QPointF(left, base))
        for px, py in ((left, base - h * 0.62), (left + w * 0.25, base - h * 0.25), (c.x(), base - h),
                       (right - w * 0.25, base - h * 0.25), (right, base - h * 0.62), (right, base)):
            crown.lineTo(px, py)
        crown.closeSubpath()
        p.setPen(self._line(s))
        p.setBrush(QColor("#f5d77e"))
        p.drawPath(crown)
        p.setBrush(QColor("#f3a7b8"))
        for px, py in ((left, base - h * 0.62), (c.x(), base - h), (right, base - h * 0.62)):
            p.drawEllipse(QPointF(px, py), s * 0.03, s * 0.03)

    def _paint_bow(self, p: QPainter, c: QPointF, rx: float, ry: float, s: float) -> None:
        center = QPointF(c.x() + rx * 0.5, c.y() - ry * 0.8)
        p.setPen(self._line(s))
        p.setBrush(QColor("#f6a9bd"))
        for side in (-1, 1):
            tail = QPainterPath(QPointF(center.x(), center.y()))
            tail.lineTo(center.x() + side * s * 0.09, center.y() + s * 0.13)
            tail.lineTo(center.x() + side * s * 0.03, center.y() + s * 0.12)
            tail.closeSubpath()
            p.drawPath(tail)
        for side in (-1, 1):
            p.save()
            p.translate(center.x() + side * s * 0.1, center.y() - s * 0.01)
            p.rotate(side * 22)
            p.drawEllipse(QPointF(0, 0), s * 0.11, s * 0.07)
            p.restore()
        p.drawEllipse(center, s * 0.04, s * 0.04)

    def _paint_catears(self, p: QPainter, c: QPointF, rx: float, ry: float, s: float) -> None:
        for side in (-1, 1):
            base_in, base_out = c.x() + side * rx * 0.18, c.x() + side * rx * 0.72
            tip = QPointF(c.x() + side * rx * 0.58, c.y() - ry * 1.55)
            ear = QPainterPath(QPointF(base_in, c.y() - ry * 0.86))
            ear.lineTo(tip)
            ear.lineTo(base_out, c.y() - ry * 0.62)
            ear.closeSubpath()
            p.setPen(self._line(s))
            p.setBrush(BODY)
            p.drawPath(ear)
            inner = QPainterPath(QPointF(c.x() + side * rx * 0.3, c.y() - ry * 0.86))
            inner.lineTo(c.x() + side * rx * 0.56, c.y() - ry * 1.32)
            inner.lineTo(c.x() + side * rx * 0.64, c.y() - ry * 0.74)
            inner.closeSubpath()
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#f6b3c4"))
            p.drawPath(inner)

    def _paint_flowercrown(self, p: QPainter, c: QPointF, rx: float, ry: float, s: float) -> None:
        colors = ("#fbd0d7", "#f9dc8a", "#c9c3ea", "#fbd0d7", "#a6cf92", "#f9dc8a", "#fbd0d7")
        p.setPen(QPen(QColor("#86b874"), max(1.0, s * 0.03), Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        arc = QRectF(c.x() - rx * 0.86, c.y() - ry * 1.02, rx * 1.72, ry * 0.9)
        p.drawArc(arc, 20 * 16, 140 * 16)
        for i, color in enumerate(colors):
            a = math.radians(160 - i * (140 / (len(colors) - 1)))
            center = QPointF(arc.center().x() + math.cos(a) * arc.width() / 2,
                             arc.center().y() - math.sin(a) * arc.height() / 2)
            r = s * 0.055
            p.setPen(QPen(OUTLINE, max(0.8, s * 0.016)))
            p.setBrush(QColor(color))
            for k in range(5):
                b = math.radians(k * 72 + i * 20)
                p.drawEllipse(QPointF(center.x() + math.cos(b) * r * 0.7, center.y() + math.sin(b) * r * 0.7), r * 0.55, r * 0.55)
            p.setPen(Qt.NoPen)
            p.setBrush(PETAL_HEART)
            p.drawEllipse(center, r * 0.38, r * 0.38)

    def _paint_gradcap(self, p: QPainter, c: QPointF, rx: float, ry: float, s: float) -> None:
        x, y = c.x(), c.y() - ry * 0.92
        cap = QRectF(x - rx * 0.46, y - ry * 0.12, rx * 0.92, ry * 0.32)
        p.setPen(self._line(s))
        p.setBrush(QColor("#5a5470"))
        p.drawRoundedRect(cap, ry * 0.06, ry * 0.06)
        board = QPainterPath(QPointF(x, y - ry * 0.52))
        board.lineTo(x + rx * 0.82, y - ry * 0.2)
        board.lineTo(x, y + ry * 0.08)
        board.lineTo(x - rx * 0.82, y - ry * 0.2)
        board.closeSubpath()
        p.setBrush(QColor("#6d6788"))
        p.drawPath(board)
        p.setPen(QPen(QColor("#f5d77e"), max(1.0, s * 0.025), Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(x, y - ry * 0.22), QPointF(x + rx * 0.62, y - ry * 0.12))
        p.drawLine(QPointF(x + rx * 0.62, y - ry * 0.12), QPointF(x + rx * 0.64, y + ry * 0.32))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#f5d77e"))
        p.drawEllipse(QPointF(x + rx * 0.64, y + ry * 0.36), s * 0.03, s * 0.045)

    def _paint_wizard(self, p: QPainter, c: QPointF, rx: float, ry: float, s: float) -> None:
        x, y = c.x(), c.y()
        brim = QRectF(x - rx * 0.78, y - ry * 0.98, rx * 1.56, ry * 0.36)
        cone = QPainterPath(QPointF(x - rx * 0.5, y - ry * 0.82))
        cone.quadTo(x - rx * 0.1, y - ry * 1.6, x + rx * 0.42, y - ry * 2.25)
        cone.quadTo(x + rx * 0.2, y - ry * 1.5, x + rx * 0.5, y - ry * 0.82)
        cone.closeSubpath()
        p.setPen(self._line(s))
        p.setBrush(QColor("#9aa7df"))
        p.drawPath(cone)
        p.drawEllipse(brim)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#f5d77e"))
        star = QPointF(x + rx * 0.02, y - ry * 1.3)
        path = QPainterPath()
        for i in range(10):
            a = -math.pi / 2 + i * math.pi / 5
            r = s * 0.06 if i % 2 == 0 else s * 0.025
            point = QPointF(star.x() + math.cos(a) * r, star.y() + math.sin(a) * r)
            path.moveTo(point) if i == 0 else path.lineTo(point)
        path.closeSubpath()
        p.drawPath(path)

    def _paint_hardhat(self, p: QPainter, c: QPointF, rx: float, ry: float, s: float) -> None:
        x, y = c.x(), c.y()
        dome = QPainterPath(QPointF(x - rx * 0.78, y - ry * 0.62))
        dome.cubicTo(x - rx * 0.78, y - ry * 1.45, x + rx * 0.78, y - ry * 1.45, x + rx * 0.78, y - ry * 0.62)
        dome.closeSubpath()
        p.setPen(self._line(s))
        p.setBrush(QColor("#f7d36b"))
        p.drawPath(dome)
        p.setPen(QPen(QColor("#e4b847"), max(1.0, s * 0.03), Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(x, y - ry * 1.27), QPointF(x, y - ry * 0.7))  # the ridge
        p.setPen(self._line(s))
        p.setBrush(QColor("#f2c64f"))
        p.drawRoundedRect(QRectF(x - rx * 0.98, y - ry * 0.72, rx * 1.96, ry * 0.2), ry * 0.08, ry * 0.08)

    def _paint_nightcap(self, p: QPainter, c: QPointF, rx: float, ry: float, s: float) -> None:
        x, y = c.x(), c.y()
        cap = QPainterPath(QPointF(x - rx * 0.82, y - ry * 0.6))
        cap.cubicTo(x - rx * 0.7, y - ry * 1.5, x + rx * 0.4, y - ry * 1.55, x + rx * 1.15, y - ry * 0.95)
        cap.quadTo(x + rx * 0.7, y - ry * 0.95, x + rx * 0.82, y - ry * 0.6)
        cap.closeSubpath()
        p.setPen(self._line(s))
        p.setBrush(QColor("#a7b3e3"))
        p.drawPath(cap)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 150))
        for k in (-0.4, 0.0, 0.4):  # stars on the fabric
            p.drawEllipse(QPointF(x + rx * k, y - ry * 1.05), s * 0.018, s * 0.018)
        p.setPen(self._line(s))
        p.setBrush(QColor("#c9c3ea"))
        p.drawRoundedRect(QRectF(x - rx * 0.9, y - ry * 0.72, rx * 1.8, ry * 0.24), ry * 0.1, ry * 0.1)
        p.setBrush(QColor("#fffdf8"))
        p.drawEllipse(QPointF(x + rx * 1.15, y - ry * 0.9), s * 0.07, s * 0.07)

    def _paint_headband(self, p: QPainter, c: QPointF, rx: float, ry: float, s: float) -> None:
        x, y = c.x(), c.y() - ry * 0.68
        band = QPainterPath(QPointF(x - rx * 0.92, y + ry * 0.08))
        band.quadTo(x, y - ry * 0.22, x + rx * 0.92, y + ry * 0.08)
        band.lineTo(x + rx * 0.86, y + ry * 0.3)
        band.quadTo(x, y, x - rx * 0.86, y + ry * 0.3)
        band.closeSubpath()
        p.setPen(self._line(s, 0.022))
        p.setBrush(QColor("#f3a7b8"))
        p.drawPath(band)
        p.setPen(QPen(QColor("#fffdf8"), max(1.0, s * 0.02), Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(x - rx * 0.6, y + ry * 0.07), QPointF(x + rx * 0.6, y + ry * 0.07))
        p.setPen(self._line(s, 0.022))
        p.setBrush(QColor("#f3a7b8"))
        for k in (0, 1):  # the knot's tails
            tail = QPainterPath(QPointF(x + rx * 0.86, y + ry * 0.18))
            tail.lineTo(x + rx * (1.1 + 0.08 * k), y + ry * (0.0 + 0.32 * k))
            tail.lineTo(x + rx * (1.02 + 0.04 * k), y + ry * (0.16 + 0.24 * k))
            tail.closeSubpath()
            p.drawPath(tail)

    def _paint_sunhat(self, p: QPainter, c: QPointF, rx: float, ry: float, s: float) -> None:
        x, y = c.x(), c.y()
        p.setPen(self._line(s))
        p.setBrush(QColor("#f1d9a5"))
        p.drawEllipse(QRectF(x - rx * 1.3, y - ry * 0.98, rx * 2.6, ry * 0.46))  # the brim
        crown = QPainterPath(QPointF(x - rx * 0.6, y - ry * 0.78))
        crown.cubicTo(x - rx * 0.6, y - ry * 1.75, x + rx * 0.6, y - ry * 1.75, x + rx * 0.6, y - ry * 0.78)
        crown.closeSubpath()
        p.drawPath(crown)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#f3a7b8"))
        ribbon = QPainterPath(QPointF(x - rx * 0.6, y - ry * 0.84))
        ribbon.quadTo(x, y - ry * 0.74, x + rx * 0.6, y - ry * 0.84)
        ribbon.lineTo(x + rx * 0.6, y - ry * 1.0)
        ribbon.quadTo(x, y - ry * 0.9, x - rx * 0.6, y - ry * 1.0)
        ribbon.closeSubpath()
        p.drawPath(ribbon)
        p.setBrush(QColor("#fbd0d7"))
        p.drawEllipse(QPointF(x + rx * 0.5, y - ry * 0.92), s * 0.05, s * 0.05)

    def _paint_chefhat(self, p: QPainter, c: QPointF, rx: float, ry: float, s: float) -> None:
        x, y = c.x(), c.y()
        p.setPen(self._line(s))
        p.setBrush(QColor("#fffdf8"))
        puff = QPainterPath()
        puff.setFillRule(Qt.WindingFill)  # overlapping puffs fill as one
        for dx, dy, r in ((-0.42, -1.35, 0.3), (0.0, -1.55, 0.36), (0.42, -1.35, 0.3)):
            puff.addEllipse(QPointF(x + rx * dx, y + ry * dy), rx * r, rx * r)
        puff.addRect(QRectF(x - rx * 0.6, y - ry * 1.35, rx * 1.2, ry * 0.6))
        p.drawPath(puff.simplified())
        p.drawRoundedRect(QRectF(x - rx * 0.66, y - ry * 0.86, rx * 1.32, ry * 0.3), ry * 0.06, ry * 0.06)

    def _paint_beret(self, p: QPainter, c: QPointF, rx: float, ry: float, s: float) -> None:
        x, y = c.x(), c.y()
        p.save()
        p.translate(x, y - ry * 0.85)
        p.rotate(-12)
        p.setPen(self._line(s))
        p.setBrush(QColor("#c96b80"))
        p.drawEllipse(QRectF(-rx * 0.95, -ry * 0.4, rx * 1.8, ry * 0.62))
        p.drawLine(QPointF(rx * 0.05, -ry * 0.4), QPointF(rx * 0.1, -ry * 0.58))  # the little stalk
        p.restore()

    def _paint_starclip(self, p: QPainter, c: QPointF, rx: float, ry: float, s: float) -> None:
        center = QPointF(c.x() + rx * 0.52, c.y() - ry * 0.78)
        star = QPainterPath()
        for i in range(10):
            a = -math.pi / 2 + i * math.pi / 5 + 0.15
            r = s * (0.13 if i % 2 == 0 else 0.058)
            point = QPointF(center.x() + math.cos(a) * r, center.y() + math.sin(a) * r)
            star.moveTo(point) if i == 0 else star.lineTo(point)
        star.closeSubpath()
        p.setPen(self._line(s))
        p.setBrush(QColor("#f9dc8a"))
        p.drawPath(star)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 200))
        p.drawEllipse(QPointF(center.x() - s * 0.025, center.y() - s * 0.03), s * 0.022, s * 0.022)
        twinkle = 0.5 + 0.5 * math.sin(self.t * 3.0)  # a little sparkle beside it
        p.setPen(QPen(QColor(245, 215, 126, int(120 + 120 * twinkle)), max(1.0, s * 0.018), Qt.SolidLine, Qt.RoundCap))
        spark = QPointF(center.x() + s * 0.17, center.y() - s * 0.12)
        arm = s * (0.025 + 0.02 * twinkle)
        p.drawLine(QPointF(spark.x() - arm, spark.y()), QPointF(spark.x() + arm, spark.y()))
        p.drawLine(QPointF(spark.x(), spark.y() - arm), QPointF(spark.x(), spark.y() + arm))

    def _paint_bunnyears(self, p: QPainter, c: QPointF, rx: float, ry: float, s: float) -> None:
        for side in (-1, 1):
            p.save()
            p.translate(c.x() + side * rx * 0.36, c.y() - ry * 0.8)
            p.rotate(side * 14 + side * math.sin(self.t * 1.3 + side) * 3)  # they twitch a little
            ear = QRectF(-s * 0.085, -s * 0.5, s * 0.17, s * 0.52)
            p.setPen(self._line(s))
            p.setBrush(BODY)
            p.drawRoundedRect(ear, s * 0.085, s * 0.085)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#f6b3c4"))
            p.drawRoundedRect(ear.adjusted(s * 0.04, s * 0.06, -s * 0.04, -s * 0.12), s * 0.045, s * 0.045)
            p.restore()

    def _paint_strawberry(self, p: QPainter, c: QPointF, rx: float, ry: float, s: float) -> None:
        x, y = c.x(), c.y()
        cap = QPainterPath(QPointF(x - rx * 0.8, y - ry * 0.62))
        cap.cubicTo(x - rx * 0.85, y - ry * 1.55, x + rx * 0.85, y - ry * 1.55, x + rx * 0.8, y - ry * 0.62)
        cap.quadTo(x, y - ry * 0.48, x - rx * 0.8, y - ry * 0.62)
        p.setPen(self._line(s))
        p.setBrush(QColor("#f28a95"))
        p.drawPath(cap)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#fff2b8"))
        for dx, dy in ((-0.5, -0.85), (-0.15, -1.0), (0.2, -0.88), (0.5, -0.78), (-0.32, -1.18), (0.05, -1.2),
                       (0.35, -1.08)):
            p.save()
            p.translate(x + rx * dx, y + ry * dy)
            p.rotate(20)
            p.drawEllipse(QPointF(0, 0), s * 0.014, s * 0.022)
            p.restore()
        p.setPen(self._line(s, 0.022))
        p.setBrush(QColor("#86b874"))
        top = QPointF(x, y - ry * 1.3)
        for angle in (-60, -20, 20, 60):
            leaf = QPainterPath(top)
            a = math.radians(angle - 90)
            tip = QPointF(top.x() + math.cos(a) * s * 0.16, top.y() + math.sin(a) * s * 0.1 + s * 0.03)
            side = QPointF(-math.sin(a) * s * 0.035, math.cos(a) * s * 0.035)
            leaf.quadTo(QPointF((top.x() + tip.x()) / 2 + side.x(), (top.y() + tip.y()) / 2 + side.y()), tip)
            leaf.quadTo(QPointF((top.x() + tip.x()) / 2 - side.x(), (top.y() + tip.y()) / 2 - side.y()), top)
            p.drawPath(leaf)
        p.setPen(QPen(QColor("#6f9d60"), max(1.0, s * 0.03), Qt.SolidLine, Qt.RoundCap))
        p.drawLine(top, QPointF(top.x() + s * 0.02, top.y() - s * 0.07))

    def _paint_glasses(self, p: QPainter, center: QPointF, rx: float, r: float, s: float) -> None:
        p.setPen(self._line(s, 0.024))
        p.setBrush(QColor(255, 255, 255, 45))
        gap = rx * 0.34
        for side in (-1, 1):
            p.drawEllipse(QPointF(center.x() + side * gap, center.y()), r, r)
        p.setBrush(Qt.NoBrush)
        p.drawLine(QPointF(center.x() - gap + r, center.y() - r * 0.15), QPointF(center.x() + gap - r, center.y() - r * 0.15))

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
            if part.age < 0.0:
                continue
            fade = 1.0 - part.age / part.life
            x, y = anchor.x() + part.x * s, anchor.y() + part.y * s
            if part.kind == "note":
                self._paint_note(p, QPointF(x + math.sin(part.age * 4.0 + part.spin) * s * 0.03, y), s, part, fade)
            elif part.kind == "spark":
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
            elif part.kind == "heart":
                r = s * 0.05 * (0.6 + 0.4 * fade)
                heart = QPainterPath(QPointF(x, y + r * 0.9))
                heart.cubicTo(x - r * 1.6, y - r * 0.2, x - r * 0.7, y - r * 1.4, x, y - r * 0.45)
                heart.cubicTo(x + r * 0.7, y - r * 1.4, x + r * 1.6, y - r * 0.2, x, y + r * 0.9)
                color = QColor("#f28ba3")
                color.setAlphaF(min(1.0, fade * 1.6))
                p.setPen(Qt.NoPen)
                p.setBrush(color)
                p.drawPath(heart)
            elif part.kind == "crumb":
                color = QColor("#e8cfa0")
                color.setAlphaF(min(1.0, fade * 2.0))
                p.setPen(Qt.NoPen)
                p.setBrush(color)
                p.drawEllipse(QPointF(x, y + s * 0.3), s * 0.018, s * 0.018)
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

    @staticmethod
    def _paint_note(p: QPainter, c: QPointF, s: float, part: Particle, fade: float) -> None:
        """A small eighth note, fading in, then out as it floats away."""
        appear = min(1.0, part.age / 0.25)
        color = QColor(part.color)
        color.setAlphaF(min(1.0, fade * 1.6) * appear)
        size = max(7.0, s * 0.2) * (0.75 + 0.25 * appear)
        p.save()
        p.translate(c)
        p.rotate(math.sin(part.age * 3.0 + part.spin) * 12.0)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        head = QPointF(-size * 0.13, size * 0.22)
        p.save()
        p.translate(head)
        p.rotate(-24)
        p.drawEllipse(QPointF(0, 0), size * 0.17, size * 0.125)
        p.restore()
        x = head.x() + size * 0.145
        p.setPen(QPen(color, max(1.0, size * 0.1), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawLine(QPointF(x, head.y() - size * 0.03), QPointF(x, -size * 0.36))
        flag = QPainterPath(QPointF(x, -size * 0.36))
        flag.cubicTo(x + size * 0.04, -size * 0.2, x + size * 0.3, -size * 0.2, x + size * 0.2, size * 0.03)
        p.setBrush(Qt.NoBrush)
        p.drawPath(flag)
        p.restore()
