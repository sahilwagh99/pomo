"""Vector glyphs and the app/tray icon, painted with QPainter (no image assets)."""
from __future__ import annotations

import math
import struct
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap


def _pen(color: QColor, width: float) -> QPen:
    return QPen(color, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)


def draw_equalizer(p: QPainter, c: QPointF, size: float, color: QColor, t: float, moving: bool = True) -> None:
    """Three little level bars: bouncing while music plays, resting when it's paused."""
    s = size
    w = s * 0.16
    base = c.y() + s * 0.32
    p.save()
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    for k, (rate, phase, rest) in enumerate(((7.1, 0.0, 0.3), (9.3, 1.7, 0.5), (6.2, 3.1, 0.38))):
        level = 0.22 + 0.42 * abs(math.sin(t * rate + phase)) if moving else rest
        h = s * level
        x = c.x() + (k - 1) * s * 0.26
        p.drawRoundedRect(QRectF(x - w / 2, base - h, w, h), w * 0.45, w * 0.45)
    p.restore()


def draw_glyph(p: QPainter, name: str, c: QPointF, size: float, color: QColor) -> None:
    """Draw a line icon centered on c, fitting a size x size box."""
    s = size
    p.save()
    p.setBrush(Qt.NoBrush)
    stroke = max(1.3, s * 0.11)
    if name == "play":
        path = QPainterPath(QPointF(c.x() - s * 0.24, c.y() - s * 0.32))
        path.lineTo(c.x() + s * 0.34, c.y())
        path.lineTo(c.x() - s * 0.24, c.y() + s * 0.32)
        path.closeSubpath()
        p.setPen(_pen(color, s * 0.12))
        p.setBrush(color)
        p.drawPath(path)
    elif name == "pause":
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        w, h = s * 0.2, s * 0.62
        for dx in (-0.17, 0.17):
            p.drawRoundedRect(QRectF(c.x() + dx * s - w / 2, c.y() - h / 2, w, h), w * 0.35, w * 0.35)
    elif name == "reset":
        r = s * 0.33
        p.setPen(_pen(color, stroke))
        rect = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
        # arc runs counter-clockwise from the top and ends at 3 o'clock heading up
        p.drawArc(rect, 70 * 16, 290 * 16)
        h = s * 0.16
        tip = QPointF(c.x() + r, c.y() - h * 0.35)
        path = QPainterPath(QPointF(tip.x() - h * 0.85, tip.y() + h * 0.8))
        path.lineTo(tip)
        path.lineTo(tip.x() + h * 0.85, tip.y() + h * 0.8)
        p.drawPath(path)
    elif name in ("skip", "previous"):  # previous is skip, mirrored
        d = 1 if name == "skip" else -1
        path = QPainterPath(QPointF(c.x() - d * s * 0.28, c.y() - s * 0.28))
        path.lineTo(c.x() + d * s * 0.16, c.y())
        path.lineTo(c.x() - d * s * 0.28, c.y() + s * 0.28)
        path.closeSubpath()
        p.setPen(_pen(color, s * 0.1))
        p.setBrush(color)
        p.drawPath(path)
        p.setPen(_pen(color, stroke * 1.1))
        p.drawLine(QPointF(c.x() + d * s * 0.3, c.y() - s * 0.28), QPointF(c.x() + d * s * 0.3, c.y() + s * 0.28))
    elif name == "note":  # an eighth note
        head = QPointF(c.x() - s * 0.13, c.y() + s * 0.22)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.save()
        p.translate(head)
        p.rotate(-24)
        p.drawEllipse(QPointF(0, 0), s * 0.165, s * 0.12)
        p.restore()
        x = head.x() + s * 0.14
        p.setPen(_pen(color, stroke))
        p.drawLine(QPointF(x, head.y() - s * 0.03), QPointF(x, c.y() - s * 0.36))
        flag = QPainterPath(QPointF(x, c.y() - s * 0.36))
        flag.cubicTo(x + s * 0.04, c.y() - s * 0.2, x + s * 0.3, c.y() - s * 0.2, x + s * 0.2, c.y() + s * 0.03)
        p.setBrush(Qt.NoBrush)
        p.drawPath(flag)
    elif name == "chart":
        p.setPen(_pen(color, stroke * 1.15))
        base = c.y() + s * 0.32
        for dx, h in ((-0.27, 0.34), (0.0, 0.64), (0.27, 0.46)):
            x = c.x() + dx * s
            p.drawLine(QPointF(x, base), QPointF(x, base - h * s))
    elif name == "gear":
        p.setPen(_pen(color, stroke))
        p.drawEllipse(c, s * 0.24, s * 0.24)
        p.drawEllipse(c, s * 0.08, s * 0.08)
        for k in range(8):
            a = math.radians(k * 45 + 22.5)
            p.drawLine(QPointF(c.x() + math.cos(a) * s * 0.3, c.y() + math.sin(a) * s * 0.3),
                       QPointF(c.x() + math.cos(a) * s * 0.42, c.y() + math.sin(a) * s * 0.42))
    elif name == "chat":
        w, h = s * 0.7, s * 0.48
        rect = QRectF(c.x() - w / 2, c.y() - h / 2 - s * 0.06, w, h)
        bubble = QPainterPath()
        bubble.addRoundedRect(rect, h * 0.45, h * 0.45)
        tail = QPainterPath(QPointF(rect.left() + w * 0.2, rect.bottom() - 1))
        tail.lineTo(rect.left() + w * 0.12, rect.bottom() + s * 0.17)
        tail.lineTo(rect.left() + w * 0.44, rect.bottom() - 1)
        tail.closeSubpath()
        p.setPen(_pen(color, stroke))
        p.drawPath(bubble.united(tail))
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        for dx in (-0.17, 0.0, 0.17):
            p.drawEllipse(QPointF(c.x() + dx * s, rect.center().y()), s * 0.045, s * 0.045)
    elif name == "island":  # a screen with the island pill at its top edge
        w, h = s * 0.82, s * 0.6
        rect = QRectF(c.x() - w / 2, c.y() - h / 2, w, h)
        p.setPen(_pen(color, stroke))
        p.drawRoundedRect(rect, s * 0.08, s * 0.08)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(QRectF(c.x() - s * 0.17, rect.top(), s * 0.34, s * 0.13), s * 0.065, s * 0.065)
    elif name == "orb":  # a round timer with its progress arc
        r = s * 0.34
        rect = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
        p.setPen(_pen(color, stroke))
        p.drawEllipse(rect)
        p.setPen(_pen(color, stroke * 2.0))
        p.drawArc(rect, 90 * 16, -200 * 16)
    elif name == "dango":  # three mochi balls on a skewer (the treat)
        p.setPen(_pen(color, stroke * 1.1))
        p.drawLine(QPointF(c.x() + s * 0.38, c.y() + s * 0.38), QPointF(c.x() - s * 0.4, c.y() - s * 0.4))
        p.setPen(_pen(color, max(1.0, stroke * 0.8)))
        r = s * 0.15
        for k, fill in ((-0.24, "#f6a9bd"), (0.0, "#fffdf8"), (0.24, "#a6cf92")):
            p.setBrush(QColor(fill))
            p.drawEllipse(QPointF(c.x() + k * s, c.y() + k * s), r, r)
    elif name == "petal":  # a sakura petal, notched at the top
        petal = QPainterPath(QPointF(c.x(), c.y() + s * 0.36))
        petal.cubicTo(c.x() - s * 0.42, c.y() + s * 0.05, c.x() - s * 0.3, c.y() - s * 0.4, c.x() - s * 0.08, c.y() - s * 0.32)
        petal.lineTo(c.x(), c.y() - s * 0.2)
        petal.lineTo(c.x() + s * 0.08, c.y() - s * 0.32)
        petal.cubicTo(c.x() + s * 0.3, c.y() - s * 0.4, c.x() + s * 0.42, c.y() + s * 0.05, c.x(), c.y() + s * 0.36)
        p.setPen(_pen(color, max(1.0, stroke * 0.8)))
        p.setBrush(QColor("#f6a9bd"))
        p.drawPath(petal)
    elif name == "headphones":  # focus sounds
        r = s * 0.31
        p.setPen(_pen(color, stroke))
        p.drawArc(QRectF(c.x() - r, c.y() - r * 1.1, 2 * r, 2 * r), 0, 180 * 16)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        for side in (-1, 1):
            p.drawRoundedRect(QRectF(c.x() + side * r - s * 0.1, c.y() - s * 0.06, s * 0.2, s * 0.32), s * 0.07, s * 0.07)
    elif name in ("bolt", "bolt_on"):  # Ultra focus (filled gold while it's on)
        bolt = QPainterPath(QPointF(c.x() + s * 0.1, c.y() - s * 0.42))
        for dx, dy in ((-0.24, 0.04), (-0.02, 0.04), (-0.12, 0.42), (0.24, -0.06), (0.02, -0.06)):
            bolt.lineTo(c.x() + dx * s, c.y() + dy * s)
        bolt.closeSubpath()
        p.setPen(_pen(color, stroke * 0.75))
        p.setBrush(QColor("#ffd23f") if name == "bolt_on" else Qt.NoBrush)
        p.drawPath(bolt)
    elif name in ("speaker", "speaker_off"):  # Pomo reads replies aloud (or doesn't)
        cone = QPainterPath(QPointF(c.x() - s * 0.38, c.y() - s * 0.11))
        for dx, dy in ((-0.2, -0.11), (0.0, -0.3), (0.0, 0.3), (-0.2, 0.11), (-0.38, 0.11)):
            cone.lineTo(c.x() + dx * s, c.y() + dy * s)
        cone.closeSubpath()
        p.setPen(_pen(color, stroke * 0.8))
        p.setBrush(color)
        p.drawPath(cone)
        p.setBrush(Qt.NoBrush)
        p.setPen(_pen(color, stroke))
        if name == "speaker":
            for r in (0.16, 0.3):
                p.drawArc(QRectF(c.x() + s * 0.02 - r * s, c.y() - r * s, 2 * r * s, 2 * r * s), -50 * 16, 100 * 16)
        else:
            d = s * 0.1
            x = c.x() + s * 0.24
            p.drawLine(QPointF(x - d, c.y() - d), QPointF(x + d, c.y() + d))
            p.drawLine(QPointF(x - d, c.y() + d), QPointF(x + d, c.y() - d))
    elif name == "mic":
        w, h = s * 0.26, s * 0.42
        p.setPen(_pen(color, stroke))
        p.drawRoundedRect(QRectF(c.x() - w / 2, c.y() - s * 0.4, w, h), w / 2, w / 2)
        arc = QRectF(c.x() - s * 0.24, c.y() - s * 0.2, s * 0.48, s * 0.42)
        p.drawArc(arc, 200 * 16, 140 * 16)
        p.drawLine(QPointF(c.x(), c.y() + s * 0.22), QPointF(c.x(), c.y() + s * 0.36))
        p.drawLine(QPointF(c.x() - s * 0.12, c.y() + s * 0.36), QPointF(c.x() + s * 0.12, c.y() + s * 0.36))
    elif name == "close":
        p.setPen(_pen(color, stroke))
        d = s * 0.26
        p.drawLine(QPointF(c.x() - d, c.y() - d), QPointF(c.x() + d, c.y() + d))
        p.drawLine(QPointF(c.x() - d, c.y() + d), QPointF(c.x() + d, c.y() - d))
    p.restore()


