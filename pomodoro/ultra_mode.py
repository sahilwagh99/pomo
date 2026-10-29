"""Ultra focus mode, the windows: which apps to block, the transformation, and powering down.

Going Ultra: Pomo asks which apps to block (what's running now, plus the ones you picked last time),
then transforms in the middle of the screen. The apps stay blocked, breaks included, until you power
down, which takes a short wait so it's a decision, not a reflex.
"""
from __future__ import annotations

import random
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import ultra
from .applock import BROWSE_FILTER, DropList, label
from .pomo import Pomo
from .theme import FONT_FAMILY
from .winutil import set_dark_title_bar

POWER_DOWN_WAIT_S = 10
BURST_TOTAL_S = 3.4    # the transformation on screen, before it fades away
FADE_S = 0.4


def portrait(size: int) -> QWidget:
    """Pomo, powered up, with headroom for the hair and the aura (the chat's avatar is snug)."""
    from .chat import PomoAvatar
    from .pomo import BODY_RY

    class Portrait(PomoAvatar):
        def _anchor(self):
            s = self.width() / 1.7
            return QPointF(self.width() / 2, self.height() - s * BODY_RY - s * 0.32), s

    widget = Portrait(size)
    widget.setFixedSize(int(size * 1.7), int(size * 2.15))
    widget.pomo.power_up(instant=True)
    return widget


def _hint(text: str) -> QLabel:
    label_ = QLabel(text)
    label_.setWordWrap(True)
    label_.setProperty("role", "muted")
    return label_


