"""The floating circular timer.

A frameless, translucent, always-on-top window that paints a round face with a
progress ring. Drag it anywhere (it snaps to screen edges), hover for controls,
double-click the time to type a new one, scroll to nudge it by a minute.
"""
from __future__ import annotations

import math

from PySide6.QtCore import (
    QEasingCurve,
    QLineF,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QRegularExpression,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QPainter,
    QPen,
    QRadialGradient,
    QRegularExpressionValidator,
)
from PySide6.QtWidgets import QLineEdit, QToolTip, QWidget

from .engine import Mode, State
from .icons import draw_glyph
from .stats import fmt_clock
from .theme import FONT_FAMILY, mode_color

BASE = 190.0  # layout below is designed at this diameter and scaled
MARGIN = 18   # transparent border for the drop shadow and glow
SNAP = 28
EDGE_GAP = 16

TIPS = {
    "reset": "Reset  (R)",
    "dashboard": "Dashboard  (D)",
    "settings": "Settings",
    "time": "Double-click to type a time, scroll to adjust,\nright-click for more options",
}


def parse_duration(text: str) -> int | None:
    """'25' -> 25 min, '25:30' -> 25 min 30 s, '1:10:00' -> 70 min. None if invalid."""
    parts = text.strip().split(":")
    if not any(parts):
        return None
    try:
        nums = [int(p) if p else 0 for p in parts]
    except ValueError:
        return None
    if len(nums) == 1:
        secs = nums[0] * 60
    elif len(nums) == 2 and nums[1] < 60:
        secs = nums[0] * 60 + nums[1]
    elif len(nums) == 3 and nums[1] < 60 and nums[2] < 60:
        secs = nums[0] * 3600 + nums[1] * 60 + nums[2]
    else:
        return None
    return secs if secs > 0 else None


class _TimeEdit(QLineEdit):
    committed = Signal(str)
    cancelled = Signal()

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self._done = True
        self.setAlignment(Qt.AlignCenter)
        self.setValidator(QRegularExpressionValidator(QRegularExpression(r"^\d{0,3}(:\d{0,2}){0,2}$"), self))
        self.returnPressed.connect(self._commit)
        self.hide()

    def begin(self, text: str) -> None:
        self._done = False
        self.setText(text)
        self.show()
        self.setFocus(Qt.OtherFocusReason)
        self.selectAll()

    def _commit(self) -> None:
        if self._done:
            return
        self._done = True
        self.hide()
        self.committed.emit(self.text())

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self._done = True
            self.hide()
            self.cancelled.emit()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self._commit()


