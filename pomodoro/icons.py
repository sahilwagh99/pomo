"""Vector glyphs and the app/tray icon, painted with QPainter (no image assets)."""
from __future__ import annotations

import math
import struct
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap


def _pen(color: QColor, width: float) -> QPen:
    return QPen(color, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)


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
    elif name == "skip":
        path = QPainterPath(QPointF(c.x() - s * 0.28, c.y() - s * 0.28))
        path.lineTo(c.x() + s * 0.16, c.y())
        path.lineTo(c.x() - s * 0.28, c.y() + s * 0.28)
        path.closeSubpath()
        p.setPen(_pen(color, s * 0.1))
        p.setBrush(color)
        p.drawPath(path)
        p.setPen(_pen(color, stroke * 1.1))
        p.drawLine(QPointF(c.x() + s * 0.3, c.y() - s * 0.28), QPointF(c.x() + s * 0.3, c.y() + s * 0.28))
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
    body = size * 0.86
    Pomo().paint(p, QPointF(size / 2, size - body * BODY_RY - size * 0.07), body, ground_shadow=False)
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
