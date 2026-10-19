"""Now playing: the mini player card (opened from the orb's music badge) and shared drawing helpers."""
from __future__ import annotations

import time

from PySide6.QtCore import QLineF, QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetricsF, QGuiApplication, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QToolTip, QWidget

from .icons import draw_glyph
from .pomo import Pomo
from .theme import FONT_FAMILY, mode_color

SHADOW = 14
WIDTH, HEIGHT = 340, 120
GAP = 10
POMO_SIZE = 44.0
TIPS = {"previous": "Previous", "next": "Next"}


def now_playing_tip(ctrl) -> str:
    track = ctrl.track
    if track is None:
        return ""
    lines = [track.title] + ([track.byline] if track.byline else [])
    if ctrl.pomo_listening():
        lines.append("Pomo is listening too ♪")
    return "\n".join(lines)


def paint_cover(p: QPainter, rect: QRectF, image: QImage | None, accent: QColor, theme) -> None:
    """The cover, center-cropped to a rounded square; a soft note tile when the app shares none."""
    radius = rect.width() * 0.24
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    p.save()
    if image is not None and not image.isNull():
        side = min(image.width(), image.height())  # video thumbnails are wide: keep the middle
        source = QRectF((image.width() - side) / 2, (image.height() - side) / 2, side, side)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setClipPath(path, Qt.IntersectClip)
        p.drawImage(rect, image, source)
    else:
        tint = QColor(accent)
        tint.setAlpha(80)
        p.setPen(Qt.NoPen)
        p.setBrush(tint)
        p.drawPath(path)
        draw_glyph(p, "note", rect.center(), rect.width() * 0.6, QColor(theme.ink_2))
    p.restore()
    p.save()
    p.setPen(QPen(QColor(theme.orb_edge), 1))
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)
    p.restore()


