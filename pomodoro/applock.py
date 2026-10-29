"""App lock: keep the apps that pull you away (games, mostly) out of the way while you focus.

Only an administrator can stop a program from starting at all, and Pomo never asks for admin rights.
So during a focus session Pomo watches which app is in front, and when it's one you locked it
minimizes it (or closes it, if you chose strict) and shows a lock card: back to work, or close it.
You can unlock it until your break, but only after a short wait, so it's a decision rather than a
reflex. Outside focus sessions, nothing is locked.
"""
from __future__ import annotations

import os
import sys
import time

from PySide6.QtCore import QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .presence import APP_LABELS, foreground_window, window_info

LOCK_POLL_MS = 700
REBLOCK_S = 1.2        # the same window again this soon counts as the same try
UNLOCK_WAIT_S = 10     # "Unlock until my break" waits this long first
NEVER_LOCK = {"pomo", "explorer", "taskmgr", "searchhost", "searchapp", "startmenuexperiencehost",
              "shellexperiencehost", "lockapp", "applicationframehost", "systemsettings", "textinputhost"}


def exe_key(name: str) -> str:
    """'C:\\Games\\Valorant.exe' or 'Valorant' -> 'valorant'."""
    base = os.path.basename(name.strip().replace("/", "\\").split("\\")[-1])
    return base[:-4].lower() if base.lower().endswith(".exe") else base.lower()


def label(exe: str) -> str:
    return APP_LABELS.get(exe.lower(), exe[:1].upper() + exe[1:] if exe else "That app")


def lockable(exe: str) -> bool:
    return bool(exe) and exe_key(exe) not in NEVER_LOCK


def minimize(hwnd: int) -> None:
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE


def close(hwnd: int) -> None:
    """Ask the window to close, as its own X button would (it may ask to save first)."""
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE


