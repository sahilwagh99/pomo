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
    elif name == "notes":  # a notebook: a page with lines, bound on the left
        w, h = s * 0.6, s * 0.74
        rect = QRectF(c.x() - w / 2 + s * 0.04, c.y() - h / 2, w, h)
        p.setPen(_pen(color, stroke))
        p.drawRoundedRect(rect, s * 0.08, s * 0.08)
        for dy in (-0.17, 0.0, 0.17):
            y = c.y() + dy * s
            p.drawLine(QPointF(rect.left() + w * 0.32, y), QPointF(rect.right() - w * 0.2, y))
        for dy in (-0.22, 0.0, 0.22):  # the rings of the binding
            p.drawLine(QPointF(rect.left() - s * 0.09, c.y() + dy * s), QPointF(rect.left() + s * 0.07, c.y() + dy * s))
    elif name == "sticky":  # a sticky note with its corner curling up
        w = s * 0.66
        x0, y0 = c.x() - w / 2, c.y() - w / 2
        fold = w * 0.32
        path = QPainterPath(QPointF(x0, y0))
        path.lineTo(x0 + w, y0)
        path.lineTo(x0 + w, y0 + w - fold)
        path.lineTo(x0 + w - fold, y0 + w)
        path.lineTo(x0, y0 + w)
        path.closeSubpath()
        p.setPen(_pen(color, stroke))
        p.drawPath(path)
        corner = QPainterPath(QPointF(x0 + w, y0 + w - fold))
        corner.lineTo(x0 + w - fold, y0 + w - fold)
        corner.lineTo(x0 + w - fold, y0 + w)
        p.drawPath(corner)
        p.drawLine(QPointF(x0 + w * 0.22, y0 + w * 0.3), QPointF(x0 + w * 0.72, y0 + w * 0.3))
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
    else:
        _draw_panel_glyph(p, name, c, s, color, stroke)
    p.restore()


