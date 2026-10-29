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
from PySide6.QtWidgets import QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget

from .presence import APP_LABELS, foreground_window

LOCK_POLL_MS = 700
REBLOCK_S = 1.2        # the same window again this soon counts as the same try
UNLOCK_WAIT_S = 10     # "Unlock until my break" waits this long first
NEVER_LOCK = {"pomo", "explorer", "taskmgr", "searchhost", "searchapp", "startmenuexperiencehost",
              "shellexperiencehost", "lockapp", "applicationframehost", "systemsettings", "textinputhost"}


BROWSE_FILTER = "Programs and shortcuts (*.exe *.lnk *.url)"
NAMES: dict[str, str] = {}  # exe -> the name you know it by (from the shortcut you dragged in); see Settings.app_names
HELPER_WORDS = ("unins", "crash", "redist", "vc_", "dxsetup", "setup", "install", "updater", "report", "helper",
                "cefprocess", "webhelper", "launcherprereq", "easyanticheat", "battleye")


def exe_key(name: str) -> str:
    """'C:\\Games\\Valorant.exe' or 'Valorant' -> 'valorant'."""
    base = os.path.basename(name.strip().replace("/", "\\").split("\\")[-1])
    return base[:-4].lower() if base.lower().endswith(".exe") else base.lower()


def label(exe: str) -> str:
    key = exe.lower()
    return NAMES.get(key) or APP_LABELS.get(key, exe[:1].upper() + exe[1:] if exe else "That app")


# ------------------------------------------------------------------ what a dropped file starts
def resolve_app(path: str) -> list[tuple[str, str]]:
    """The programs a file you dragged in (or picked) stands for: [(exe name, the name to show)].

    An .exe is itself. A desktop or Start menu shortcut (.lnk) points at its program. A Steam or Epic
    game's shortcut (.url) points at the game's own programs, found from the launcher's install records.
    Empty when it can't tell.
    """
    name, ext = os.path.splitext(os.path.basename(path))
    ext = ext.lower()
    found: list[str] = []
    if ext == ".exe":
        found = [exe_key(path)]
    elif ext == ".lnk":
        from PySide6.QtCore import QFileInfo

        target = QFileInfo(path).symLinkTarget()
        if target.lower().endswith(".exe"):
            found = [exe_key(target)]
    elif ext == ".url":
        url = _shortcut_url(path)
        if url.startswith("steam://rungameid/"):
            found = steam_game_exes(url.rsplit("/", 1)[-1].split("?")[0])
        elif url.startswith("com.epicgames.launcher://apps/"):
            found = epic_game_exes(url)
    return [(exe, name) for exe in found if lockable(exe)]


