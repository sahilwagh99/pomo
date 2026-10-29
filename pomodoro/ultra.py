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
BOLT_GLOW = QColor(120, 200, 255, 190)
BOLT_CORE = QColor("#f4fbff")



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
    """Thin white lightning crackling up and down around him, jagged and branching, a few at a time."""
    slot_len = 0.15
    for lane in range(4):
        offset = lane * 0.041
        slot = int((t + offset) / slot_len)
        into = (t + offset) % slot_len
        h = (slot * 2654435761 + lane * 40503) & 0xFFFFFFFF
        if into > 0.11 or h % 5 == 0:
            continue
        side = -1 if h & 1 else 1
        x0 = c.x() + side * rx * (0.75 + 0.55 * ((h >> 3) % 100) / 100)
        y0 = c.y() + ry * (0.9 - 2.6 * ((h >> 10) % 100) / 100)
        down = 1 if (h >> 17) & 1 else -1
        length = s * (0.45 + 0.4 * ((h >> 20) % 100) / 100) * level
        path = QPainterPath(QPointF(x0, y0))
        point = QPointF(x0, y0)
        branch_at = None
        steps = 9
        for k in range(steps):
            jag = (((h >> (k % 24)) * 7919 + k * 104729) % 1000) / 1000 - 0.5
            point = QPointF(point.x() + jag * s * 0.11, point.y() + down * length / steps)
            path.lineTo(point)
            if k == 4:
                branch_at = QPointF(point)
        if branch_at is not None:  # a fork off the middle
            twig = QPainterPath(branch_at)
            q = QPointF(branch_at)
            for k in range(3):
                jag = (((h >> (k + 5)) * 31 + k * 977) % 100) / 100 - 0.5
                q = QPointF(q.x() + side * s * 0.05 + jag * s * 0.04, q.y() + down * s * 0.05)
                twig.lineTo(q)
            path.addPath(twig)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(BOLT_GLOW, max(2.0, s * 0.05), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(path)
        p.setPen(QPen(BOLT_CORE, max(1.0, s * 0.018), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(path)


def paint_rim(p: QPainter, body: QPainterPath, s: float, level: float) -> None:
    """A bright glow hugging his outline, as power pours off him."""
    if level <= 0.001:
        return
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(Qt.NoBrush)
    for width, alpha in ((0.16, 0.25), (0.08, 0.55)):
        glow = QColor(GOLD_LIGHT)
        glow.setAlphaF(min(1.0, alpha * level))
        p.setPen(QPen(glow, s * width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(body)
    p.restore()


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
# Each spike: (angle from straight up in degrees, where it grows from across the head -1..1,
# length and half-width at the root, in body radii). Tall in the middle, fanning out to the sides.
BACK_SPIKES = ((-62, -0.94, 0.80, 0.30), (-38, -0.66, 1.40, 0.38), (-24, -0.40, 1.95, 0.40),
               (-9, -0.13, 2.45, 0.40), (6, 0.13, 2.35, 0.40), (21, 0.40, 2.00, 0.39),
               (36, 0.66, 1.50, 0.37), (60, 0.94, 0.85, 0.30))
FRONT_SPIKES = ((-30, -0.58, 1.25, 0.32), (-17, -0.30, 1.70, 0.34), (-4, -0.04, 2.00, 0.34),
                (10, 0.22, 1.85, 0.33), (24, 0.48, 1.45, 0.32), (46, 0.78, 0.95, 0.27))
# the bangs: (root x, root y, tip x, tip y, half-width), in body radii from his center
BANGS = ((-0.30, -0.62, -0.10, -0.10, 0.17), (-0.06, -0.66, 0.16, -0.24, 0.15),
         (-0.72, -0.52, -0.98, -0.02, 0.15), (0.62, -0.56, 0.90, -0.08, 0.15))


def _spike(c: QPointF, rx: float, ry: float, angle: float, b: float, length: float, half: float,
           sway: float) -> tuple[QPainterPath, QPointF, QPointF]:
    """One tapered spike from the head, curving outward a little toward its tip. Returns it, its root and tip."""
    bx = c.x() + b * rx * 0.84
    by = c.y() - ry * (0.96 - 0.48 * b * b)
    a = math.radians(angle + sway)
    n = QPointF(math.cos(a), math.sin(a))  # across the spike
    curl = math.radians(angle * 0.12)  # the tips lean a little further out
    tip = QPointF(bx + math.sin(a + curl) * length * ry, by - math.cos(a + curl) * length * ry)
    left = QPointF(bx - n.x() * half * rx, by - n.y() * half * rx)
    right = QPointF(bx + n.x() * half * rx, by + n.y() * half * rx)
    bulge = half * rx * 0.55
    mid_l = QPointF((left.x() + tip.x()) / 2 - n.x() * bulge, (left.y() + tip.y()) / 2 - n.y() * bulge)
    mid_r = QPointF((right.x() + tip.x()) / 2 + n.x() * bulge * 0.4, (right.y() + tip.y()) / 2 + n.y() * bulge * 0.4)
    path = QPainterPath(left)
    path.quadTo(mid_l, tip)
    path.quadTo(mid_r, right)
    path.closeSubpath()
    return path, QPointF(bx, by), tip


def _hairline(c: QPointF, rx: float, ry: float) -> QPainterPath:
    """The hair over the top of his head, with a jagged edge across the forehead."""
    x, y = c.x(), c.y()
    path = QPainterPath()
    for i in range(25):  # over the top, from the left temple to the right
        a = math.pi * (1.0 - i / 24)
        point = QPointF(x + math.cos(a) * rx * 1.0, y - ry * 0.32 - math.sin(a) * ry * 0.78)
        if i == 0:
            path.moveTo(point)
        else:
            path.lineTo(point)
    for fx, fy in ((0.92, -0.18), (0.74, -0.44), (0.56, -0.30), (0.38, -0.52), (0.16, -0.36), (-0.06, -0.56),
                   (-0.26, -0.38), (-0.46, -0.54), (-0.64, -0.32), (-0.82, -0.46), (-1.0, -0.32)):
        path.lineTo(QPointF(x + fx * rx, y + fy * ry))
    path.closeSubpath()
    return path


def _bang(c: QPointF, rx: float, ry: float, root_x, root_y, tip_x, tip_y, half) -> QPainterPath:
    x, y = c.x(), c.y()
    root, tip = QPointF(x + root_x * rx, y + root_y * ry), QPointF(x + tip_x * rx, y + tip_y * ry)
    path = QPainterPath(QPointF(root.x() - half * rx, root.y()))
    path.quadTo(QPointF(root.x() - half * rx * 0.6, (root.y() + tip.y()) / 2), tip)
    path.quadTo(QPointF(root.x() + half * rx * 1.2, (root.y() + tip.y()) / 2 - ry * 0.05),
                QPointF(root.x() + half * rx, root.y()))
    path.closeSubpath()
    return path


def paint_hair(p: QPainter, c: QPointF, rx: float, ry: float, s: float, t: float, scale: float) -> None:
    """Tall golden spikes in two layers, bangs over the forehead, a glowing rim and a few shine streaks."""
    if scale <= 0.001:
        return
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    root = QPointF(c.x(), c.y() - ry * 0.7)
    p.translate(root)
    p.scale(scale, scale)
    p.translate(-root)

    def sway(i: int) -> float:
        return math.sin(t * 12 + i * 1.9) * 2.2  # degrees: the aura's wind

    back = QPainterPath()
    for i, spec in enumerate(BACK_SPIKES):
        back = back.united(_spike(c, rx, ry, *spec, sway(i))[0])
    front = _hairline(c, rx, ry)
    tips = []
    for i, spec in enumerate(FRONT_SPIKES):
        spike, base, tip = _spike(c, rx, ry, *spec, sway(i + 9))
        front = front.united(spike)
        tips.append((base, tip))
    bangs = QPainterPath()
    for spec in BANGS:
        bangs = bangs.united(_bang(c, rx, ry, *spec))

    whole = back.united(front).united(bangs)
    glow = QColor(GOLD_LIGHT)
    glow.setAlphaF(0.45)
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(glow, s * 0.1, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.drawPath(whole)  # the glow around the hair

    line = QPen(OUTLINE, max(1.0, s * 0.026), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    top, bottom = QPointF(c.x(), c.y() - ry * 3.2), QPointF(c.x(), c.y() - ry * 0.2)
    deep = QLinearGradient(top, bottom)
    deep.setColorAt(0.0, GOLD)
    deep.setColorAt(1.0, GOLD_DEEP)
    p.setPen(line)
    p.setBrush(deep)
    p.drawPath(back)
    bright = QLinearGradient(top, bottom)
    bright.setColorAt(0.0, GOLD_LIGHT)
    bright.setColorAt(0.55, QColor("#ffe14d"))
    bright.setColorAt(1.0, GOLD)
    p.setBrush(bright)
    p.drawPath(front.united(bangs))
    p.setPen(QPen(QColor(255, 255, 255, 190), max(0.8, s * 0.018), Qt.SolidLine, Qt.RoundCap))
    for base, tip in tips[1:6]:  # streaks of light up the front spikes
        p.drawLine(QPointF(base.x() + (tip.x() - base.x()) * 0.22, base.y() + (tip.y() - base.y()) * 0.22),
                   QPointF(base.x() + (tip.x() - base.x()) * 0.58, base.y() + (tip.y() - base.y()) * 0.58))
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