class UltraPicker(QDialog):
    """Before going Ultra: pick the apps it blocks."""

    def __init__(self, ctrl, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctrl = ctrl
        s = ctrl.settings
        self.setWindowTitle("Ultra focus")
        self.setWindowIcon(ctrl.icon)
        self.setMinimumWidth(440)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 18)
        lay.setSpacing(8)
        top = QHBoxLayout()
        self.avatar = portrait(54)
        top.addWidget(self.avatar, 0, Qt.AlignTop)
        words = QVBoxLayout()
        title = QLabel("Go Ultra")
        title.setProperty("role", "h1")
        sub = QLabel("Which apps should Ultra focus block? They stay blocked until you power down, "
                     "breaks included.")
        sub.setWordWrap(True)
        sub.setProperty("role", "secondary")
        words.addWidget(title)
        words.addWidget(sub)
        top.addLayout(words, 1)
        lay.addLayout(top)
        lay.addSpacing(6)

        self.list = DropList("Drag games and apps here\nfrom your desktop, the Start menu or a folder")
        self.list.setMinimumHeight(190)
        self.list.dropped_unknown.connect(self._unknown)
        self.list.added.connect(lambda _apps: self.note.hide())
        lay.addWidget(self.list)
        chosen = list(s.ultra_apps)
        for exe in chosen:
            self.list.add_app(exe, checked=True)
        for exe in s.locked_apps:
            self.list.add_app(exe, checked=not chosen)  # your app lock list, ticked the first time
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setProperty("role", "muted")
        self.note.hide()
        lay.addWidget(self.note)
        row = QHBoxLayout()
        browse = QPushButton("Browse…")
        browse.setObjectName("ghost")
        browse.clicked.connect(self._browse)
        remove = QPushButton("Remove")
        remove.setObjectName("ghost")
        remove.clicked.connect(self.list.remove_selected)
        row.addWidget(browse)
        row.addWidget(remove)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(_hint("Shortcuts work, including Steam and Epic games. Untick an app to leave it out this time."))
        self.start_focus = QCheckBox("Start a focus session now")
        self.start_focus.setChecked(s.ultra_start_focus)
        lay.addWidget(self.start_focus)
        lay.addSpacing(6)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Not now")
        cancel.setObjectName("ghost")
        cancel.clicked.connect(self.reject)
        go = QPushButton("Go Ultra!")
        go.setObjectName("primary")
        go.setStyleSheet("QPushButton#primary { background: #ffd23f; color: #3f2a26; }"
                         " QPushButton#primary:hover { background: #ffe066; }")
        go.setDefault(True)
        go.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(go)
        lay.addLayout(buttons)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        set_dark_title_bar(self, self.ctrl.theme.dark)

    def _browse(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Block apps", "", BROWSE_FILTER)
        self.list.add_paths(paths)

    def _unknown(self, names: list[str]) -> None:
        self.note.setText(f"Pomo couldn't tell which program {', '.join(names)} starts. Try dragging the app's "
                          ".exe from its folder instead.")
        self.note.show()
        self.layout().activate()
        self.adjustSize()  # room for the note, below the list

    def chosen(self) -> list[str]:
        return self.list.apps()


class PowerDown(QDialog):
    """Leaving Ultra focus: your apps unlock, after a short wait."""

    def __init__(self, ctrl, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.setWindowTitle("Power down?")
        self.setWindowIcon(ctrl.icon)
        self.setMinimumWidth(380)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 18)
        lay.setSpacing(8)
        title = QLabel("Leave Ultra focus?")
        title.setProperty("role", "h1")
        apps = ", ".join(label(a) for a in ctrl.settings.ultra_apps) or "Nothing was blocked"
        body = QLabel(f"Pomo powers down and these unlock: {apps}.\n\nTake a breath first: is this a break you "
                      "planned, or a distraction knocking?")
        body.setWordWrap(True)
        body.setProperty("role", "secondary")
        lay.addWidget(title)
        lay.addWidget(body)
        lay.addSpacing(8)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.leave = QPushButton()
        self.leave.setObjectName("ghost")
        self.leave.setEnabled(False)
        self.leave.clicked.connect(self.accept)
        stay = QPushButton("Stay Ultra")
        stay.setObjectName("primary")
        stay.setDefault(True)
        stay.clicked.connect(self.reject)
        buttons.addWidget(self.leave)
        buttons.addWidget(stay)
        lay.addLayout(buttons)
        self._left = POWER_DOWN_WAIT_S
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self._tick(first=True)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        set_dark_title_bar(self, self.ctrl.theme.dark)

    def _tick(self, first: bool = False) -> None:
        if not first:
            self._left -= 1
        if self._left > 0:
            self.leave.setText(f"Power down ({self._left})")
        else:
            self._timer.stop()
            self.leave.setText("Power down")
            self.leave.setEnabled(True)


class UltraBurst(QWidget):
    """The transformation, big, in the middle of the screen: charge, flash, ULTRA FOCUS. Clicks go through."""

    def __init__(self, ctrl, blocked: list[str]):
        super().__init__(None)
        self.ctrl = ctrl
        self.blocked = blocked
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint
                            | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.resize(560, 640)
        self.pomo = Pomo(random.Random())
        self.pomo._next_fidget = 99.0
        self.pomo.power_up()
        self._start = time.monotonic()
        self._last = self._start
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def play(self) -> None:
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        self.move(area.center().x() - self.width() // 2, area.center().y() - self.height() // 2)
        self.show()
        self._timer.start()

    def _tick(self) -> None:
        now = time.monotonic()
        self.pomo.update(now - self._last)
        self._last = now
        elapsed = now - self._start
        if elapsed >= BURST_TOTAL_S + FADE_S:
            self._timer.stop()
            self.close()
            return
        self.setWindowOpacity(1.0 if elapsed < BURST_TOTAL_S else max(0.0, 1 - (elapsed - BURST_TOTAL_S) / FADE_S))
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        w, h = self.width(), self.height()
        center = QPointF(w / 2, h / 2 + 10)
        elapsed = time.monotonic() - self._start
        dim = QRadialGradient(center, w * 0.5)  # a soft dark halo, so the gold glows
        dim.setColorAt(0.0, QColor(30, 18, 8, 150))
        dim.setColorAt(0.7, QColor(30, 18, 8, 70))
        dim.setColorAt(1.0, QColor(30, 18, 8, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(dim)
        p.drawEllipse(center, w * 0.5, h * 0.5)
        self.pomo.paint(p, QPointF(center.x(), center.y() + 60), 160)
        shown = elapsed - ultra.CHARGE_S
        if shown > 0:
            pop = min(1.0, shown / 0.25)
            scale = 0.6 + 0.4 * ultra._overshoot(pop)
            p.save()
            p.translate(center.x(), h - 92)
            p.scale(scale, scale)
            font = QFont(FONT_FAMILY)
            font.setPixelSize(46)
            font.setWeight(QFont.Black)
            font.setLetterSpacing(QFont.AbsoluteSpacing, 3)
            text = QPainterPath()
            text.addText(0, 0, font, "ULTRA FOCUS")
            box = text.boundingRect()
            text.translate(-box.center().x(), -box.center().y())
            p.setOpacity(min(1.0, shown / 0.2))
            p.setPen(QPen(QColor("#5b2c29"), 6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            p.drawPath(text)
            p.setPen(Qt.NoPen)
            p.setBrush(ultra.GOLD)
            p.drawPath(text)
            p.restore()
            if self.blocked and shown > 0.5:
                p.setOpacity(min(1.0, (shown - 0.5) / 0.4))
                small = QFont(FONT_FAMILY)
                small.setPixelSize(15)
                small.setWeight(QFont.DemiBold)
                p.setFont(small)
                p.setPen(QColor("#fff3a8"))
                names = ", ".join(label(a) for a in self.blocked[:4]) + ("…" if len(self.blocked) > 4 else "")
                p.drawText(QRectF(0, h - 62, w, 24), Qt.AlignCenter, f"Blocked until you power down: {names}")
        p.end()