def _shortcut_url(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if line.strip().lower().startswith("url="):
                    return line.strip()[4:]
    except OSError:
        pass
    return ""


def _game_exes(folder: str, limit: int = 4) -> list[str]:
    """The programs in a game's folder that look like the game (not its installer or crash reporter)."""
    out: list[str] = []
    base_depth = folder.rstrip("\\/").count(os.sep)
    for root, dirs, files in os.walk(folder):
        if root.count(os.sep) - base_depth >= 3:
            dirs[:] = []
        for f in files:
            low = f.lower()
            if low.endswith(".exe") and not any(word in low for word in HELPER_WORDS):
                out.append(exe_key(f))
        if len(out) >= limit:
            break
    return list(dict.fromkeys(out))[:limit]


def steam_game_exes(app_id: str) -> list[str]:
    """A Steam game's programs, from its appmanifest in any of your Steam libraries."""
    if sys.platform != "win32" or not app_id.isdigit():
        return []
    import re
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            steam = winreg.QueryValueEx(key, "SteamPath")[0]
    except OSError:
        return []
    libraries = [steam]
    try:
        with open(os.path.join(steam, "steamapps", "libraryfolders.vdf"), encoding="utf-8", errors="ignore") as fh:
            libraries += [p.replace("\\\\", "\\") for p in re.findall(r'"path"\s+"([^"]+)"', fh.read())]
    except OSError:
        pass
    for library in dict.fromkeys(libraries):
        manifest = os.path.join(library, "steamapps", f"appmanifest_{app_id}.acf")
        try:
            with open(manifest, encoding="utf-8", errors="ignore") as fh:
                match = re.search(r'"installdir"\s+"([^"]+)"', fh.read())
        except OSError:
            continue
        if match:
            return _game_exes(os.path.join(library, "steamapps", "common", match.group(1)))
    return []


def epic_game_exes(url: str) -> list[str]:
    """An Epic Games game's program, from the launcher's install manifests."""
    import json
    from urllib.parse import unquote

    app = unquote(url.split("apps/", 1)[-1].split("?")[0]).split(":")[-1]
    folder = os.path.expandvars(r"%PROGRAMDATA%\Epic\EpicGamesLauncher\Data\Manifests")
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    for name in names:
        if not name.endswith(".item"):
            continue
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as fh:
                item = json.load(fh)
        except (OSError, ValueError):
            continue
        if app and app in (item.get("AppName"), item.get("CatalogItemId"), item.get("MainGameAppName")):
            exe = item.get("LaunchExecutable", "")
            return [exe_key(exe)] if exe.lower().endswith(".exe") else []
    return []


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


class DropList(QListWidget):
    """A list of apps you fill by dragging them in from the desktop, the Start menu or a folder.

    Each app is a checkable row (exe name in its data). dropped_unknown carries the names of files it
    couldn't work out a program for, so the window can say so.
    """

    dropped_unknown = Signal(list)
    added = Signal(list)  # [(exe, name)] newly in the list

    def __init__(self, hint: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.hint = hint
        self.setAcceptDrops(True)
        self.setDragDropMode(QListWidget.DropOnly)

    def add_app(self, exe: str, name: str = "", checked: bool = True) -> None:
        key = exe_key(exe)
        if not lockable(key):
            return
        if name:
            NAMES[key] = name
        for i in range(self.count()):
            if self.item(i).data(Qt.UserRole) == key:
                self.item(i).setCheckState(Qt.Checked if checked else Qt.Unchecked)
                return
        item = QListWidgetItem(f"{label(key)}  ({key}.exe)")
        item.setData(Qt.UserRole, key)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        self.addItem(item)

    def add_paths(self, paths: list[str]) -> None:
        unknown, added = [], []
        for path in paths:
            apps = resolve_app(path)
            if not apps:
                unknown.append(os.path.splitext(os.path.basename(path))[0])
            for exe, name in apps:
                self.add_app(exe, name)
                added.append((exe, name))
        if added:
            self.added.emit(added)
        if unknown:
            self.dropped_unknown.emit(unknown)

    def apps(self, checked_only: bool = True) -> list[str]:
        return [self.item(i).data(Qt.UserRole) for i in range(self.count())
                if not checked_only or self.item(i).checkState() == Qt.Checked]

    def remove_selected(self) -> None:
        for item in self.selectedItems():
            self.takeItem(self.row(item))

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            event.acceptProposedAction()
            self.add_paths(paths)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self.count() == 0:  # an empty list says what to do
            p = QPainter(self.viewport())
            p.setPen(self.palette().placeholderText().color())
            p.drawText(self.viewport().rect().adjusted(16, 0, -16, 0), Qt.AlignCenter | Qt.TextWordWrap, self.hint)
            p.end()


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
        self._focusing = False
        self.unlocked: set[str] = set()   # unlocked until the break
        self.tries: dict[str, int] = {}   # exe -> times it came to the front this session
        self._timer = QTimer(self)
        self._timer.setInterval(LOCK_POLL_MS)
        self._timer.timeout.connect(self.sample)

    @property
    def active(self) -> bool:
        return self._timer.isActive()

    def set_active(self, focusing: bool) -> None:
        """A focus session started or ended."""
        if self._focusing and not focusing:
            self.unlocked.clear()  # a new focus session locks them again
        self._focusing = focusing
        self.refresh()

    def locked(self) -> set[str]:
        """What's locked right now: your app lock list during focus, and Ultra focus's apps the whole time it's on."""
        s = self.settings
        apps = set(s.locked_apps) - self.unlocked if self._focusing and s.app_lock else set()
        if s.ultra_on:
            apps |= set(s.ultra_apps)  # no unlocking these: only powering down does
        return apps

    def ultra_app(self, exe: str) -> bool:
        return self.settings.ultra_on and exe_key(exe) in self.settings.ultra_apps

    def refresh(self) -> None:
        """Watch while anything is locked."""
        if self.locked() and not self._timer.isActive():
            self._seen.clear()
            self._timer.start()
            self.sample()
        elif not self.locked() and self._timer.isActive():
            self._timer.stop()

    def sample(self) -> None:
        front = self._source()
        if front is None:
            return
        hwnd, exe, _title, pid = front
        key = exe_key(exe)
        if pid == self._own_pid or key not in self.locked():
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
        from .ultra_mode import portrait

        top = QHBoxLayout()
        self.avatar = portrait(46)
        self.avatar.pomo.power_down()
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

    def show_for(self, hwnd: int, exe: str, closed: bool, ultra: bool = False) -> None:
        self.hwnd, self.exe = hwnd, exe
        self._wait = 0
        self._ultra = ultra
        self.unlock_btn.setEnabled(True)
        name = label(exe)
        self.title.setText(f"Ultra focus: {name} is blocked" if ultra else f"Focus time: {name} is locked")
        if ultra:
            self.avatar.pomo.power_up(instant=True)
        else:
            self.avatar.pomo.power_down()
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
        if getattr(self, "_ultra", False):
            self.body.setText(f"{what} It stays blocked until you power down Ultra focus, breaks included.")
            self.unlock_btn.setText("Power down Ultra focus…")
            return
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
        if getattr(self, "_ultra", False):
            self.hide()
            self.ctrl.leave_ultra()
            return
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