def render_pomo_icon(size: int) -> QPixmap:
    """The app icon: Pomo himself on a transparent background."""
    from .pomo import BODY_RY, Pomo

    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    # Pomo plus his blossom is about 0.86 of his width tall; fill the square and centre that span
    body = size * 0.94
    Pomo().paint(p, QPointF(size / 2, size / 2 + body * 0.03), body, ground_shadow=False)
    p.end()
    return pm


def render_tray_icon(size: int, accent: QColor, fraction: float, face: QColor, track: QColor) -> QPixmap:
    """Tray icon: the session's progress ring around a mini Pomo."""
    from .pomo import Pomo

    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    c = QPointF(size / 2, size / 2)
    r = size / 2 - 0.5
    p.setPen(Qt.NoPen)
    p.setBrush(face)
    p.drawEllipse(c, r, r)
    ring_w = size * 0.13
    rr = r - ring_w / 2 - size * 0.02
    rect = QRectF(c.x() - rr, c.y() - rr, 2 * rr, 2 * rr)
    p.setPen(QPen(track, ring_w, Qt.SolidLine, Qt.FlatCap))
    p.drawEllipse(rect)
    if fraction > 0:
        p.setPen(QPen(accent, ring_w, Qt.SolidLine, Qt.RoundCap if fraction < 1 else Qt.FlatCap))
        start = 90 - (1 - fraction) * 360
        p.drawArc(rect, int(start * 16), int(-fraction * 360 * 16))
    Pomo().paint(p, QPointF(c.x(), c.y() + size * 0.04), size * 0.5, ground_shadow=False)
    p.end()
    return pm


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(render_pomo_icon(size))
    return icon


def write_ico(path: Path) -> None:
    """Multi-resolution .ico with PNG-compressed entries (for the PyInstaller build)."""
    images = []
    for size in (16, 24, 32, 48, 64, 128, 256):
        data = QByteArray()
        buf = QBuffer(data)
        buf.open(QIODevice.WriteOnly)
        render_pomo_icon(size).save(buf, "PNG")
        buf.close()
        images.append((size, bytes(data)))
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, blob in images:
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
        blobs += blob
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + entries + blobs)
