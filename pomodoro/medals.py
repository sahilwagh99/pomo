"""Rank medals for the habit tracker, drawn in code.

Each medal is a metal disc with an emblem, and stars along the bottom of its rim (one to five). The frame
grows as you climb: studs, then spikes, wings, a gem, a crown and sun rays, and flames around the top
medal, which shows a number instead of stars. The ladder and the look are inspired by the ranked medals
of competitive games; the art is Pomo's own.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient

from .habits import STAR_POINTS, TOP, Rank
from .theme import FONT_FAMILY

# light, base, dark metal; then the emblem
TIERS = (
    ("#efd6b8", "#c99a6b", "#87593b", "shield"),       # Rookie: bronze
    ("#f1f4ee", "#b9c5b3", "#72826b", "shield_band"),  # Squire: silver sage
    ("#d6f1e9", "#86c7b8", "#47857a", "sword"),        # Knight: teal
    ("#dde9fb", "#93b6e6", "#52739f", "swords"),       # Paladin: sky blue
    ("#ebe2fb", "#b5a1e0", "#6f5ca1", "crown"),        # Champion: lavender
    ("#fcdee7", "#ec9fb6", "#a1536f", "gem"),          # Mythic: rose
    ("#fdf1c6", "#efc55a", "#a87a22", "sparkle"),      # Celestial: gold
    ("#fdd9b8", "#f0955a", "#a4512a", "flame"),        # Eternal: flame orange (red means points at risk)
)
LINE = QColor(91, 44, 41, 215)       # Pomo's cocoa line art
STAR = QColor("#ffe48a")
STAR_LINE = QColor("#9a6b16")
FLAME = (QColor("#f8b04f"), QColor("#fbd56c"))


def color(medal: int) -> QColor:
    """The medal's metal: progress bars use it, so they match the badge."""
    return QColor(TIERS[max(0, min(TOP, medal))][1])


def deep(medal: int) -> QColor:
    """A darker step of the metal, for fills that sit on light surfaces."""
    return QColor(TIERS[max(0, min(TOP, medal))][2])


