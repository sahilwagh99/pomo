"""Top-of-screen island: the timer as a soft pill hanging from the top edge.

Three sizes, blended by two animated values:
  presence  0 = a thin sliver (no timer running), 1 = a compact pill (time + mini Pomo)
  expand    0 = as above, 1 = the full panel with Pomo, controls and progress
Hovering springs it open; it tucks away again shortly after the pointer leaves.
The window itself never moves or resizes (that flickers); only the painted pill
changes, and Windows passes clicks through its fully transparent pixels.
"""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QEasingCurve, QLineF, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetricsF, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QToolTip, QWidget

from .engine import State
from .icons import draw_glyph
from .orb import TimeEdit, apply_typed_time
from .pomo import Pomo, mood_for
from .stats import fmt_clock
from .theme import FONT_FAMILY, mode_color


SLIVER = (110.0, 8.0, 4.0)       # width, height, bottom corner radius
COMPACT = (212.0, 38.0, 19.0)
EXPANDED = (392.0, 136.0, 28.0)
FLARE = 10.0                     # concave shoulders where the pill meets the screen edge
PAD = 30                         # room for spring overshoot and the shadow
FRAME_MS = 33                    # while Pomo moves
CALM_FRAME_MS = 125              # while he only breathes
OPEN_DELAY_MS = 120
CLOSE_DELAY_MS = 450
PIN_AFTER_SESSION = 5.0

TIPS = {
    "reset": "Reset  (R)",
    "dashboard": "Dashboard",
    "chat": "Ask Pomo",
    "style": "Switch to the floating orb",
    "feed": "Feed Pomo a treat (you earn one per focus session)",
    "play": "Play Petal Catch with Pomo",
    "settings": "Settings",
    "pomo": "Pomo, your focus buddy. Poke him!",
    "time": "Double-click to type a time, scroll to adjust",
}


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