class OrbWidget(QWidget):
    dashboard_requested = Signal()
    settings_requested = Signal()
    menu_requested = Signal(QPoint)
    moved = Signal()
    interacted = Signal()

    def __init__(self, ctrl):
        super().__init__(None)
        self.ctrl = ctrl
        self.engine = ctrl.engine
        self._diam = BASE
        self._flags = None
        self._hover = 0.0
        self._inside = False
        self._hot: str | None = None
        self._pressed: str | None = None
        self._press_global: QPoint | None = None
        self._press_origin = QPoint()
        self._dragging = False
        self._wheel_acc = 0
        self._editing = False
        self._opacity = 1.0
        self._pulse = 0.0
        self._color = QColor()
        self._last_mode: Mode | None = None

        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.ClickFocus)
        self.setWindowTitle("FocusOrb")

        self._hover_anim = self._animation(170, lambda v: self._set("_hover", float(v)))
        self._opacity_anim = self._animation(260, lambda v: self._set("_opacity", float(v)))
        self._color_anim = self._animation(450, lambda v: self._set("_color", QColor(v)))
        self._pulse_anim = self._animation(1500, lambda v: self._set("_pulse", float(v)))
        self._pulse_anim.setEasingCurve(QEasingCurve.InOutSine)
        self._pulse_anim.setStartValue(0.0)
        self._pulse_anim.setKeyValueAt(0.5, 1.0)
        self._pulse_anim.setEndValue(0.0)

        self._tip_timer = QTimer(self)
        self._tip_timer.setSingleShot(True)
        self._tip_timer.timeout.connect(self._show_tip)

        self._edit = _TimeEdit(self)
        self._edit.committed.connect(self._commit_edit)
        self._edit.cancelled.connect(self._end_edit)

        self.engine.ticked.connect(self.update)
        self.engine.changed.connect(self._on_engine_changed)
        self.apply_settings()

    # ------------------------------------------------------------- setup/state
    def _animation(self, ms: int, setter) -> QVariantAnimation:
        anim = QVariantAnimation(self)
        anim.setDuration(ms)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.valueChanged.connect(setter)
        return anim

    def _set(self, attr: str, value) -> None:
        setattr(self, attr, value)
        self.update()

    @staticmethod
    def _run(anim: QVariantAnimation, start, end) -> None:
        anim.stop()
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.start()

    def apply_settings(self) -> None:
        s = self.ctrl.settings
        was_visible = self.isVisible()
        old_center = self.geometry().center()
        self._diam = float(s.orb_size)
        side = int(self._diam) + 2 * MARGIN
        flags = Qt.FramelessWindowHint | Qt.Tool | Qt.NoDropShadowWindowHint
        if s.always_on_top:
            flags |= Qt.WindowStaysOnTopHint
        if flags != self._flags:
            self._flags = flags
            self.setWindowFlags(flags)
            if was_visible:
                self.show()
        self.setFixedSize(side, side)
        if was_visible:
            self.move(old_center - QPoint(side // 2, side // 2))
            self.ensure_on_screen()
        self._color = self._target_color()
        self._last_mode = self.engine.mode
        self._opacity = self._target_opacity()
        self._layout_edit()
        self.update()

    def _target_color(self) -> QColor:
        return mode_color(self.ctrl.theme, self.ctrl.settings, self.engine.mode.value)

    def _target_opacity(self) -> float:
        s = self.ctrl.settings
        base = s.opacity / 100
        away = not self._inside and not self._editing and not self._dragging
        if s.fade_when_away and away and self.engine.state is State.RUNNING:
            return base * 0.55
        return base

    def _update_opacity(self) -> None:
        target = self._target_opacity()
        if abs(target - self._opacity) > 0.001:
            self._run(self._opacity_anim, self._opacity, target)

    def _on_engine_changed(self) -> None:
        target = self._target_color()
        if self._last_mode is not None and self.engine.mode is not self._last_mode and self.isVisible():
            self._run(self._color_anim, QColor(self._color), target)
        elif self._color_anim.state() != QVariantAnimation.Running:
            self._color = target
        self._last_mode = self.engine.mode

        waiting = self.engine.state is State.FINISHED
        if waiting and self._pulse_anim.loopCount() != -1:
            self._pulse_anim.stop()
            self._pulse_anim.setLoopCount(-1)
            self._pulse_anim.start()
        elif not waiting and self._pulse_anim.loopCount() == -1:
            self._pulse_anim.stop()
            self._pulse_anim.setLoopCount(1)
            self._pulse = 0.0
        self._update_opacity()
        self.update()

    def flash(self, loops: int = 3) -> None:
        if self._pulse_anim.loopCount() == -1 and self._pulse_anim.state() == QVariantAnimation.Running:
            return
        self._pulse_anim.stop()
        self._pulse_anim.setLoopCount(loops)
        self._pulse_anim.start()

    # --------------------------------------------------------------- geometry
    def _center(self) -> QPointF:
        return QPointF(self.width() / 2, self.height() / 2)

    def _k(self) -> float:
        return self._diam / BASE

    def circle_rect_global(self) -> QRect:
        d = int(self._diam)
        return QRect(self.mapToGlobal(QPoint(MARGIN, MARGIN)), QSize(d, d))

    def _in_circle(self, pos: QPointF) -> bool:
        return QLineF(pos, self._center()).length() <= self._diam / 2

    def _time_rect(self) -> QRectF:
        c, k = self._center(), self._k()
        return QRectF(c.x() - 64 * k, c.y() - 27 * k, 128 * k, 48 * k)

    def _button_geometry(self) -> dict[str, tuple[QPointF, float]]:
        c, k = self._center(), self._k()
        return {
            "dashboard": (QPointF(c.x() - 17 * k, c.y() - 58 * k), 12 * k),
            "settings": (QPointF(c.x() + 17 * k, c.y() - 58 * k), 12 * k),
            "reset": (QPointF(c.x() - 40 * k, c.y() + 40 * k), 14 * k),
            "toggle": (QPointF(c.x(), c.y() + 40 * k), 19 * k),
            "skip": (QPointF(c.x() + 40 * k, c.y() + 40 * k), 14 * k),
        }

    def _button_at(self, pos: QPointF) -> str | None:
        if self._hover < 0.5 or self._editing:
            return None
        slack = 3 * self._k()
        for name, (center, r) in self._button_geometry().items():
            if QLineF(pos, center).length() <= r + slack:
                return name
        return None

    # -------------------------------------------------------------- placement
    def place(self) -> None:
        s = self.ctrl.settings
        if s.orb_x is not None and s.orb_y is not None:
            self.move(s.orb_x, s.orb_y)
            if QGuiApplication.screenAt(self.geometry().center()) is not None:
                self.ensure_on_screen()
                return
        self.move_to_corner()

    def move_to_corner(self) -> None:
        geo = QGuiApplication.primaryScreen().availableGeometry()
        d = int(self._diam)
        self.move(geo.right() + 1 - EDGE_GAP - d - MARGIN, geo.bottom() + 1 - EDGE_GAP - d - MARGIN)

    def ensure_on_screen(self) -> None:
        self.move(self._clamp(self.pos(), snap=False))

    def _clamp(self, top_left: QPoint, snap: bool) -> QPoint:
        d = int(self._diam)
        left, top = top_left.x() + MARGIN, top_left.y() + MARGIN
        center = QPoint(left + d // 2, top + d // 2)
        screen = QGuiApplication.screenAt(center) or QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        right_edge, bottom_edge = geo.right() + 1, geo.bottom() + 1
        if snap:
            if abs(left - geo.left()) < SNAP + EDGE_GAP:
                left = geo.left() + EDGE_GAP
            elif abs(right_edge - (left + d)) < SNAP + EDGE_GAP:
                left = right_edge - EDGE_GAP - d
            if abs(top - geo.top()) < SNAP + EDGE_GAP:
                top = geo.top() + EDGE_GAP
            elif abs(bottom_edge - (top + d)) < SNAP + EDGE_GAP:
                top = bottom_edge - EDGE_GAP - d
        left = max(geo.left(), min(left, right_edge - d))
        top = max(geo.top(), min(top, bottom_edge - d))
        return QPoint(left - MARGIN, top - MARGIN)

    # --------------------------------------------------------------- painting
    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        p.setOpacity(self._opacity)
        t = self.ctrl.theme
        e = self.engine
        k = self._k()
        c = self._center()
        R = self._diam / 2
        accent = QColor(self._color)

        # drop shadow
        offset = QPointF(0, 3 * k)
        sr = R + MARGIN - 3 * k - 1
        shadow = QRadialGradient(c + offset, sr)
        shadow.setColorAt(0.0, QColor(0, 0, 0, t.shadow_alpha))
        shadow.setColorAt(R / sr * 0.94, QColor(0, 0, 0, t.shadow_alpha))
        shadow.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(shadow)
        p.drawEllipse(c + offset, sr, sr)

        # attention glow when a session ends
        if self._pulse > 0.01:
            hr = R + MARGIN - 1
            halo = QRadialGradient(c, hr)
            glow = QColor(accent)
            glow.setAlphaF(0.55 * self._pulse)
            clear = QColor(accent)
            clear.setAlpha(0)
            halo.setColorAt(R / hr * 0.97, glow)
            halo.setColorAt(1.0, clear)
            p.setBrush(halo)
            p.drawEllipse(c, hr, hr)

        # face
        face = QColor(t.orb_face)
        sheen = QRadialGradient(QPointF(c.x(), c.y() - R * 0.65), R * 1.7)
        sheen.setColorAt(0.0, face.lighter(125) if t.dark else QColor("#ffffff"))
        sheen.setColorAt(1.0, face if t.dark else face.darker(103))
        p.setBrush(sheen)
        p.setPen(QPen(QColor(t.orb_edge), 1))
        p.drawEllipse(c, R - 0.5, R - 0.5)

        # progress ring: the colored arc is the time left, draining clockwise
        ring_w = 7 * k
        rr = R - 13 * k
        ring = QRectF(c.x() - rr, c.y() - rr, 2 * rr, 2 * rr)
        track = QColor(accent)
        track.setAlpha(48 if t.dark else 42)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(track, ring_w, Qt.SolidLine, Qt.FlatCap))
        p.drawEllipse(ring)
        frac = e.fraction_left
        if frac > 0.0005:
            start = 90 - (1 - frac) * 360
            p.setPen(QPen(accent, ring_w, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(ring, round(start * 16), round(-frac * 360 * 16))
            if e.state in (State.RUNNING, State.PAUSED):
                a = math.radians(start)
                head = QPointF(c.x() + rr * math.cos(a), c.y() - rr * math.sin(a))
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(255, 255, 255, 235))
                p.drawEllipse(head, ring_w * 0.24, ring_w * 0.24)

        self._paint_label(p, c, k, accent)
        if not self._editing:
            self._paint_time(p, k)

        info = 1.0 - self._hover
        if info > 0.01:
            p.setOpacity(self._opacity * info)
            self._paint_info(p, c, k)
        if self._hover > 0.01:
            p.setOpacity(self._opacity * self._hover)
            self._paint_controls(p, k, accent)
        p.end()

    def _font(self, px: float, weight=QFont.Normal, spacing: float = 0.0) -> QFont:
        f = QFont(FONT_FAMILY)
        f.setPixelSize(max(8, round(px)))
        f.setWeight(weight)
        if spacing:
            f.setLetterSpacing(QFont.AbsoluteSpacing, spacing)
        return f

    def _paint_label(self, p: QPainter, c: QPointF, k: float, accent: QColor) -> None:
        t = self.ctrl.theme
        label = "PAUSED" if self.engine.state is State.PAUSED else self.engine.mode.label.upper()
        f = self._font(10.5 * k, QFont.DemiBold, 1.3 * k)
        fm = QFontMetricsF(f)
        tw = fm.horizontalAdvance(label) - 1.3 * k
        dot_r, gap = 3 * k, 6 * k
        x0 = c.x() - (2 * dot_r + gap + tw) / 2
        y = c.y() - 38 * k
        p.setPen(Qt.NoPen)
        p.setBrush(accent)
        p.drawEllipse(QPointF(x0 + dot_r, y), dot_r, dot_r)
        p.setFont(f)
        p.setPen(QColor(t.ink_2))
        p.drawText(QRectF(x0 + 2 * dot_r + gap, y - fm.height() / 2, tw + 8 * k, fm.height()),
                   Qt.AlignLeft | Qt.AlignVCenter, label)

    def _paint_time(self, p: QPainter, k: float) -> None:
        t = self.ctrl.theme
        text = fmt_clock(self.engine.remaining)
        p.setFont(self._font((40 if len(text) <= 5 else 31) * k, QFont.DemiBold))
        p.setPen(QColor(t.ink_2 if self.engine.state is State.PAUSED else t.ink))
        p.drawText(self._time_rect(), Qt.AlignCenter, text)

    def _paint_info(self, p: QPainter, c: QPointF, k: float) -> None:
        t, s, e = self.ctrl.theme, self.ctrl.settings, self.engine
        focus = mode_color(t, s, "focus")
        # one dot per focus session in the cycle before the long break
        n = e.cycle_length
        done = min(e.cycle_index, n)
        y = c.y() + 26 * k
        if n <= 8:
            r, step = 3.2 * k, 11 * k
            x0 = c.x() - (n - 1) * step / 2
            current = e.mode is Mode.FOCUS and e.state in (State.RUNNING, State.PAUSED)
            for i in range(n):
                center = QPointF(x0 + i * step, y)
                if i < done:
                    p.setPen(Qt.NoPen)
                    p.setBrush(focus)
                elif i == done and current:
                    p.setPen(QPen(focus, 1.4 * k))
                    p.setBrush(Qt.NoBrush)
                else:
                    ring = QColor(t.muted)
                    ring.setAlpha(150)
                    p.setPen(QPen(ring, 1.2 * k))
                    p.setBrush(Qt.NoBrush)
                p.drawEllipse(center, r, r)
        else:
            p.setFont(self._font(10.5 * k))
            p.setPen(QColor(t.ink_2))
            p.drawText(QRectF(c.x() - 60 * k, y - 8 * k, 120 * k, 16 * k), Qt.AlignCenter, f"Cycle {done}/{n}")

        count, goal = self.ctrl.today_count, s.daily_goal
        met = count >= goal
        p.setFont(self._font(11 * k, QFont.DemiBold if met else QFont.Normal))
        p.setPen(QColor(t.success if met else t.ink_2))
        text = f"✓ {count}/{goal} today" if met else f"{count}/{goal} today"
        p.drawText(QRectF(c.x() - 60 * k, c.y() + 35 * k, 120 * k, 16 * k), Qt.AlignCenter, text)

        if s.show_task and e.task:
            f = self._font(10.5 * k)
            p.setFont(f)
            p.setPen(QColor(t.muted))
            elided = QFontMetricsF(f).elidedText(e.task, Qt.ElideRight, 88 * k)
            p.drawText(QRectF(c.x() - 50 * k, c.y() + 51 * k, 100 * k, 15 * k), Qt.AlignCenter, elided)

    def _paint_controls(self, p: QPainter, k: float, accent: QColor) -> None:
        t, e = self.ctrl.theme, self.engine
        glyphs = {"reset": "reset", "skip": "skip", "dashboard": "chart", "settings": "gear"}
        for name, (center, r) in self._button_geometry().items():
            hot = name == self._hot
            if name == "toggle":
                p.setPen(Qt.NoPen)
                p.setBrush(accent.lighter(112) if hot else accent)
                p.drawEllipse(center, r, r)
                running = e.state is State.RUNNING
                nudge = QPointF(0 if running else 1.5 * k, 0)
                draw_glyph(p, "pause" if running else "play", center + nudge, r * 1.05, QColor(t.on_accent))
                continue
            if hot:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(t.hover))
                p.drawEllipse(center, r, r)
            draw_glyph(p, glyphs[name], center, r * 1.2, QColor(t.ink if hot else t.ink_2))

    # ------------------------------------------------------------------ input
    def _set_inside(self, inside: bool) -> None:
        if inside == self._inside:
            return
        self._inside = inside
        self._run(self._hover_anim, self._hover, 1.0 if inside else 0.0)
        if not inside:
            self._set_hot(None)
        self._update_opacity()

    def _set_hot(self, name: str | None) -> None:
        if name == self._hot:
            return
        self._hot = name
        QToolTip.hideText()
        if name:
            self._tip_timer.start(900 if name == "time" else 500)
        else:
            self._tip_timer.stop()
        self.setCursor(Qt.PointingHandCursor if name and name != "time" else Qt.ArrowCursor)
        self.update()

    def _show_tip(self) -> None:
        name = self._hot
        if not name:
            return
        if name == "toggle":
            text = "Pause  (Space)" if self.engine.is_running else "Start  (Space)"
        elif name == "skip":
            text = f"Skip to {self.engine.skip_target().label.lower()}  (S)"
        else:
            text = TIPS.get(name, "")
        QToolTip.showText(QCursor.pos(), text, self)

    def enterEvent(self, event) -> None:
        self._set_inside(self._in_circle(event.position()))
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if not self._dragging:
            self._set_inside(False)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        pos = event.position()
        if not self._in_circle(pos):
            event.ignore()
            return
        if event.button() == Qt.LeftButton:
            self._pressed = self._button_at(pos)
            self._press_global = event.globalPosition().toPoint()
            self._press_origin = self.pos()
            self._dragging = False
        elif event.button() == Qt.RightButton:
            QToolTip.hideText()
            self.interacted.emit()
            self.menu_requested.emit(event.globalPosition().toPoint())

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        if self._press_global is not None and event.buttons() & Qt.LeftButton:
            delta = event.globalPosition().toPoint() - self._press_global
            if not self._dragging and delta.manhattanLength() > 5:
                self._dragging = True
                self._pressed = None
                QToolTip.hideText()
                self.setCursor(Qt.ClosedHandCursor)
            if self._dragging:
                self.move(self._clamp(self._press_origin + delta, snap=True))
            return
        self._set_inside(self._in_circle(pos))
        if not self._inside:
            return
        button = self._button_at(pos)
        if button is None and self._time_rect().contains(pos) and not self._editing:
            button = "time"
        self._set_hot(button)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        if self._dragging:
            self._dragging = False
            self.setCursor(Qt.ArrowCursor)
            self._set_inside(self._in_circle(event.position()))
            self.moved.emit()
        elif self._pressed and self._pressed == self._button_at(event.position()):
            self._trigger(self._pressed)
        self._pressed = None
        self._press_global = None

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        pos = event.position()
        if self._button_at(pos) is not None:
            # second click of a fast double-click on a button still counts as a click
            self.mousePressEvent(event)
        elif self._time_rect().contains(pos):
            self.begin_edit()

    def wheelEvent(self, event) -> None:
        if self._editing:
            return
        delta = event.angleDelta().y() or event.angleDelta().x()
        self._wheel_acc += delta
        steps = int(self._wheel_acc / 120)
        if steps:
            self._wheel_acc -= steps * 120
            big = event.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier)
            self.engine.adjust(steps * (300 if big else 60))
            self.interacted.emit()

    def keyPressEvent(self, event) -> None:
        key = event.key()
        actions = {
            Qt.Key_Space: self.engine.toggle,
            Qt.Key_R: self.engine.reset,
            Qt.Key_S: self.engine.skip,
            Qt.Key_D: self.dashboard_requested.emit,
            Qt.Key_E: self.begin_edit,
            Qt.Key_Return: self.begin_edit,
            Qt.Key_Enter: self.begin_edit,
            Qt.Key_Up: lambda: self.engine.adjust(60),
            Qt.Key_Down: lambda: self.engine.adjust(-60),
        }
        if key in actions:
            actions[key]()
            self.interacted.emit()
        else:
            super().keyPressEvent(event)

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
        elif name == "settings":
            self.settings_requested.emit()

    # ------------------------------------------------------------ inline edit
    def _layout_edit(self) -> None:
        k = self._k()
        t = self.ctrl.theme
        self._edit.setGeometry(self._time_rect().adjusted(6 * k, 4 * k, -6 * k, -4 * k).toRect())
        self._edit.setStyleSheet(
            f"QLineEdit {{ background: transparent; border: none; border-radius: 0;"
            f" border-bottom: 2px solid {self._color.name()}; color: {t.ink}; padding: 0;"
            f" selection-background-color: {self._color.name()}; selection-color: {t.on_accent};"
            f" font-family: '{FONT_FAMILY}'; font-size: {round(32 * k)}px; font-weight: 600; }}"
        )

    def begin_edit(self) -> None:
        if self._editing:
            return
        self._editing = True
        self._set_hot(None)
        QToolTip.hideText()
        self._layout_edit()
        self.raise_()
        self.activateWindow()
        self._edit.begin(fmt_clock(self.engine.remaining))
        self._update_opacity()
        self.update()

    def _commit_edit(self, text: str) -> None:
        self._end_edit()
        seconds = parse_duration(text)
        if seconds is None:
            return
        e = self.engine
        if e.state in (State.IDLE, State.FINISHED):
            e.set_duration(seconds)
            if seconds % 60 == 0:
                self.ctrl.set_default_minutes(e.mode, seconds // 60)
        else:
            e.set_remaining(seconds)
        self.interacted.emit()

    def _end_edit(self) -> None:
        self._editing = False
        self._update_opacity()
        self.update()