def running_apps() -> list[tuple[str, str]]:
    """(exe, a window title) for the apps with a window open now, to pick from."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    found: dict[str, str] = {}
    own = os.getpid()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0 and not user32.GetWindow(hwnd, 4):
            exe, title, pid = window_info(hwnd)  # (GW_OWNER 4: skip owned popups)
            if pid != own and lockable(exe) and exe_key(exe) not in found:
                found[exe_key(exe)] = title
        return True

    user32.EnumWindows(each, 0)
    return sorted(found.items(), key=lambda pair: label(pair[0]).lower())


class AppLock(QObject):
    """During focus: notices a locked app in front (blocked), counting the tries."""

    blocked = Signal(int, str)  # window handle, exe

    def __init__(self, settings, source=foreground_window, clock=time.monotonic, parent: QObject | None = None):
        super().__init__(parent)
        self.settings = settings
        self._source = source
        self._clock = clock
        self._own_pid = os.getpid()
        self._seen: dict[int, float] = {}
        self.unlocked: set[str] = set()   # unlocked until the break
        self.tries: dict[str, int] = {}   # exe -> times it came to the front this session
        self._timer = QTimer(self)
        self._timer.setInterval(LOCK_POLL_MS)
        self._timer.timeout.connect(self.sample)

    @property
    def active(self) -> bool:
        return self._timer.isActive()

    def set_active(self, active: bool) -> None:
        active = active and self.settings.app_lock and bool(self.settings.locked_apps)
        if active and not self._timer.isActive():
            self._seen.clear()
            self._timer.start()
            self.sample()
        elif not active and self._timer.isActive():
            self._timer.stop()
            self.unlocked.clear()  # a new focus session locks them again

    def sample(self) -> None:
        front = self._source()
        if front is None:
            return
        hwnd, exe, _title, pid = front
        key = exe_key(exe)
        if pid == self._own_pid or key not in self.settings.locked_apps or key in self.unlocked:
            return
        now = self._clock()
        if now - self._seen.get(hwnd, -1e9) < REBLOCK_S:
            return
        self._seen[hwnd] = now
        self.tries[key] = self.tries.get(key, 0) + 1
        self.blocked.emit(hwnd, key)

    def unlock(self, exe: str) -> None:
        self.unlocked.add(exe_key(exe))

    def take_tries(self) -> dict[str, int]:
        tries, self.tries = self.tries, {}
        return tries


def tries_text(tries: dict[str, int]) -> str:
    """'App lock kept away Valorant (3 tries) and Steam (1 try).'"""
    if not tries:
        return ""
    parts = [f"{label(exe)} ({n} {'try' if n == 1 else 'tries'})"
             for exe, n in sorted(tries.items(), key=lambda t: -t[1])]
    listed = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return f"App lock kept away {listed}."


class LockCard(QWidget):
    """The card a locked app gets instead of your attention: back to work, close it, or (after a wait) unlock it."""

    SHADOW = 16

    def __init__(self, ctrl):
        super().__init__(None)
        self.ctrl = ctrl
        self.hwnd = 0
        self.exe = ""
        self._wait = 0
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating, False)
        self.setFixedWidth(400 + 2 * self.SHADOW)
        lay = QVBoxLayout(self)
        m = self.SHADOW
        lay.setContentsMargins(m + 22, m + 20, m + 22, m + 18)
        lay.setSpacing(6)
        from .chat import PomoAvatar

        top = QHBoxLayout()
        self.avatar = PomoAvatar(64)
        self.avatar.pomo.set_mood("focus")
        top.addWidget(self.avatar, 0, Qt.AlignTop)
        text = QVBoxLayout()
        self.title = QLabel("Focus time")
        self.title.setProperty("role", "title")
        self.body = QLabel()
        self.body.setWordWrap(True)
        self.body.setProperty("role", "secondary")
        text.addWidget(self.title)
        text.addWidget(self.body)
        top.addLayout(text, 1)
        lay.addLayout(top)
        lay.addSpacing(8)
        buttons = QHBoxLayout()
        self.back = QPushButton("Back to work")
        self.back.setObjectName("primary")
        self.back.setCursor(Qt.PointingHandCursor)
        self.back.clicked.connect(self._back)
        self.close_btn = QPushButton()
        self.close_btn.setObjectName("ghost")
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.clicked.connect(self._close_app)
        buttons.addWidget(self.back)
        buttons.addWidget(self.close_btn)
        buttons.addStretch(1)
        lay.addLayout(buttons)
        self.unlock_btn = QPushButton()
        self.unlock_btn.setObjectName("ghost")
        self.unlock_btn.setCursor(Qt.PointingHandCursor)
        self.unlock_btn.setStyleSheet("QPushButton { font-size: 12px; padding: 2px 6px; }")
        self.unlock_btn.clicked.connect(self._unlock_clicked)
        lay.addWidget(self.unlock_btn, 0, Qt.AlignLeft)
        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._refresh)

    def show_for(self, hwnd: int, exe: str, closed: bool) -> None:
        self.hwnd, self.exe = hwnd, exe
        self._wait = 0
        self.unlock_btn.setEnabled(True)
        name = label(exe)
        self.title.setText(f"Focus time: {name} is locked")
        self.close_btn.setText(f"Close {name}")
        self.close_btn.setVisible(not closed)
        self._closed = closed
        self._refresh()
        self.adjustSize()
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        self.move(area.center().x() - self.width() // 2, area.center().y() - self.height() // 2 - 40)
        self.avatar.pomo.side_eye()
        self.show()
        self.raise_()
        self.activateWindow()
        self._tick.start()

    def _refresh(self) -> None:
        e = self.ctrl.engine
        from .stats import fmt_clock

        name = label(self.exe)
        what = "Pomo closed it." if getattr(self, "_closed", False) else "Pomo put it away."
        self.body.setText(f"{what} {fmt_clock(e.remaining)} left in this focus session, then it's all yours "
                          f"on your break.")
        if self._wait > 0:
            self.unlock_btn.setText(f"Unlocking in {self._wait}…  (click to cancel)")
            self._wait -= 1
            if self._wait == 0:
                self.ctrl.applock.unlock(self.exe)
                self.unlock_btn.setText(f"{name} is unlocked until your break")
                self.unlock_btn.setEnabled(False)
                QTimer.singleShot(1500, self.hide)
        elif self.unlock_btn.isEnabled():
            self.unlock_btn.setText(f"Unlock {name} until my break")

    def _unlock_clicked(self) -> None:
        if self._wait > 0:  # cancel the countdown
            self._wait = 0
        else:
            self._wait = UNLOCK_WAIT_S
        self._refresh()

    def _back(self) -> None:
        self.hide()
        work = self.ctrl.tracker.last_work
        if work is not None and work.hwnd != self.hwnd:
            from . import winutil

            winutil.activate_window(work.hwnd)

    def _close_app(self) -> None:
        close(self.hwnd)
        self.hide()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._tick.stop()
        self._wait = 0

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self._back()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, _event) -> None:
        t = self.ctrl.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        m = self.SHADOW
        rect = QRectF(self.rect()).adjusted(m, m, -m, -m)
        for i in range(m, 0, -2):  # a soft shadow
            shade = QColor(0, 0, 0, int(26 * (1 - i / m)))
            p.setPen(Qt.NoPen)
            p.setBrush(shade)
            p.drawRoundedRect(rect.adjusted(-i, -i + 4, i, i + 4), 16 + i, 16 + i)
        p.setBrush(QColor(t.surface))
        p.setPen(QPen(QColor(*t.border_rgba), 1))
        p.drawRoundedRect(rect, 16, 16)
        p.end()