def bar_color(medal: int, dark: bool) -> QColor:
    """The rank's progress color: the medal's metal, a step deeper on light themes so it reads on cream."""
    base, low = color(medal), deep(medal)
    if dark:
        return base
    return QColor((base.red() * 3 + low.red() * 2) // 5, (base.green() * 3 + low.green() * 2) // 5,
                  (base.blue() * 3 + low.blue() * 2) // 5)


def progress_parts(standing) -> tuple[float, float]:
    """(solid, open end) as shares of the current star. The solid part is the points you have. Today's open
    match reaches past it when the day would gain points, and falls short of it (the end of the fill is at
    risk) when the day would lose them."""
    have = standing.rank.fraction
    change = standing.pending / STAR_POINTS
    if change >= 0:
        return have, min(1.0, have + change)
    return max(0.0, have + change), have


def _open_color(standing, fill: QColor, danger: QColor) -> QColor:
    out = QColor(fill) if standing.pending > 0 else QColor(danger)
    out.setAlpha(115 if standing.pending > 0 else 170)
    return out


def paint_arc(p: QPainter, rect: QRectF, start: float, span: float, width: float, standing, dark: bool,
              danger: QColor) -> None:
    """The rank's progress as an arc, filling from `start` through `span` (degrees, Qt's way round)."""
    fill = bar_color(standing.rank.medal, dark)
    track = QColor(fill)
    track.setAlpha(70 if dark else 55)
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(track, width, Qt.SolidLine, Qt.RoundCap))
    p.drawArc(rect, round(start * 16), round(span * 16))
    solid, end = progress_parts(standing)
    if end - solid > 0.004:
        p.setPen(QPen(_open_color(standing, fill, danger), width, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(rect, round(start * 16), round(span * end * 16))
    if solid > 0.004:
        p.setPen(QPen(fill, width, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(rect, round(start * 16), round(span * solid * 16))


def paint_bar(p: QPainter, rect: QRectF, standing, dark: bool, danger: QColor) -> None:
    """The rank's progress as a rounded bar, filling left to right."""
    fill = bar_color(standing.rank.medal, dark)
    track = QColor(fill)
    track.setAlpha(70 if dark else 55)
    r = rect.height() / 2
    p.setPen(Qt.NoPen)
    p.setBrush(track)
    p.drawRoundedRect(rect, r, r)
    solid, end = progress_parts(standing)
    for share, brush in ((end, _open_color(standing, fill, danger)), (solid, fill)):
        if share > 0.004 and (brush is fill or end - solid > 0.004):
            p.setBrush(brush)
            p.drawRoundedRect(QRectF(rect.left(), rect.top(), max(rect.height(), rect.width() * share),
                                     rect.height()), r, r)


def extent(medal: int) -> tuple[float, float, float]:
    """How far the medal reaches, in disc radii: (to each side, above the center, below the center)."""
    side = (1.0, 1.0, 1.25, 1.38, 1.48, 1.52, 1.58, 1.6)[medal]
    top = (1.0, 1.0, 1.25, 1.05, 1.32, 1.32, 1.42, 1.52)[medal]
    return side, top, 1.13


def _star(c: QPointF, outer: float, inner_ratio: float = 0.46) -> QPainterPath:
    path = QPainterPath()
    for i in range(10):
        a = math.pi * i / 5 - math.pi / 2
        rr = outer if i % 2 == 0 else outer * inner_ratio
        point = QPointF(c.x() + math.cos(a) * rr, c.y() + math.sin(a) * rr)
        path.moveTo(point) if i == 0 else path.lineTo(point)
    path.closeSubpath()
    return path


def _line(r: float, scale: float = 0.075) -> QPen:
    return QPen(LINE, max(0.8, r * scale), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)


def paint_medal(p: QPainter, c: QPointF, r: float, rank: Rank, faded: bool = False) -> None:
    """The medal for a rank, its disc `r` across from the center (ornaments reach further, see extent)."""
    light_hex, base_hex, dark_hex, emblem = TIERS[rank.medal]
    light, base, dark = QColor(light_hex), QColor(base_hex), QColor(dark_hex)
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    if faded:
        p.setOpacity(p.opacity() * 0.45)
    tier = rank.medal
    detailed = r >= 9

    if tier == TOP:
        _flames(p, c, r)
    if tier >= 6:
        _rays(p, c, r, light)
    if tier >= 3:
        _wings(p, c, r, tier, light, base)
    if tier == 2:
        _spikes(p, c, r, base)
    if 4 <= tier < 6:
        _top_gem(p, c, r, tier, light)

    # the disc: a metal ring around a deeper center
    ring = QLinearGradient(QPointF(c.x() - r, c.y() - r), QPointF(c.x() + r, c.y() + r))
    ring.setColorAt(0.0, light)
    ring.setColorAt(0.45, base)
    ring.setColorAt(1.0, dark)
    p.setPen(_line(r))
    p.setBrush(ring)
    p.drawEllipse(c, r, r)
    inner = QRadialGradient(QPointF(c.x() - r * 0.25, c.y() - r * 0.3), r * 1.1)
    inner.setColorAt(0.0, base.lighter(108))
    inner.setColorAt(1.0, dark)
    p.setPen(QPen(QColor(dark.darker(130)), max(0.6, r * 0.04)))
    p.setBrush(inner)
    p.drawEllipse(c, r * 0.74, r * 0.74)
    if tier == 1 and detailed:
        p.setPen(Qt.NoPen)
        p.setBrush(light)
        for i in range(8):
            a = math.pi / 8 + i * math.pi / 4
            p.drawEllipse(QPointF(c.x() + math.cos(a) * r * 0.87, c.y() + math.sin(a) * r * 0.87), r * 0.06, r * 0.06)
    if tier >= 6:
        _crown(p, QPointF(c.x(), c.y() - r * 0.98), r * 0.5)

    _emblem(p, emblem, QPointF(c.x(), c.y() - r * 0.06), r * 0.44, light, dark)
    if rank.stars:
        _stars(p, c, r, rank.stars)
    elif rank.number:
        _plate(p, c, r, rank.number, dark)
    p.restore()


# ------------------------------------------------------------------ ornaments
def _flames(p: QPainter, c: QPointF, r: float) -> None:
    outer, inner = FLAME
    for color_, reach, width in ((outer, 1.5, 0.42), (inner, 1.28, 0.3)):
        path = QPainterPath()
        n = 9
        for i in range(n):
            a = math.pi + math.pi * i / (n - 1)  # from the left, over the top, to the right
            tip = QPointF(c.x() + math.cos(a) * r * reach, c.y() + math.sin(a) * r * reach)
            left = QPointF(c.x() + math.cos(a - width / 2) * r * 0.85, c.y() + math.sin(a - width / 2) * r * 0.85)
            right = QPointF(c.x() + math.cos(a + width / 2) * r * 0.85, c.y() + math.sin(a + width / 2) * r * 0.85)
            lean = QPointF(math.cos(a + 0.35) * r * 0.35, math.sin(a + 0.35) * r * 0.35)
            path.moveTo(left)
            path.quadTo(tip - lean * 0.2 + (left - c) * 0.1, tip)
            path.quadTo(tip + lean * 0.2 + (right - c) * 0.1, right)
            path.closeSubpath()
        p.setPen(Qt.NoPen)
        p.setBrush(color_)
        p.drawPath(path)


def _rays(p: QPainter, c: QPointF, r: float, light: QColor) -> None:
    """Sun rays behind the top medals, in the medal's light metal (they read on light and dark themes)."""
    path = QPainterPath()
    for i in range(12):
        a = -math.pi / 2 + (i + 0.5) * math.pi / 6
        tip = QPointF(c.x() + math.cos(a) * r * 1.34, c.y() + math.sin(a) * r * 1.34)
        side = 0.1
        path.moveTo(QPointF(c.x() + math.cos(a - side) * r * 0.8, c.y() + math.sin(a - side) * r * 0.8))
        path.lineTo(tip)
        path.lineTo(QPointF(c.x() + math.cos(a + side) * r * 0.8, c.y() + math.sin(a + side) * r * 0.8))
        path.closeSubpath()
    soft = QColor(light)
    soft.setAlpha(200)
    p.setPen(QPen(QColor(LINE.red(), LINE.green(), LINE.blue(), 90), max(0.6, r * 0.03)))
    p.setBrush(soft)
    p.drawPath(path)


def _spikes(p: QPainter, c: QPointF, r: float, base: QColor) -> None:
    p.setPen(_line(r, 0.06))
    p.setBrush(base)
    for a in (-math.pi / 4, -3 * math.pi / 4, math.pi / 4, 3 * math.pi / 4, -math.pi / 2):
        tip = QPointF(c.x() + math.cos(a) * r * 1.22, c.y() + math.sin(a) * r * 1.22)
        side = 0.26
        path = QPainterPath(QPointF(c.x() + math.cos(a - side) * r * 0.86, c.y() + math.sin(a - side) * r * 0.86))
        path.lineTo(tip)
        path.lineTo(QPointF(c.x() + math.cos(a + side) * r * 0.86, c.y() + math.sin(a + side) * r * 0.86))
        path.closeSubpath()
        p.drawPath(path)


def _wings(p: QPainter, c: QPointF, r: float, tier: int, light: QColor, base: QColor) -> None:
    """Feathered wings behind the disc, growing with the rank."""
    feathers = {3: 3, 4: 3, 5: 4}.get(tier, 5)
    reach = {3: 0.78, 4: 0.88, 5: 0.92}.get(tier, 0.98)
    for side in (-1, 1):
        for i in reversed(range(feathers)):  # the lowest feather in front
            angle = -6 + i * (66 / (feathers - 1))  # degrees above horizontal, fanning upward
            length = r * reach * (1.0 - 0.12 * i)
            width = r * 0.4 * (1.0 - 0.1 * i)
            p.save()
            p.translate(QPointF(c.x() + side * r * 0.56, c.y() - r * 0.08))
            p.rotate(-angle if side > 0 else angle - 180)
            grad = QLinearGradient(QPointF(0, 0), QPointF(length, 0))
            grad.setColorAt(0.0, base)
            grad.setColorAt(1.0, light)
            p.setPen(_line(r, 0.055))
            p.setBrush(grad)
            path = QPainterPath(QPointF(0, -width * 0.5))
            path.cubicTo(QPointF(length * 0.55, -width * 0.62), QPointF(length * 0.95, -width * 0.3), QPointF(length, 0))
            path.cubicTo(QPointF(length * 0.9, width * 0.42), QPointF(length * 0.5, width * 0.5), QPointF(0, width * 0.5))
            path.closeSubpath()
            p.drawPath(path)
            p.restore()


def _top_gem(p: QPainter, c: QPointF, r: float, tier: int, light: QColor) -> None:
    tip = c.y() - r * 1.3
    path = QPainterPath(QPointF(c.x(), tip))
    path.lineTo(QPointF(c.x() + r * 0.22, c.y() - r * 0.9))
    path.lineTo(QPointF(c.x(), c.y() - r * 0.72))
    path.lineTo(QPointF(c.x() - r * 0.22, c.y() - r * 0.9))
    path.closeSubpath()
    p.setPen(_line(r, 0.06))
    p.setBrush(light if tier == 4 else QColor("#fbe3ec"))
    p.drawPath(path)


def _crown(p: QPainter, c: QPointF, s: float) -> None:
    """A small crown sitting on the medal's top edge, `s` wide from its center."""
    path = QPainterPath(QPointF(c.x() - s, c.y() + s * 0.3))
    path.lineTo(QPointF(c.x() - s * 1.05, c.y() - s * 0.55))
    path.lineTo(QPointF(c.x() - s * 0.5, c.y() - s * 0.08))
    path.lineTo(QPointF(c.x(), c.y() - s * 0.82))
    path.lineTo(QPointF(c.x() + s * 0.5, c.y() - s * 0.08))
    path.lineTo(QPointF(c.x() + s * 1.05, c.y() - s * 0.55))
    path.lineTo(QPointF(c.x() + s, c.y() + s * 0.3))
    path.closeSubpath()
    grad = QLinearGradient(QPointF(c.x(), c.y() - s), QPointF(c.x(), c.y() + s * 0.4))
    grad.setColorAt(0.0, QColor("#fff2bf"))
    grad.setColorAt(1.0, QColor("#e9b949"))
    p.setPen(_line(s * 2, 0.05))
    p.setBrush(grad)
    p.drawPath(path)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#f28b9b"))
    p.drawEllipse(QPointF(c.x(), c.y() - s * 0.05), s * 0.16, s * 0.16)


# ------------------------------------------------------------------ emblem, stars, number
def _emblem(p: QPainter, name: str, c: QPointF, s: float, light: QColor, dark: QColor) -> None:
    fill = QColor(light)
    pen = _line(s * 2.2, 0.05)
    p.setPen(pen)
    p.setBrush(fill)
    x, y = c.x(), c.y()
    if name in ("shield", "shield_band"):
        path = QPainterPath(QPointF(x - s * 0.72, y - s * 0.78))
        path.quadTo(QPointF(x, y - s * 0.98), QPointF(x + s * 0.72, y - s * 0.78))
        path.lineTo(QPointF(x + s * 0.72, y - s * 0.05))
        path.quadTo(QPointF(x + s * 0.62, y + s * 0.62), QPointF(x, y + s * 0.95))
        path.quadTo(QPointF(x - s * 0.62, y + s * 0.62), QPointF(x - s * 0.72, y - s * 0.05))
        path.closeSubpath()
        p.drawPath(path)
        if name == "shield_band":
            p.save()
            p.setClipPath(path)
            p.setPen(Qt.NoPen)
            p.setBrush(dark)
            p.drawRect(QRectF(x - s, y - s * 0.22, 2 * s, s * 0.38))
            p.restore()
    elif name in ("sword", "swords"):
        for tilt in ((0,) if name == "sword" else (-38, 38)):
            p.save()
            p.translate(c)
            p.rotate(tilt)
            blade = QPainterPath(QPointF(0, -s * 1.02))
            blade.lineTo(QPointF(s * 0.17, -s * 0.78))
            blade.lineTo(QPointF(s * 0.17, s * 0.42))
            blade.lineTo(QPointF(-s * 0.17, s * 0.42))
            blade.lineTo(QPointF(-s * 0.17, -s * 0.78))
            blade.closeSubpath()
            p.setBrush(fill)
            p.drawPath(blade)
            p.setBrush(dark)
            p.drawRoundedRect(QRectF(-s * 0.48, s * 0.4, s * 0.96, s * 0.18), s * 0.08, s * 0.08)
            p.drawRoundedRect(QRectF(-s * 0.1, s * 0.56, s * 0.2, s * 0.36), s * 0.06, s * 0.06)
            p.restore()
    elif name == "crown":
        _crown(p, QPointF(x, y + s * 0.22), s * 0.82)
    elif name == "gem":
        path = QPainterPath(QPointF(x - s * 0.8, y - s * 0.28))
        path.lineTo(QPointF(x - s * 0.42, y - s * 0.78))
        path.lineTo(QPointF(x + s * 0.42, y - s * 0.78))
        path.lineTo(QPointF(x + s * 0.8, y - s * 0.28))
        path.lineTo(QPointF(x, y + s * 0.92))
        path.closeSubpath()
        p.drawPath(path)
        p.setPen(QPen(QColor(dark), max(0.6, s * 0.08)))
        p.drawLine(QPointF(x - s * 0.8, y - s * 0.28), QPointF(x + s * 0.8, y - s * 0.28))
        p.drawLine(QPointF(x - s * 0.3, y - s * 0.28), QPointF(x, y + s * 0.85))
        p.drawLine(QPointF(x + s * 0.3, y - s * 0.28), QPointF(x, y + s * 0.85))
    elif name == "sparkle":
        path = QPainterPath()
        for i in range(8):
            a = -math.pi / 2 + i * math.pi / 4
            rr = s * (1.0 if i % 2 == 0 else 0.34)
            point = QPointF(x + math.cos(a) * rr, y + math.sin(a) * rr)
            path.moveTo(point) if i == 0 else path.lineTo(point)
        path.closeSubpath()
        p.drawPath(path)
    elif name == "flame":
        path = QPainterPath(QPointF(x, y - s * 1.0))
        path.cubicTo(QPointF(x + s * 0.2, y - s * 0.45), QPointF(x + s * 0.78, y - s * 0.2), QPointF(x + s * 0.62, y + s * 0.42))
        path.cubicTo(QPointF(x + s * 0.48, y + s * 0.9), QPointF(x - s * 0.48, y + s * 0.9), QPointF(x - s * 0.62, y + s * 0.42))
        path.cubicTo(QPointF(x - s * 0.74, y + s * 0.02), QPointF(x - s * 0.4, y - s * 0.2), QPointF(x - s * 0.3, y - s * 0.5))
        path.quadTo(QPointF(x - s * 0.05, y - s * 0.3), QPointF(x, y - s * 1.0))
        path.closeSubpath()
        p.setBrush(FLAME[1])
        p.drawPath(path)
        p.setPen(Qt.NoPen)
        p.setBrush(FLAME[0])
        p.drawEllipse(QPointF(x, y + s * 0.42), s * 0.3, s * 0.36)


def _stars(p: QPainter, c: QPointF, r: float, count: int) -> None:
    """Earned stars along the bottom of the rim, centered, like the ranked medals they're inspired by."""
    spread = math.radians(25)
    size = r * 0.21
    for i in range(count):
        a = (i - (count - 1) / 2) * spread
        center = QPointF(c.x() + math.sin(a) * r * 0.9, c.y() + math.cos(a) * r * 0.9)
        p.setPen(QPen(STAR_LINE, max(0.6, r * 0.035), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(STAR)
        p.drawPath(_star(center, size))


def _plate(p: QPainter, c: QPointF, r: float, number: int, dark: QColor) -> None:
    text = str(number)
    w = r * (0.62 + 0.18 * len(text))
    plate = QRectF(c.x() - w / 2, c.y() + r * 0.62, w, r * 0.46)
    p.setPen(QPen(QColor("#fbd56c"), max(0.7, r * 0.05)))
    p.setBrush(QColor(dark).darker(115))
    p.drawRoundedRect(plate, r * 0.16, r * 0.16)
    f = QFont(FONT_FAMILY)
    f.setPixelSize(max(6, round(r * 0.36)))
    f.setWeight(QFont.Bold)
    p.setFont(f)
    p.setPen(QColor("#fff6dc"))
    p.drawText(plate, Qt.AlignCenter, text)


# ------------------------------------------------------------------ cached pictures
_CACHE: dict[tuple, QPixmap] = {}


def paint_cached(p: QPainter, c: QPointF, r: float, rank: Rank, ratio: float, faded: bool = False) -> None:
    """paint_medal from a picture drawn once and kept: the timer repaints often, the medal rarely changes."""
    side, top, bottom = extent(rank.medal)
    w, h = 2 * side * r + 4, (top + bottom) * r + 4
    key = (rank.medal, rank.stars, rank.number, round(r, 2), ratio, faded)
    pixmap = _CACHE.get(key)
    if pixmap is None:
        if len(_CACHE) > 64:
            _CACHE.clear()
        pixmap = QPixmap(max(1, math.ceil(w * ratio)), max(1, math.ceil(h * ratio)))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        paint_medal(painter, QPointF(w / 2, top * r + 2), r, rank, faded)
        painter.end()
        _CACHE[key] = pixmap
    p.drawPixmap(QPointF(c.x() - w / 2, c.y() - top * r - 2), pixmap)