class IslandWidget(QWidget):
    dashboard_requested = Signal()
    settings_requested = Signal()
    chat_requested = Signal()
    menu_requested = Signal(QPoint)
    moved = Signal()  # unused; keeps the same interface as the orb
    interacted = Signal()

    def __init__(self, ctrl):
        super().__init__(None)
        self.ctrl = ctrl
        self.engine = ctrl.engine
        self.pomo = Pomo()
        self._pomo_on = True
        self._presence = 1.0
        self._expand = 0.0
        self._hover = False
        self._menu_open = False
        self._editing = False
        self._pinned_until = 0.0
        self._hot: str | None = None
        self._pressed: str | None = None
        self._wheel_acc = 0
        self._last_frame = time.monotonic()
        self._last_second = -1
        self._last_move_x = 0.0

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.setWindowTitle("Pomo island")
        ew, eh, _ = EXPANDED
        self.setFixedSize(int(ew * 1.08 + 2 * (FLARE + PAD)), int(eh * 1.1 + PAD))

        self._presence_anim = self._animation(lambda v: self._set("_presence", float(v)))
        self._expand_anim = self._animation(lambda v: self._set("_expand", float(v)))
        self._open_timer = self._single_shot(OPEN_DELAY_MS, lambda: self._set_hover(True))
        self._close_timer = self._single_shot(CLOSE_DELAY_MS, lambda: self._set_hover(False))
        self._tip_timer = self._single_shot(600, self._show_tip)
        self._frames = QTimer(self)
        self._frames.setInterval(FRAME_MS)
        self._frames.timeout.connect(self._animate)

        self._edit = TimeEdit(self)
        self._edit.committed.connect(self._commit_edit)
        self._edit.cancelled.connect(self._end_edit)

        self.engine.ticked.connect(self._on_tick)
        self.engine.changed.connect(self._update_targets)
        self.apply_settings()

    def _on_tick(self) -> None:
        second = math.ceil(self.engine.remaining)
        if second != self._last_second:
            self._last_second = second
            self.update()

    # ------------------------------------------------------------------ setup
    def _animation(self, setter) -> QVariantAnimation:
        anim = QVariantAnimation(self)
        anim.valueChanged.connect(setter)
        return anim

    def _single_shot(self, ms: int, fn) -> QTimer:
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(ms)
        timer.timeout.connect(fn)
        return timer

    def _set(self, attr: str, value) -> None:
        setattr(self, attr, value)
        self.update()

    @staticmethod
    def _spring_to(anim: QVariantAnimation, start: float, end: float) -> None:
        anim.stop()
        anim.setStartValue(start)
        anim.setEndValue(end)
        if end > start:
            curve = QEasingCurve(QEasingCurve.OutBack)
            curve.setOvershoot(1.1)
            anim.setEasingCurve(curve)
            anim.setDuration(420)
        else:
            anim.setEasingCurve(QEasingCurve.InOutCubic)
            anim.setDuration(260)
        anim.start()

    def apply_settings(self) -> None:
        self._pomo_on = bool(self.ctrl.settings.show_buddy)
        self._update_targets()
        self.update()

    def place(self) -> None:
        geo = QGuiApplication.primaryScreen().availableGeometry()
        self.move(geo.center().x() - self.width() // 2 + 1, geo.top())

    def ensure_on_screen(self) -> None:
        self.place()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._last_frame = time.monotonic()
        self._frames.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._frames.stop()

    # ------------------------------------------------------------------ state
    def _update_targets(self) -> None:
        s, e = self.ctrl.settings, self.engine
        hub = getattr(self.ctrl, "agents", None)
        agents_active = hub is not None and hub.signal() is not None
        tucked = s.island_autohide and e.state is State.IDLE and self.pomo.reaction is None and not agents_active
        presence = 0.0 if tucked else 1.0
        approval_waiting = hub is not None and bool(hub.pending)
        expand = 1.0 if (self._hover or self._menu_open or self._editing or approval_waiting
                         or time.monotonic() < self._pinned_until) else 0.0
        for anim, current, target in ((self._presence_anim, self._presence, presence),
                                      (self._expand_anim, self._expand, expand)):
            running_to = anim.endValue() if anim.state() == QVariantAnimation.Running else None
            if running_to != target and abs(current - target) > 1e-3:
                self._spring_to(anim, current, target)
        self.update()

    def _set_hover(self, hover: bool) -> None:
        if hover != self._hover:
            self._hover = hover
            if not hover:
                self._set_hot(None)
            self._update_targets()

    def pin(self, seconds: float) -> None:
        self._pinned_until = max(self._pinned_until, time.monotonic() + seconds)
        self._update_targets()

    def flash(self, loops: int = 3) -> None:
        self.pin(1.2 * loops)
        if self._pomo_on:
            self.pomo.hop(1.6)

    def celebrate(self, big: bool = False) -> None:
        self.pin(PIN_AFTER_SESSION)
        if self._pomo_on:
            self.pomo.celebrate(big)

    def hello(self) -> None:
        self.pin(2.5)
        if self._pomo_on:
            self.pomo.hello()

    def _animate(self) -> None:
        now = time.monotonic()
        dt, self._last_frame = now - self._last_frame, now
        if self._pinned_until and now >= self._pinned_until:
            self._pinned_until = 0.0
            self._update_targets()
        resizing = (self._presence_anim.state() == QVariantAnimation.Running
                    or self._expand_anim.state() == QVariantAnimation.Running)
        calm = True
        if self._pomo_on:
            e = self.engine
            self.pomo.set_mood(mood_for(e.state, e.mode.is_break, self.ctrl.idle_seconds()))
            self.pomo.signal = self.ctrl.agent_signal()
            self.pomo.outfit = self.ctrl.settings.pomo_outfit
            anchor, size = self._pomo_place()
            cursor = QPointF(self.mapFromGlobal(QCursor.pos())) - anchor
            self.pomo.update(dt, (cursor.x() / size, cursor.y() / size))
            calm = self.pomo.is_calm()
            if not resizing and (self._presence > 0.3 or self._expand > 0.3):
                if self.pomo.particles:
                    self.update()
                else:
                    # body, leaf and the highest hop all fit in this box
                    self.update(QRectF(anchor.x() - size, anchor.y() - 1.4 * size, 2 * size, 2.3 * size).toAlignedRect())
        if self.engine.state is State.FINISHED and not resizing:
            self.update()  # the compact ring pulses
            calm = False
        self._frames.setInterval(CALM_FRAME_MS if calm else FRAME_MS)

    # --------------------------------------------------------------- geometry
    def _pill(self) -> tuple[QRectF, float]:
        p, e = self._presence, self._expand
        bw, bh, br = (_lerp(a, b, p) for a, b in zip(SLIVER, COMPACT))
        ew, eh, er = EXPANDED
        w, h, r = _lerp(bw, ew, e), _lerp(bh, eh, e), _lerp(br, er, e)
        return QRectF((self.width() - w) / 2, 0.0, w, h), r

    def _expanded_rect(self) -> QRectF:
        ew, eh, _ = EXPANDED
        return QRectF((self.width() - ew) / 2, 0.0, ew, eh)

    def anchor_rect_global(self) -> QRect:
        r = self._expanded_rect().toRect()
        return QRect(self.mapToGlobal(r.topLeft()), r.size())

    @staticmethod
    def _path(rect: QRectF, radius: float) -> QPainterPath:
        f = min(FLARE, rect.height() * 0.6)
        r = max(0.0, min(radius, rect.height() - f, rect.width() / 2))
        x0, x1, top, bottom = rect.left(), rect.right(), rect.top(), rect.bottom()
        path = QPainterPath(QPointF(x0 - f, top))
        path.quadTo(x0, top, x0, top + f)
        path.lineTo(x0, bottom - r)
        path.arcTo(QRectF(x0, bottom - 2 * r, 2 * r, 2 * r), 180, 90)
        path.lineTo(x1 - r, bottom)
        path.arcTo(QRectF(x1 - 2 * r, bottom - 2 * r, 2 * r, 2 * r), 270, 90)
        path.lineTo(x1, top + f)
        path.quadTo(x1, top, x1 + f, top)
        path.closeSubpath()
        return path

    def _pomo_place(self) -> tuple[QPointF, float]:
        """Pomo's anchor and size, blended between the compact and expanded layouts."""
        rect, _ = self._pill()
        small = (QPointF(rect.left() + 24, rect.top() + rect.height() / 2 + 2), 22.0)
        big = (QPointF(rect.left() + 62, rect.top() + 72), 58.0)
        t = _clamp01((self._expand - 0.15) / 0.85)
        anchor = QPointF(_lerp(small[0].x(), big[0].x(), t), _lerp(small[0].y(), big[0].y(), t))
        return anchor, _lerp(small[1], big[1], t)

    def _on_pomo(self, pos: QPointF) -> bool:
        if not self._pomo_on or self._presence < 0.5 and self._expand < 0.5:
            return False
        anchor, size = self._pomo_place()
        body = self.pomo.body_center(anchor, size)
        return QLineF(pos, body).length() <= size * 0.62

    def _buttons(self) -> dict[str, tuple[QPointF, float]]:
        r = self._expanded_rect()
        return {
            "style": (QPointF(r.right() - 122, r.top() + 28), 12.0),
            "chat": (QPointF(r.right() - 92, r.top() + 28), 12.0),
            "dashboard": (QPointF(r.right() - 62, r.top() + 28), 12.0),
            "settings": (QPointF(r.right() - 32, r.top() + 28), 12.0),
            "reset": (QPointF(r.right() - 120, r.top() + 80), 15.0),
            "toggle": (QPointF(r.right() - 76, r.top() + 80), 21.0),
            "skip": (QPointF(r.right() - 32, r.top() + 80), 15.0),
        }

    def _badges(self) -> dict[str, tuple[QPointF, float]]:
        """Toys beside the big Pomo in the open island: treat (feed) and Petal Catch (play)."""
        if not self._pomo_on or self._expand < 0.8:
            return {}
        r = self._expanded_rect()
        out = {"play": (QPointF(r.left() + 104, r.top() + 114), 11.0)}
        if self.ctrl.settings.treats > 0:
            out["feed"] = (QPointF(r.left() + 20, r.top() + 114), 11.0)
        return out

    def _button_at(self, pos: QPointF) -> str | None:
        if self._expand < 0.8 or self._editing:
            return None
        for name, (center, radius) in {**self._badges(), **self._buttons()}.items():
            if QLineF(pos, center).length() <= radius + 3:
                return name
        return None

    def _time_rect(self) -> QRectF:
        r = self._expanded_rect()
        return QRectF(r.left() + 120, r.top() + 42, 138, 42)

    def _inside(self, pos: QPointF) -> bool:
        rect, radius = self._pill()
        return self._path(rect.adjusted(-2, 0, 2, 4), radius).contains(pos)

    # --------------------------------------------------------------- painting
    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        t = self.ctrl.theme
        rect, radius = self._pill()
        path = self._path(rect, radius)

        # soft shadow below the pill
        p.setPen(Qt.NoPen)
        for i in range(10, 0, -2):
            p.setBrush(t.shadow(10 if not t.dark else 22))
            p.drawPath(self._path(rect.adjusted(-i * 0.6, 0, i * 0.6, i * 0.9 + 2), radius + i * 0.5))
        p.setBrush(QColor(t.orb_face))
        p.drawPath(path)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(t.orb_edge), 1.2))
        p.drawPath(path)

        p.save()
        p.setClipPath(path)
        e = self._expand
        accent = mode_color(self.ctrl.theme, self.ctrl.settings, self.engine.mode.value)
        sliver_alpha = (1 - self._presence) * (1 - e)
        compact_alpha = self._presence * _clamp01(1 - e * 2.2)
        full_alpha = _clamp01((e - 0.45) / 0.55)
        if sliver_alpha > 0.01:
            p.setOpacity(sliver_alpha)
            p.setPen(Qt.NoPen)
            p.setBrush(accent)
            p.drawRoundedRect(QRectF(rect.center().x() - 17, rect.bottom() - 4.5, 34, 2.5), 1.25, 1.25)
        if compact_alpha > 0.01:
            p.setOpacity(compact_alpha)
            self._paint_compact(p, rect, accent)
        if full_alpha > 0.01:
            p.setOpacity(full_alpha)
            self._paint_expanded(p, accent)
        if self._pomo_on and (self._presence > 0.3 or e > 0.3):
            p.setOpacity(_clamp01(max(self._presence, e) * 1.6 - 0.5))
            anchor, size = self._pomo_place()
            self.pomo.paint(p, anchor, size, ground_shadow=e > 0.6)
        p.restore()
        p.end()

    def _font(self, px: float, weight=QFont.Normal, spacing: float = 0.0) -> QFont:
        f = QFont(FONT_FAMILY)
        f.setPixelSize(round(px))
        f.setWeight(weight)
        if spacing:
            f.setLetterSpacing(QFont.AbsoluteSpacing, spacing)
        return f

    def _ring(self, p: QPainter, center: QPointF, r: float, width: float, accent: QColor) -> None:
        track = QColor(accent)
        track.setAlpha(70)
        rect = QRectF(center.x() - r, center.y() - r, 2 * r, 2 * r)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(track, width))
        p.drawEllipse(rect)
        frac = self.engine.fraction_left
        if frac > 0.0005:
            p.setPen(QPen(accent, width, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(rect, round((90 - (1 - frac) * 360) * 16), round(-frac * 360 * 16))

    def _paint_compact(self, p: QPainter, rect: QRectF, accent: QColor) -> None:
        e, t = self.engine, self.ctrl.theme
        p.setFont(self._font(15, QFont.DemiBold))
        p.setPen(QColor(t.muted if e.state is State.PAUSED else t.ink))
        p.drawText(QRectF(rect.left() + 44, rect.top(), rect.width() - 44 - 36, rect.height()),
                   Qt.AlignRight | Qt.AlignVCenter, fmt_clock(e.remaining))
        if e.state is State.FINISHED:
            accent = QColor(accent)
            accent.setAlphaF(0.55 + 0.45 * abs(math.sin(time.monotonic() * 3.0)))
        self._ring(p, QPointF(rect.right() - 20, rect.center().y()), 7.5, 2.6, accent)
        signal = self.ctrl.agent_signal()
        if signal:
            # a small dot between Pomo and the time: sakura when an agent needs you, matcha while one works
            dot = QColor(t.short if signal == "alert" else t.focus)
            p.setPen(Qt.NoPen)
            p.setBrush(dot)
            p.drawEllipse(QPointF(rect.left() + 50, rect.center().y()), 3.5, 3.5)

    def _agent_line(self) -> tuple[str, str] | None:
        hub = getattr(self.ctrl, "agents", None)
        if hub is None or not hub.sessions:
            return None
        return hub.headline(), hub.signal() or ""

    def _paint_expanded(self, p: QPainter, accent: QColor) -> None:
        e, s, t = self.engine, self.ctrl.settings, self.ctrl.theme
        r = self._expanded_rect()
        x0 = r.left() + 122

        label = "PAUSED" if e.state is State.PAUSED else e.mode.label.upper()
        if e.state is State.FINISHED:
            label = f"UP NEXT · {label}"
        p.setPen(Qt.NoPen)
        p.setBrush(accent)
        p.drawEllipse(QPointF(x0 + 3, r.top() + 30), 3.2, 3.2)
        p.setFont(self._font(10.5, QFont.DemiBold, 1.2))
        p.setPen(QColor(t.ink_2))
        p.drawText(QRectF(x0 + 12, r.top() + 22, 116, 16), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetricsF(p.font()).elidedText(label, Qt.ElideRight, 116))

        if not self._editing:
            text = fmt_clock(e.remaining)
            p.setFont(self._font(34 if len(text) <= 5 else 27, QFont.DemiBold))
            p.setPen(QColor(t.muted if e.state is State.PAUSED else t.ink))
            p.drawText(self._time_rect().adjusted(-2, 0, 0, 0), Qt.AlignLeft | Qt.AlignVCenter, text)

        info = f"{self.ctrl.today_count}/{s.daily_goal} today"
        if s.show_task and e.task:
            info += f"  ·  {e.task}"
        font = self._font(12)
        p.setFont(font)
        p.setPen(QColor(t.muted))
        p.drawText(QRectF(x0, r.top() + 84, 136, 16), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetricsF(font).elidedText(info, Qt.ElideRight, 136))
        agent = self._agent_line()
        if agent:
            text, signal = agent
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(t.short if signal == "alert" else t.focus if signal == "busy" else t.muted))
            p.drawEllipse(QPointF(x0 + 3, r.top() + 108), 3, 3)
            p.setPen(QColor(t.ink_2))
            p.drawText(QRectF(x0 + 11, r.top() + 100, 126, 16), Qt.AlignLeft | Qt.AlignVCenter,
                       QFontMetricsF(font).elidedText(text, Qt.ElideRight, 126))

        bar = QRectF(x0, r.bottom() - 17, r.right() - 22 - x0, 4)
        track = QColor(accent)
        track.setAlpha(60)
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(bar, 2, 2)
        frac = e.fraction_left
        if frac > 0:
            p.setBrush(accent)
            p.drawRoundedRect(QRectF(bar.left(), bar.top(), max(4.0, bar.width() * frac), bar.height()), 2, 2)

        glyphs = {"reset": "reset", "skip": "skip", "dashboard": "chart", "chat": "chat", "settings": "gear",
                  "style": "orb"}
        for name, (center, radius) in self._buttons().items():
            hot = name == self._hot
            if name == "toggle":
                p.setPen(Qt.NoPen)
                p.setBrush(accent.lighter(112) if hot else accent)
                p.drawEllipse(center, radius, radius)
                running = e.state is State.RUNNING
                nudge = QPointF(0 if running else 1.5, 0)
                draw_glyph(p, "pause" if running else "play", center + nudge, radius * 1.05, QColor(t.on_accent))
                continue
            if hot:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(t.hover))
                p.drawEllipse(center, radius, radius)
            draw_glyph(p, glyphs[name], center, radius * 1.2, QColor(t.ink if hot else t.ink_2))

        for name, (center, radius) in self._badges().items():
            p.setPen(QPen(QColor(t.orb_edge), 1))
            p.setBrush(QColor(t.hover if name == self._hot else t.raised))
            p.drawEllipse(center, radius, radius)
            draw_glyph(p, "dango" if name == "feed" else "petal", center, radius * 1.35, QColor(t.ink_2))
            if name == "feed" and s.treats > 1:
                dot = QPointF(center.x() + radius * 0.75, center.y() - radius * 0.75)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(t.short))
                p.drawEllipse(dot, radius * 0.55, radius * 0.55)
                p.setFont(self._font(9, QFont.DemiBold))
                p.setPen(QColor(t.on_accent))
                p.drawText(QRectF(dot.x() - radius, dot.y() - radius, 2 * radius, 2 * radius), Qt.AlignCenter,
                           str(s.treats))

    # ------------------------------------------------------------------ input
    def _set_hot(self, name: str | None) -> None:
        if name == self._hot:
            return
        self._hot = name
        QToolTip.hideText()
        if name:
            self._tip_timer.start()
        else:
            self._tip_timer.stop()
        self.setCursor(Qt.PointingHandCursor if name and name != "time" else Qt.ArrowCursor)
        self.update()

    def _show_tip(self) -> None:
        name = self._hot
        if not name:
            return
        if name == "toggle":
            text = "Pause" if self.engine.is_running else "Start"
        elif name == "skip":
            text = f"Skip to {self.engine.skip_target().label.lower()}"
        else:
            text = TIPS.get(name, "")
        QToolTip.showText(QCursor.pos(), text, self)

    def _track_pointer(self, pos: QPointF) -> None:
        if self._inside(pos):
            self._close_timer.stop()
            if not self._hover and not self._open_timer.isActive():
                self._open_timer.start()
            hot = self._button_at(pos)
            if hot is None and self._on_pomo(pos):
                hot = "pomo"
                dx = pos.x() - self._last_move_x
                if abs(dx) > 2:  # rubbing back and forth pets him
                    self.pomo.rub(1 if dx > 0 else -1)
            elif hot is None and self._expand > 0.8 and self._time_rect().contains(pos):
                hot = "time"
            self._last_move_x = pos.x()
            self._set_hot(hot)
        else:
            self._open_timer.stop()
            self._set_hot(None)
            if self._hover and not self._close_timer.isActive():
                self._close_timer.start()

    def mouseMoveEvent(self, event) -> None:
        self._track_pointer(event.position())

    def enterEvent(self, event) -> None:
        self._track_pointer(event.position())
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._open_timer.stop()
        self._set_hot(None)
        if self._hover:
            self._close_timer.start()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        pos = event.position()
        if not self._inside(pos):
            event.ignore()
            return
        if event.button() == Qt.RightButton:
            QToolTip.hideText()
            self.interacted.emit()
            self._menu_open = True
            self._update_targets()
            self.menu_requested.emit(event.globalPosition().toPoint())  # blocks while the menu is open
            self._menu_open = False
            self._update_targets()
        elif event.button() == Qt.LeftButton:
            self._pressed = self._button_at(pos) or ("pomo" if self._on_pomo(pos) else "pill")

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.LeftButton or self._pressed is None:
            return
        pos = event.position()
        pressed, self._pressed = self._pressed, None
        if pressed == "pomo" and self._on_pomo(pos):
            self.interacted.emit()
            self.pomo.poke()
        elif pressed == "pill" and self._inside(pos) and self._expand < 0.5:
            self.pin(4.0)  # a click opens it too, for touch screens
        elif pressed == self._button_at(pos):
            self._trigger(pressed)

    def mouseDoubleClickEvent(self, event) -> None:
        pos = event.position()
        if event.button() == Qt.LeftButton and self._expand > 0.8 and self._time_rect().contains(pos):
            self.begin_edit()
        elif event.button() == Qt.LeftButton and self._on_pomo(pos) and self._button_at(pos) is None:
            self.pomo.twirl()
            self.interacted.emit()
        else:
            self.mousePressEvent(event)

    def wheelEvent(self, event) -> None:
        if self._editing:
            return
        self._wheel_acc += event.angleDelta().y() or event.angleDelta().x()
        steps = int(self._wheel_acc / 120)
        if steps:
            self._wheel_acc -= steps * 120
            big = event.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier)
            self.engine.adjust(steps * (300 if big else 60))
            self.interacted.emit()

    def _trigger(self, name: str) -> None:
        self.interacted.emit()
        if name == "toggle":
            self.engine.toggle()
        elif name == "reset":
            self.engine.reset()
        elif name == "skip":
            self.engine.skip()
        elif name == "dashboard":
            self.dashboard_requested.emit()
        elif name == "chat":
            self.chat_requested.emit()
        elif name == "settings":
            self.settings_requested.emit()
        elif name == "style":
            self.ctrl.set_display_mode("orb")
        elif name == "feed":
            self.ctrl.feed_pomo()
        elif name == "play":
            self.ctrl.open_game()

    # ------------------------------------------------------------ inline edit
    def begin_edit(self) -> None:
        if self._editing:
            return
        self._editing = True
        self._set_hot(None)
        accent = mode_color(self.ctrl.theme, self.ctrl.settings, self.engine.mode.value)
        self._edit.setGeometry(self._time_rect().adjusted(-2, 2, -10, -2).toRect())
        self._edit.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        t = self.ctrl.theme
        self._edit.style_for(accent, t.ink, t.on_accent, 30)
        self._update_targets()
        self.activateWindow()
        self._edit.begin(fmt_clock(self.engine.remaining))

    def _commit_edit(self, text: str) -> None:
        self._end_edit()
        if apply_typed_time(self.ctrl, text):
            self.interacted.emit()

    def _end_edit(self) -> None:
        self._editing = False
        self._update_targets()