def _draw_panel_glyph(p: QPainter, name: str, c: QPointF, s: float, color: QColor, stroke: float) -> None:
    """The panel's glyphs: its tabs, quick actions and system controls."""
    x, y = c.x(), c.y()
    p.setPen(_pen(color, stroke))
    if name == "home":
        roof = QPainterPath(QPointF(x - s * 0.36, y - s * 0.02))
        roof.lineTo(x, y - s * 0.36)
        roof.lineTo(x + s * 0.36, y - s * 0.02)
        p.drawPath(roof)
        p.drawRoundedRect(QRectF(x - s * 0.26, y - s * 0.1, s * 0.52, s * 0.44), s * 0.05, s * 0.05)
        p.drawLine(QPointF(x, y + s * 0.34), QPointF(x, y + s * 0.14))
    elif name == "clock":
        r = s * 0.36
        p.drawEllipse(c, r, r)
        p.drawLine(c, QPointF(x, y - r * 0.6))
        p.drawLine(c, QPointF(x + r * 0.45, y + r * 0.2))
        p.drawLine(QPointF(x - s * 0.08, y - r - s * 0.08), QPointF(x + s * 0.08, y - r - s * 0.08))
    elif name == "agent":  # a terminal: the coding agents
        p.drawRoundedRect(QRectF(x - s * 0.38, y - s * 0.3, s * 0.76, s * 0.6), s * 0.09, s * 0.09)
        p.drawPolyline([QPointF(x - s * 0.22, y - s * 0.1), QPointF(x - s * 0.08, y + s * 0.02),
                        QPointF(x - s * 0.22, y + s * 0.14)])
        p.drawLine(QPointF(x + s * 0.0, y + s * 0.14), QPointF(x + s * 0.2, y + s * 0.14))
    elif name == "habit":  # a ticked circle
        r = s * 0.36
        p.drawEllipse(c, r, r)
        p.drawPolyline([QPointF(x - s * 0.16, y), QPointF(x - s * 0.04, y + s * 0.13), QPointF(x + s * 0.18, y - s * 0.13)])
    elif name == "clipboard":
        p.drawRoundedRect(QRectF(x - s * 0.28, y - s * 0.3, s * 0.56, s * 0.66), s * 0.07, s * 0.07)
        p.drawRoundedRect(QRectF(x - s * 0.13, y - s * 0.38, s * 0.26, s * 0.14), s * 0.04, s * 0.04)
        for dy in (0.02, 0.17):
            p.drawLine(QPointF(x - s * 0.14, y + dy * s), QPointF(x + s * 0.14, y + dy * s))
    elif name == "shelf":  # a tray with something in it
        tray = QPainterPath(QPointF(x - s * 0.38, y - s * 0.02))
        tray.lineTo(x - s * 0.38, y + s * 0.3)
        tray.lineTo(x + s * 0.38, y + s * 0.3)
        tray.lineTo(x + s * 0.38, y - s * 0.02)
        p.drawPath(tray)
        p.drawLine(QPointF(x - s * 0.38, y - s * 0.02), QPointF(x - s * 0.14, y - s * 0.02))
        p.drawLine(QPointF(x + s * 0.14, y - s * 0.02), QPointF(x + s * 0.38, y - s * 0.02))
        p.drawLine(QPointF(x - s * 0.14, y - s * 0.02), QPointF(x - s * 0.1, y + s * 0.1))
        p.drawLine(QPointF(x + s * 0.14, y - s * 0.02), QPointF(x + s * 0.1, y + s * 0.1))
        p.drawLine(QPointF(x - s * 0.1, y + s * 0.1), QPointF(x + s * 0.1, y + s * 0.1))
        p.drawLine(QPointF(x, y - s * 0.36), QPointF(x, y + s * 0.0))
        p.drawPolyline([QPointF(x - s * 0.1, y - s * 0.1), QPointF(x, y), QPointF(x + s * 0.1, y - s * 0.1)])
    elif name == "grid":  # the launcher
        for dx in (-0.17, 0.17):
            for dy in (-0.17, 0.17):
                p.drawRoundedRect(QRectF(x + dx * s - s * 0.12, y + dy * s - s * 0.12, s * 0.24, s * 0.24),
                                  s * 0.06, s * 0.06)
    elif name == "doc":
        page = QPainterPath(QPointF(x - s * 0.26, y - s * 0.36))
        page.lineTo(x + s * 0.1, y - s * 0.36)
        page.lineTo(x + s * 0.26, y - s * 0.2)
        page.lineTo(x + s * 0.26, y + s * 0.36)
        page.lineTo(x - s * 0.26, y + s * 0.36)
        page.closeSubpath()
        p.drawPath(page)
        p.drawPolyline([QPointF(x + s * 0.1, y - s * 0.36), QPointF(x + s * 0.1, y - s * 0.2),
                        QPointF(x + s * 0.26, y - s * 0.2)])
    elif name == "calendar":
        box = QRectF(x - s * 0.34, y - s * 0.28, s * 0.68, s * 0.62)
        p.drawRoundedRect(box, s * 0.08, s * 0.08)
        p.drawLine(QPointF(box.left(), y - s * 0.1), QPointF(box.right(), y - s * 0.1))
        for dx in (-0.16, 0.16):
            p.drawLine(QPointF(x + dx * s, y - s * 0.38), QPointF(x + dx * s, y - s * 0.2))
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        for dx in (-0.16, 0.0, 0.16):
            p.drawEllipse(QPointF(x + dx * s, y + s * 0.1), s * 0.04, s * 0.04)
    elif name == "sliders":  # system controls
        for dy, knob in ((-0.2, 0.12), (0.0, -0.14), (0.2, 0.05)):
            p.drawLine(QPointF(x - s * 0.36, y + dy * s), QPointF(x + s * 0.36, y + dy * s))
            p.setBrush(color)
            p.drawEllipse(QPointF(x + knob * s, y + dy * s), s * 0.06, s * 0.06)
    elif name == "gamepad":
        pad = QPainterPath()
        pad.addRoundedRect(QRectF(x - s * 0.4, y - s * 0.2, s * 0.8, s * 0.42), s * 0.2, s * 0.2)
        p.drawPath(pad)
        p.drawLine(QPointF(x - s * 0.26, y), QPointF(x - s * 0.1, y))
        p.drawLine(QPointF(x - s * 0.18, y - s * 0.08), QPointF(x - s * 0.18, y + s * 0.08))
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(QPointF(x + s * 0.14, y - s * 0.04), s * 0.045, s * 0.045)
        p.drawEllipse(QPointF(x + s * 0.24, y + s * 0.06), s * 0.045, s * 0.045)
    elif name == "dots":
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        for dx in (-0.24, 0.0, 0.24):
            p.drawEllipse(QPointF(x + dx * s, y), s * 0.06, s * 0.06)
    elif name == "pin":
        p.drawEllipse(QPointF(x, y - s * 0.14), s * 0.16, s * 0.16)
        p.drawLine(QPointF(x, y + s * 0.02), QPointF(x, y + s * 0.38))
        p.drawLine(QPointF(x - s * 0.24, y + s * 0.02), QPointF(x + s * 0.24, y + s * 0.02))
    elif name == "pin_on":
        p.setBrush(color)
        p.drawEllipse(QPointF(x, y - s * 0.14), s * 0.16, s * 0.16)
        p.drawLine(QPointF(x, y + s * 0.02), QPointF(x, y + s * 0.38))
        p.drawLine(QPointF(x - s * 0.24, y + s * 0.02), QPointF(x + s * 0.24, y + s * 0.02))
    elif name == "popout":
        box = QPainterPath(QPointF(x + s * 0.02, y - s * 0.3))
        box.lineTo(x - s * 0.3, y - s * 0.3)
        box.lineTo(x - s * 0.3, y + s * 0.3)
        box.lineTo(x + s * 0.3, y + s * 0.3)
        box.lineTo(x + s * 0.3, y - s * 0.02)
        p.drawPath(box)
        p.drawLine(QPointF(x - s * 0.02, y + s * 0.02), QPointF(x + s * 0.34, y - s * 0.34))
        p.drawPolyline([QPointF(x + s * 0.12, y - s * 0.34), QPointF(x + s * 0.34, y - s * 0.34),
                        QPointF(x + s * 0.34, y - s * 0.12)])
    elif name == "lock":
        p.drawRoundedRect(QRectF(x - s * 0.26, y - s * 0.04, s * 0.52, s * 0.4), s * 0.07, s * 0.07)
        p.drawArc(QRectF(x - s * 0.16, y - s * 0.36, s * 0.32, s * 0.6), 0, 180 * 16)
    elif name == "crop":  # take a screenshot
        p.drawPolyline([QPointF(x - s * 0.24, y - s * 0.38), QPointF(x - s * 0.24, y + s * 0.24),
                        QPointF(x + s * 0.38, y + s * 0.24)])
        p.drawPolyline([QPointF(x - s * 0.38, y - s * 0.24), QPointF(x + s * 0.24, y - s * 0.24),
                        QPointF(x + s * 0.24, y + s * 0.38)])
    elif name == "bluetooth":
        p.drawPolyline([QPointF(x - s * 0.18, y - s * 0.16), QPointF(x + s * 0.18, y + s * 0.16),
                        QPointF(x, y + s * 0.34), QPointF(x, y - s * 0.34), QPointF(x + s * 0.18, y - s * 0.16),
                        QPointF(x - s * 0.18, y + s * 0.16)])
    elif name == "sun":  # brightness
        p.drawEllipse(c, s * 0.15, s * 0.15)
        for k in range(8):
            a = math.radians(k * 45)
            p.drawLine(QPointF(x + math.cos(a) * s * 0.26, y + math.sin(a) * s * 0.26),
                       QPointF(x + math.cos(a) * s * 0.38, y + math.sin(a) * s * 0.38))
    elif name == "refresh":
        r = s * 0.3
        p.drawArc(QRectF(x - r, y - r, 2 * r, 2 * r), 40 * 16, 280 * 16)
        tip = QPointF(x + r * math.cos(math.radians(40)), y - r * math.sin(math.radians(40)))
        p.drawPolyline([QPointF(tip.x() - s * 0.16, tip.y() - s * 0.02), tip, QPointF(tip.x() + s * 0.02, tip.y() + s * 0.16)])
    elif name == "plus":
        p.drawLine(QPointF(x - s * 0.3, y), QPointF(x + s * 0.3, y))
        p.drawLine(QPointF(x, y - s * 0.3), QPointF(x, y + s * 0.3))
    elif name == "search":
        p.drawEllipse(QPointF(x - s * 0.06, y - s * 0.06), s * 0.24, s * 0.24)
        p.drawLine(QPointF(x + s * 0.12, y + s * 0.12), QPointF(x + s * 0.34, y + s * 0.34))
    elif name == "folder":
        path = QPainterPath(QPointF(x - s * 0.38, y + s * 0.3))
        for dx, dy in ((-0.38, -0.28), (-0.1, -0.28), (0.0, -0.18), (0.38, -0.18), (0.38, 0.3)):
            path.lineTo(x + dx * s, y + dy * s)
        path.closeSubpath()
        p.drawPath(path)
    elif name == "trash":
        p.drawLine(QPointF(x - s * 0.32, y - s * 0.24), QPointF(x + s * 0.32, y - s * 0.24))
        p.drawLine(QPointF(x - s * 0.1, y - s * 0.34), QPointF(x + s * 0.1, y - s * 0.34))
        can = QPainterPath(QPointF(x - s * 0.24, y - s * 0.24))
        can.lineTo(x - s * 0.19, y + s * 0.36)
        can.lineTo(x + s * 0.19, y + s * 0.36)
        can.lineTo(x + s * 0.24, y - s * 0.24)
        p.drawPath(can)
    elif name == "monitor":
        p.drawRoundedRect(QRectF(x - s * 0.38, y - s * 0.3, s * 0.76, s * 0.48), s * 0.06, s * 0.06)
        p.drawLine(QPointF(x, y + s * 0.18), QPointF(x, y + s * 0.32))
        p.drawLine(QPointF(x - s * 0.16, y + s * 0.34), QPointF(x + s * 0.16, y + s * 0.34))
    elif name == "chip":  # a processor
        p.drawRoundedRect(QRectF(x - s * 0.22, y - s * 0.22, s * 0.44, s * 0.44), s * 0.05, s * 0.05)
        for k in (-0.1, 0.1):
            for a, b, cc, d in ((k, -0.22, k, -0.36), (k, 0.22, k, 0.36), (-0.22, k, -0.36, k), (0.22, k, 0.36, k)):
                p.drawLine(QPointF(x + a * s, y + b * s), QPointF(x + cc * s, y + d * s))
    elif name == "thermo":
        p.drawRoundedRect(QRectF(x - s * 0.08, y - s * 0.38, s * 0.16, s * 0.5), s * 0.08, s * 0.08)
        p.setBrush(color)
        p.drawEllipse(QPointF(x, y + s * 0.22), s * 0.14, s * 0.14)
    elif name in ("cloud", "cloud_sun", "rain", "snow", "storm", "fog"):
        if name == "cloud_sun":
            p.drawEllipse(QPointF(x + s * 0.12, y - s * 0.14), s * 0.14, s * 0.14)
            for k in range(5):
                a = math.radians(-100 + k * 45)
                p.drawLine(QPointF(x + s * 0.12 + math.cos(a) * s * 0.21, y - s * 0.14 + math.sin(a) * s * 0.21),
                           QPointF(x + s * 0.12 + math.cos(a) * s * 0.29, y - s * 0.14 + math.sin(a) * s * 0.29))
        cy = y - s * 0.02 if name in ("rain", "snow", "storm") else y + s * 0.06
        cloud = QPainterPath()
        cloud.addRoundedRect(QRectF(x - s * 0.36, cy - s * 0.02, s * 0.72, s * 0.22), s * 0.11, s * 0.11)
        cloud.addEllipse(QPointF(x - s * 0.12, cy - s * 0.04), s * 0.15, s * 0.15)
        cloud.addEllipse(QPointF(x + s * 0.1, cy - s * 0.08), s * 0.19, s * 0.19)
        p.setBrush(QColor(0, 0, 0, 0))
        p.drawPath(cloud.simplified())
        if name == "rain":
            for dx in (-0.18, 0.0, 0.18):
                p.drawLine(QPointF(x + dx * s, y + s * 0.26), QPointF(x + dx * s - s * 0.05, y + s * 0.38))
        elif name == "snow":
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            for dx in (-0.18, 0.0, 0.18):
                p.drawEllipse(QPointF(x + dx * s, y + s * 0.32), s * 0.045, s * 0.045)
        elif name == "storm":
            p.drawPolyline([QPointF(x + s * 0.04, y + s * 0.2), QPointF(x - s * 0.06, y + s * 0.32),
                            QPointF(x + s * 0.04, y + s * 0.32), QPointF(x - s * 0.06, y + s * 0.44)])
        elif name == "fog":
            for dy in (0.3, 0.42):
                p.drawLine(QPointF(x - s * 0.3, y + dy * s), QPointF(x + s * 0.3, y + dy * s))
    elif name == "moon":
        moon = QPainterPath()
        moon.addEllipse(QPointF(x, y), s * 0.32, s * 0.32)
        bite = QPainterPath()
        bite.addEllipse(QPointF(x + s * 0.16, y - s * 0.12), s * 0.28, s * 0.28)
        p.drawPath(moon.subtracted(bite))
    elif name == "mic_off":
        w, h = s * 0.26, s * 0.42
        p.drawRoundedRect(QRectF(x - w / 2, y - s * 0.4, w, h), w / 2, w / 2)
        p.drawArc(QRectF(x - s * 0.24, y - s * 0.2, s * 0.48, s * 0.42), 200 * 16, 140 * 16)
        p.drawLine(QPointF(x, y + s * 0.22), QPointF(x, y + s * 0.36))
        p.drawLine(QPointF(x - s * 0.36, y - s * 0.36), QPointF(x + s * 0.36, y + s * 0.36))


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
