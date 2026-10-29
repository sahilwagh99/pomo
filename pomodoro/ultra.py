"""Ultra focus: Pomo powers up. A golden flame aura with rising embers and crackling lightning,
tall spiky golden hair, and fire in his eyes.

The transformation (about 2.4 seconds): Pomo crouches and trembles while the aura charges, a flash,
the hair bursts up with a hop, then the aura settles into a steady flicker. Powering down shrinks it
all away. Everything here is drawn in code, scaled to Pomo's size, so the big Pomo in the
transformation and the little one on the timer share it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient

CHARGE_S = 0.9         # crouch, tremble, the aura grows
BURST_S = 0.35         # the flash
TRANSFORM_S = 2.4      # until it settles
POWER_DOWN_S = 0.6

OUTLINE = QColor("#5b2c29")
GOLD = QColor("#ffd23f")
GOLD_LIGHT = QColor("#fff3a8")
GOLD_DEEP = QColor("#f2a51b")
EMBER = QColor("#ffb84d")
BOLT_GLOW = QColor(150, 215, 255, 120)
BOLT_CORE = QColor("#f4fbff")

# the hair: (where along the head, -1 left to 1 right; tip x offset and height above the head in body
# radii; half-width at the base). Tall, swept back a little, with one bang over the forehead.
SPIKES = (
    (-0.86, -1.18, 0.55, 0.28),
    (-0.58, -0.86, 1.18, 0.30),
    (-0.24, -0.36, 1.62, 0.32),
    (0.12, 0.18, 1.72, 0.32),
    (0.46, 0.72, 1.30, 0.30),
    (0.80, 1.16, 0.70, 0.28),
)


@dataclass
class Look:
    """How much of the power-up shows right now."""
    aura: float = 0.0     # 0 none, 1 steady (a little more right after the burst)
    hair: float = 0.0     # the hair's scale (it overshoots as it pops up)
    charge: float = 0.0   # 0..1 while charging: crouch and tremble
    flash: float = 0.0    # 0..1 the burst of light

    @property
    def showing(self) -> bool:
        return self.aura > 0.001 or self.hair > 0.001 or self.flash > 0.001


def _overshoot(u: float) -> float:
    """0 -> 1 with a springy overshoot (for the hair popping up)."""
    if u <= 0:
        return 0.0
    if u >= 1:
        return 1.0
    return 1 + 2.4 * (u - 1) ** 3 + 1.4 * (u - 1) ** 2


def look_at(since: float | None, down_since: float | None = None) -> Look:
    """The look `since` seconds into the transformation (None: not ultra), or `down_since` into powering down."""
    if since is None:
        if down_since is None or down_since >= POWER_DOWN_S:
            return Look()
        f = 1.0 - down_since / POWER_DOWN_S
        return Look(aura=f, hair=f * f)
    if since >= TRANSFORM_S:
        return Look(aura=1.0, hair=1.0)
    if since < CHARGE_S:
        u = since / CHARGE_S
        return Look(aura=0.7 * u, charge=u)
    d = since - CHARGE_S
    return Look(aura=1.25 - 0.25 * min(1.0, d / 1.0), hair=_overshoot(d / 0.28),
                flash=max(0.0, 1.0 - d / BURST_S))


# ------------------------------------------------------------------ the aura
def _flame(c: QPointF, w: float, up: float, down: float, t: float, wild: float) -> QPainterPath:
    """A flame around a point: round below, tall flickering tongues above."""
    n = 72
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        sa, ca = math.sin(a), math.cos(a)
        flick = 0.05 * math.sin(3 * a + t * 7) + 0.03 * math.sin(7 * a - t * 11)
        if sa > 0:  # the upper half: tongues licking upward
            tongues = wild * sa ** 2 * (0.55 + 0.45 * math.sin(5 * a - t * 10)) * (0.8 + 0.2 * math.sin(t * 17 + a))
            pts.append(QPointF(c.x() + ca * w * (1 + flick), c.y() - sa * up * (1 + flick + tongues)))
        else:
            pts.append(QPointF(c.x() + ca * w * (1 + flick), c.y() - sa * down))
    path = QPainterPath()
    mid = lambda p, q: QPointF((p.x() + q.x()) / 2, (p.y() + q.y()) / 2)  # noqa: E731
    path.moveTo(mid(pts[-1], pts[0]))
    for i, pt in enumerate(pts):
        path.quadTo(pt, mid(pt, pts[(i + 1) % n]))
    path.closeSubpath()
    return path


def paint_aura(p: QPainter, c: QPointF, rx: float, ry: float, s: float, t: float, level: float) -> None:
    """Behind Pomo: two flame layers, embers floating up, and now and then a crackle of lightning."""
    if level <= 0.001:
        return
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    base = QPointF(c.x(), c.y() + ry * 0.15)
    for layer, (grow, alpha, speed) in enumerate(((1.0, 0.62, 1.0), (0.74, 0.9, 1.35))):
        w, up, down = rx * 1.55 * grow * level, ry * 2.75 * grow * level, ry * 1.35 * grow * level
        if w < 1:
            continue
        path = _flame(base, w, up, down, t * speed, 0.38)
        glow = QRadialGradient(QPointF(c.x(), c.y() - ry * 0.3), max(w, up) * 1.05)
        core = QColor(GOLD_LIGHT)
        core.setAlphaF(min(1.0, alpha * level))
        mid = QColor(GOLD)
        mid.setAlphaF(min(1.0, alpha * 0.85 * level))
        edge = QColor(EMBER)
        edge.setAlphaF(min(1.0, 0.25 * level))
        glow.setColorAt(0.0, core)
        glow.setColorAt(0.55, mid)
        glow.setColorAt(1.0, edge)
        p.setBrush(glow)
        p.drawPath(path)
        if layer == 0:  # a bright rim, so the flame's shape reads on dark themes too
            rim = QColor(GOLD)
            rim.setAlphaF(min(1.0, 0.55 * level))
            p.setPen(QPen(rim, max(1.0, s * 0.02), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
            p.setPen(Qt.NoPen)
    for i in range(10):  # embers drifting up through the aura
        u = (t * (0.55 + 0.07 * (i % 3)) + i / 10) % 1.0
        x = c.x() + math.sin(i * 12.9898 + 1.3) * rx * 1.1 + math.sin(t * 3 + i) * rx * 0.08
        y = c.y() + ry * 0.9 - u * ry * 3.2 * level
        r = s * 0.022 * (1.0 - u * 0.6)
        ember = QColor(GOLD_LIGHT if i % 2 else EMBER)
        ember.setAlphaF(max(0.0, (1.0 - u) * level))
        p.setBrush(ember)
        p.drawPolygon([QPointF(x, y - r * 1.6), QPointF(x + r, y), QPointF(x, y + r * 1.6), QPointF(x - r, y)])
    if level > 0.35:
        _paint_bolts(p, c, rx, ry, s, t, level)
    p.restore()


def _paint_bolts(p: QPainter, c: QPointF, rx: float, ry: float, s: float, t: float, level: float) -> None:
    """Short zigzags of lightning flickering around him."""
    slot_len = 0.22
    for lane in range(2):
        slot = int((t + lane * 0.11) / slot_len)
        into = (t + lane * 0.11) % slot_len
        h = (slot * 2654435761 + lane * 97) % 1000
        if into > 0.09 or h % 3 == 0:
            continue
        a = math.pi * (0.08 + 0.84 * (h / 1000))  # somewhere around the upper half
        start = QPointF(c.x() + math.cos(a) * rx * 0.95, c.y() - math.sin(a) * ry * 1.25)
        out = QPointF(math.cos(a), -math.sin(a))
        side = QPointF(-out.y(), out.x())
        path = QPainterPath(start)
        point = start
        flip = 1 if h & 1 else -1
        for k in range(5):  # a sharp zigzag, kinking left and right
            step = s * 0.075 * level
            wiggle = flip * (1 if k % 2 == 0 else -1) * s * 0.075
            point = QPointF(point.x() + out.x() * step + side.x() * wiggle, point.y() + out.y() * step + side.y() * wiggle)
            path.lineTo(point)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(BOLT_GLOW, max(1.5, s * 0.045), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(path)
        p.setPen(QPen(BOLT_CORE, max(0.8, s * 0.016), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(path)


def paint_flash(p: QPainter, c: QPointF, s: float, flash: float) -> None:
    """The burst: a ring of light that spreads and fades."""
    if flash <= 0.001:
        return
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    radius = s * (0.5 + 1.9 * (1.0 - flash))
    light = QRadialGradient(c, radius)
    inner = QColor(255, 255, 240)
    inner.setAlphaF(0.9 * flash)
    ring = QColor(GOLD_LIGHT)
    ring.setAlphaF(0.75 * flash)
    clear = QColor(GOLD)
    clear.setAlphaF(0.0)
    light.setColorAt(0.0, inner)
    light.setColorAt(0.7, ring)
    light.setColorAt(1.0, clear)
    p.setPen(Qt.NoPen)
    p.setBrush(light)
    p.drawEllipse(c, radius, radius)
    p.restore()


# ------------------------------------------------------------------ hair and eyes
def _hair_path(c: QPointF, rx: float, ry: float, t: float) -> QPainterPath:
    x, y = c.x(), c.y()
    cap = QPainterPath()
    cap.addEllipse(QPointF(x, y - ry * 0.74), rx * 0.9, ry * 0.42)
    hair = cap
    for i, (b, tip_dx, height, half) in enumerate(SPIKES):
        bx = x + b * rx * 0.86
        by = y - ry * (0.98 - 0.42 * b * b)
        sway = math.sin(t * 13 + i * 1.7) * rx * 0.035
        tip = QPointF(x + tip_dx * rx + sway, y - ry - height * ry)
        left, right = QPointF(bx - half * rx, by + ry * 0.12), QPointF(bx + half * rx, by + ry * 0.12)
        spike = QPainterPath(left)
        spike.quadTo(QPointF((left.x() + tip.x()) / 2 - rx * 0.06, (left.y() + tip.y()) / 2), tip)
        spike.quadTo(QPointF((right.x() + tip.x()) / 2 + rx * 0.06, (right.y() + tip.y()) / 2), right)
        spike.closeSubpath()
        hair = hair.united(spike)
    bang = QPainterPath(QPointF(x - rx * 0.32, y - ry * 0.55))  # the one strand over the forehead
    bang.quadTo(QPointF(x - rx * 0.05, y - ry * 0.48), QPointF(x - rx * 0.1, y - ry * 0.08))
    bang.quadTo(QPointF(x + rx * 0.02, y - ry * 0.4), QPointF(x + rx * 0.12, y - ry * 0.58))
    bang.closeSubpath()
    return hair.united(bang)


def paint_hair(p: QPainter, c: QPointF, rx: float, ry: float, s: float, t: float, scale: float) -> None:
    if scale <= 0.001:
        return
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    root = QPointF(c.x(), c.y() - ry * 0.7)
    p.translate(root)
    p.scale(scale, scale)
    p.translate(-root)
    hair = _hair_path(c, rx, ry, t)
    shade = QLinearGradient(QPointF(c.x(), c.y() - ry * 2.7), QPointF(c.x(), c.y() - ry * 0.2))
    shade.setColorAt(0.0, GOLD_LIGHT)
    shade.setColorAt(0.5, GOLD)
    shade.setColorAt(1.0, GOLD_DEEP)
    p.setBrush(shade)
    p.setPen(QPen(OUTLINE, max(1.0, s * 0.03), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.drawPath(hair)
    p.setPen(QPen(QColor(255, 255, 255, 170), max(0.8, s * 0.018), Qt.SolidLine, Qt.RoundCap))
    for b, tip_dx, height, _half in SPIKES[1:5]:  # a shine streak up a few spikes
        bx = c.x() + b * rx * 0.86
        p.drawLine(QPointF(bx - rx * 0.05, c.y() - ry * 1.05),
                   QPointF(bx + (tip_dx * rx - b * rx * 0.86) * 0.35 - rx * 0.05, c.y() - ry - height * ry * 0.45))
    p.restore()


def paint_eye_fire(p: QPainter, c: QPointF, r: float, side: int, s: float, t: float, level: float) -> None:
    """Fire in his eyes: a gold glint, and a little flame flickering up from the outer corner."""
    if level <= 0.001:
        return
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    glint = QColor(GOLD)
    glint.setAlphaF(min(1.0, level))
    p.setBrush(glint)
    p.drawEllipse(QPointF(c.x() - r * 0.25, c.y() - r * 0.15), r * 0.38, r * 0.38)
    height = r * (2.4 + 0.5 * math.sin(t * 19 + side)) * min(1.0, level)
    base = QPointF(c.x() + side * r * 0.75, c.y() - r * 0.45)
    p.translate(base)
    p.rotate(side * 22)
    flame = QPainterPath(QPointF(-r * 0.55, 0))
    flame.cubicTo(QPointF(-r * 0.6, -height * 0.5), QPointF(-r * 0.05, -height * 0.7), QPointF(0, -height))
    flame.cubicTo(QPointF(r * 0.2, -height * 0.6), QPointF(r * 0.6, -height * 0.45), QPointF(r * 0.55, 0))
    flame.closeSubpath()
    fire = QLinearGradient(QPointF(0, 0), QPointF(0, -height))
    hot = QColor(GOLD_LIGHT)
    hot.setAlphaF(min(1.0, level))
    warm = QColor(EMBER)
    warm.setAlphaF(min(1.0, level) * 0.95)
    tip = QColor("#ff7a2e")
    tip.setAlphaF(min(1.0, level) * 0.7)
    fire.setColorAt(0.0, hot)
    fire.setColorAt(0.5, warm)
    fire.setColorAt(1.0, tip)
    p.setBrush(fire)
    p.drawPath(flame)
    p.restore()