class MusicCard(QWidget):
    """Cover, song, play/pause and skip, and a little Pomo listening along. Closes on any click outside."""

    def __init__(self, ctrl):
        super().__init__(None, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.ctrl = ctrl
        self.pomo = Pomo()
        self._hot: str | None = None
        self._last = time.monotonic()
        self._last_move_x = 0.0
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setWindowTitle("Now playing")
        self.setFixedSize(WIDTH + 2 * SHADOW, HEIGHT + 2 * SHADOW)
        self._frames = QTimer(self)
        self._frames.setInterval(40)
        self._frames.timeout.connect(self._tick)
        self._tip_timer = QTimer(self)
        self._tip_timer.setSingleShot(True)
        self._tip_timer.setInterval(600)
        self._tip_timer.timeout.connect(self._show_tip)

    # ---------------------------------------------------------------- showing
    def show_near(self, anchor: QRect) -> None:
        screen = QGuiApplication.screenAt(anchor.center()) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        w, h = self.width(), self.height()
        if anchor.top() - geo.top() < 60:  # under the island
            x, y = anchor.center().x() - w // 2, anchor.bottom() + GAP - SHADOW
        else:  # beside the orb, level with its top; on its right if the left has no room
            x = anchor.left() - GAP - (w - SHADOW)
            if x + SHADOW < geo.left():
                x = anchor.right() + GAP - SHADOW
            y = anchor.top() - SHADOW
        self.move(max(geo.left() - SHADOW, min(x, geo.right() + 1 - w + SHADOW)),
                  max(geo.top() - SHADOW, min(y, geo.bottom() + 1 - h + SHADOW)))
        self.pomo.outfit = self.ctrl.settings.pomo_outfit
        self.show()
        self.raise_()

    def refresh(self) -> None:
        if self.ctrl.track is None:
            self.hide()
        else:
            self.update()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._last = time.monotonic()
        self._frames.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._frames.stop()
        QToolTip.hideText()

    def _tick(self) -> None:
        now = time.monotonic()
        dt, self._last = now - self._last, now
        listening = self.ctrl.pomo_listening()
        self.pomo.listening = listening
        self.pomo.outfit = self.ctrl.settings.pomo_outfit
        self.pomo.set_mood("break" if listening else "idle")  # eyes closed, enjoying it
        anchor = self._pomo_anchor()
        cursor = QPointF(self.mapFromGlobal(QCursor.pos())) - anchor
        self.pomo.update(dt, (cursor.x() / POMO_SIZE, cursor.y() / POMO_SIZE))
        self.update()

    # ---------------------------------------------------------------- layout
    def _card(self) -> QRectF:
        return QRectF(SHADOW, SHADOW, WIDTH, HEIGHT)

    def _cover(self) -> QRectF:
        c = self._card()
        return QRectF(c.left() + 14, c.top() + 14, 92, 92)

    def _text_left(self) -> float:
        return self._cover().right() + 14

    def _text_width(self) -> float:
        return self._card().right() - 72 - self._text_left()

    def _pomo_anchor(self) -> QPointF:
        c = self._card()
        return QPointF(c.right() - 38, c.bottom() - 36)

    def _buttons(self) -> dict[str, tuple[QPointF, float, bool]]:
        track = self.ctrl.track
        if track is None:
            return {}
        x, y = self._text_left(), self._card().top() + 89
        return {
            "previous": (QPointF(x + 13, y), 13.0, track.can_previous),
            "toggle": (QPointF(x + 51, y), 17.0, track.can_toggle),
            "next": (QPointF(x + 89, y), 13.0, track.can_next),
        }

    def _on_pomo(self, pos: QPointF) -> bool:
        body = self.pomo.body_center(self._pomo_anchor(), POMO_SIZE)
        return QLineF(pos, body).length() <= POMO_SIZE * 0.62

    # ---------------------------------------------------------------- paint
    def paintEvent(self, _event) -> None:
        t, track = self.ctrl.theme, self.ctrl.track
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        card = self._card()
        p.setPen(Qt.NoPen)
        for i in range(SHADOW, 0, -1):
            alpha = int(t.shadow_alpha * 0.22 * (1 - i / SHADOW) ** 2)
            p.setBrush(t.shadow(alpha))
            p.drawRoundedRect(card.adjusted(-i, -i + 4, i, i + 4), 14 + i, 14 + i)
        p.setBrush(QColor(t.surface))
        p.setPen(QPen(t.border, 1))
        p.drawRoundedRect(card.adjusted(0.5, 0.5, -0.5, -0.5), 14, 14)
        if track is None:
            p.end()
            return

        accent = mode_color(t, self.ctrl.settings, "focus")
        paint_cover(p, self._cover(), self.ctrl.track_art, accent, t)
        x, width = self._text_left(), self._text_width()
        title_font = self._font(14, QFont.DemiBold)
        p.setFont(title_font)
        p.setPen(QColor(t.ink))
        p.drawText(QRectF(x, card.top() + 14, width, 20), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetricsF(title_font).elidedText(track.title, Qt.ElideRight, width))
        small = self._font(12)
        p.setFont(small)
        p.setPen(QColor(t.muted))
        p.drawText(QRectF(x, card.top() + 35, width, 16), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetricsF(small).elidedText(track.byline, Qt.ElideRight, width))
        if self.ctrl.pomo_listening():
            note = self._font(11.5)
            p.setFont(note)
            p.setPen(QColor(t.ink_2))
            draw_glyph(p, "note", QPointF(x + 5, card.top() + 60), 11, QColor(accent).darker(115))
            p.drawText(QRectF(x + 14, card.top() + 52, width - 14, 16), Qt.AlignLeft | Qt.AlignVCenter,
                       "Pomo is listening too")
        elif not track.playing:
            p.setFont(self._font(11.5))
            p.setPen(QColor(t.muted))
            p.drawText(QRectF(x, card.top() + 52, width, 16), Qt.AlignLeft | Qt.AlignVCenter, "Paused")

        for name, (center, radius, enabled) in self._buttons().items():
            hot = name == self._hot
            ink = QColor(t.ink if hot else t.ink_2)
            if not enabled:
                ink = QColor(t.muted)
                ink.setAlpha(110)
            if name == "toggle":
                p.setPen(Qt.NoPen)
                p.setBrush(accent.lighter(108) if hot else accent)
                p.drawEllipse(center, radius, radius)
                nudge = QPointF(0 if track.playing else 1.5, 0)
                draw_glyph(p, "pause" if track.playing else "play", center + nudge, radius * 1.0, QColor(t.on_accent))
                continue
            if hot:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(t.hover))
                p.drawEllipse(center, radius, radius)
            draw_glyph(p, name, center, radius * 1.05, ink)

        if self.ctrl.settings.show_buddy:
            self.pomo.paint(p, self._pomo_anchor(), POMO_SIZE)
        p.end()

    def _font(self, px: float, weight=QFont.Normal) -> QFont:
        f = QFont(FONT_FAMILY)
        f.setPixelSize(round(px))
        f.setWeight(weight)
        return f

    # ---------------------------------------------------------------- input
    def _hit(self, pos: QPointF) -> str | None:
        for name, (center, radius, enabled) in self._buttons().items():
            if enabled and QLineF(pos, center).length() <= radius + 3:
                return name
        if self.ctrl.settings.show_buddy and self._on_pomo(pos):
            return "pomo"
        if self._cover().contains(pos):
            return "track"
        return None

    def _set_hot(self, name: str | None) -> None:
        if name == self._hot:
            return
        self._hot = name
        QToolTip.hideText()
        if name:
            self._tip_timer.start()
        else:
            self._tip_timer.stop()
        self.setCursor(Qt.PointingHandCursor if name in ("previous", "toggle", "next") else Qt.ArrowCursor)
        self.update()

    def _show_tip(self) -> None:
        name, track = self._hot, self.ctrl.track
        if not name or track is None:
            return
        if name == "toggle":
            text = "Pause" if track.playing else "Play"
        elif name == "track":
            text = now_playing_tip(self.ctrl)
        elif name == "pomo":
            text = "Pomo is listening too ♪" if self.ctrl.pomo_listening() else "Pomo"
        else:
            text = TIPS.get(name, "")
        QToolTip.showText(QCursor.pos(), text, self)

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        hot = self._hit(pos)
        if hot == "pomo":
            dx = pos.x() - self._last_move_x
            if abs(dx) > 2:
                self.pomo.rub(1 if dx > 0 else -1)
        self._last_move_x = pos.x()
        self._set_hot(hot)

    def leaveEvent(self, event) -> None:
        self._set_hot(None)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        pos = event.position()
        if not self.rect().contains(pos.toPoint()) or not self._card().contains(pos):
            self.hide()  # a click outside the card (Qt delivers it here while the popup is open)
            return
        if event.button() != Qt.LeftButton:
            return
        hit = self._hit(pos)
        if hit in ("previous", "toggle", "next"):
            self.ctrl.media_command(hit)
        elif hit == "pomo":
            self.pomo.poke()

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key_Escape:
            self.hide()
        elif key == Qt.Key_Space:
            self.ctrl.media_command("toggle")
        elif key == Qt.Key_Right:
            self.ctrl.media_command("next")
        elif key == Qt.Key_Left:
            self.ctrl.media_command("previous")
        else:
            super().keyPressEvent(event)
