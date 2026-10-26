"""Are you here, and what's in front? Away detection and the distraction check-in.

Away detection asks Windows how long ago the keyboard or mouse was last used.
The distraction check-in looks at the foreground window: its app, and its title only to
match your list of distracting sites (a browser's title holds the page name). Pomo keeps
app and site names with seconds; it never stores titles or anything you see or type.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import date

from PySide6.QtCore import QObject, QTimer, Signal

AWAY_POLL_MS = 5000
GUARD_POLL_MS = 2000
NUDGE_AFTER_S = 20      # on a distracting app this long, in one go
NUDGE_EVERY_S = 300     # at most one nudge per five minutes
BACK_IDLE_S = 3.0       # input this recent means you're back

BROWSERS = {"chrome", "msedge", "firefox", "brave", "opera", "vivaldi", "arc", "iexplore"}
APP_LABELS = {
    "code": "VS Code", "chrome": "Chrome", "msedge": "Edge", "firefox": "Firefox", "brave": "Brave",
    "opera": "Opera", "vivaldi": "Vivaldi", "windowsterminal": "Terminal", "cmd": "Terminal",
    "powershell": "Terminal", "pwsh": "Terminal", "explorer": "File Explorer", "winword": "Word",
    "excel": "Excel", "powerpnt": "PowerPoint", "outlook": "Outlook", "olk": "Outlook", "onenote": "OneNote",
    "ms-teams": "Teams", "teams": "Teams", "slack": "Slack", "discord": "Discord", "spotify": "Spotify",
    "notion": "Notion", "obsidian": "Obsidian", "figma": "Figma", "zoom": "Zoom", "devenv": "Visual Studio",
    "idea64": "IntelliJ IDEA", "pycharm64": "PyCharm", "acrobat": "Acrobat", "steam": "Steam",
    "steamwebhelper": "Steam", "notepad": "Notepad", "wordpad": "WordPad", "vlc": "VLC",
}
SITE_LABELS = {
    "youtube": "YouTube", "netflix": "Netflix", "twitch": "Twitch", "tiktok": "TikTok", "instagram": "Instagram",
    "facebook": "Facebook", "reddit": "Reddit", "twitter": "Twitter", "9gag": "9GAG", "prime video": "Prime Video",
    "disney+": "Disney+", "steam": "Steam",
}


def idle_seconds() -> float:
    """Seconds since the last keyboard or mouse input anywhere on the PC."""
    if sys.platform != "win32":
        return 0.0
    import ctypes
    from ctypes import wintypes

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    info = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    millis = (ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF
    return millis / 1000.0


def foreground() -> tuple[str, str, int] | None:
    """(exe name without .exe, window title, process id) of the window in front, or None."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    length = user32.GetWindowTextLengthW(hwnd)
    title = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, title, length + 1)
    exe = ""
    handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if handle:
        try:
            size = wintypes.DWORD(1024)
            path = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
                exe = os.path.splitext(os.path.basename(path.value))[0]
        finally:
            kernel32.CloseHandle(handle)
    return exe, title.value, pid.value


def classify(exe: str, title: str, distracting: list[str]) -> tuple[str, str | None]:
    """(label to record, the distracting word it matched or None).

    In a browser, a matched site is recorded by its name ("YouTube"); otherwise the app ("Chrome").
    """
    low_exe, low_title = exe.lower(), title.lower()
    app = APP_LABELS.get(low_exe, exe[:1].upper() + exe[1:] if exe else "Unknown")
    if low_exe in BROWSERS:  # a browser's title is the page: "Lo-fi beats - YouTube - Google Chrome"
        match = next((word for word in distracting if word and word in low_title), None)
        return (SITE_LABELS.get(match, match.title()), match) if match else (app, None)
    # any other app counts by what it is, not by what its title mentions (say, youtube_api.py in VS Code)
    match = next((word for word in distracting if word and word in low_exe), None)
    return app, match


# where people run coding agents: a terminal, or an editor with one built in
TERMINAL_APPS = {"windowsterminal", "cmd", "powershell", "pwsh", "conhost", "openconsole", "wezterm-gui", "alacritty",
                 "mintty", "hyper", "warp", "tabby", "code", "code - insiders", "cursor", "windsurf", "zed",
                 "idea64", "pycharm64", "webstorm64", "rider64", "goland64", "clion64", "devenv", "sublime_text"}


def user_at_terminal() -> bool:
    """True when a terminal or code editor is in front: the user can answer Claude Code right there."""
    front = foreground()
    return front is not None and front[0].lower() in TERMINAL_APPS


class AwayWatcher(QObject):
    """Notices when nobody has used the PC for a while (during focus), and when you come back."""

    went_away = Signal(float)   # seconds idle when noticed
    came_back = Signal(float)   # seconds away in total

    def __init__(self, idle_source=idle_seconds, clock=time.monotonic, parent: QObject | None = None):
        super().__init__(parent)
        self._idle = idle_source
        self._clock = clock
        self.away_since: float | None = None
        self.threshold_s = 0.0
        self.active = False
        self._timer = QTimer(self)
        self._timer.setInterval(AWAY_POLL_MS)
        self._timer.timeout.connect(self.check)

    def configure(self, active: bool, threshold_s: float) -> None:
        """active: a focus session is running (or we're waiting for you to come back)."""
        self.active, self.threshold_s = active, threshold_s
        if (active and threshold_s > 0) or self.away_since is not None:
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()

    def check(self) -> None:
        idle = self._idle()
        if self.away_since is None:
            if self.active and self.threshold_s > 0 and idle >= self.threshold_s:
                self.away_since = self._clock() - idle
                self.went_away.emit(idle)
        elif idle < BACK_IDLE_S:
            away = self._clock() - self.away_since
            self.away_since = None
            self.came_back.emit(away)
            if not (self.active and self.threshold_s > 0):
                self._timer.stop()


class FocusGuard(QObject):
    """During focus, keeps track of which app or site is in front and nudges after a while on a distracting one."""

    nudge = Signal(str, str)  # label ("YouTube"), the matched word ("youtube")

    def __init__(self, storage, settings, source=foreground, clock=time.monotonic, parent: QObject | None = None):
        super().__init__(parent)
        self.storage = storage
        self.settings = settings
        self._source = source
        self._clock = clock
        self._own_pid = os.getpid()
        self._last: float | None = None
        self._streak = 0.0
        self._last_nudge = -1e9
        self.session_distracted = 0.0
        self._pending: dict[str, list] = {}  # label -> [seconds, distracting]
        self._timer = QTimer(self)
        self._timer.setInterval(GUARD_POLL_MS)
        self._timer.timeout.connect(self.sample)
        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(60_000)
        self._flush_timer.timeout.connect(self.flush)

    def set_active(self, active: bool) -> None:
        active = active and self.settings.focus_guard
        if active and not self._timer.isActive():
            self._last = self._clock()
            self._streak = 0.0
            self._timer.start()
            self._flush_timer.start()
        elif not active and self._timer.isActive():
            self.sample()
            self._timer.stop()
            self._flush_timer.stop()
            self.flush()
            self._last = None

    def sample(self) -> None:
        now = self._clock()
        dt = 0.0 if self._last is None else min(now - self._last, GUARD_POLL_MS / 1000 * 3)
        self._last = now
        front = self._source()
        if front is None or front[2] == self._own_pid:
            self._streak = 0.0
            return
        label, match = classify(front[0], front[1], self.settings.distracting)
        entry = self._pending.setdefault(label, [0.0, False])
        entry[0] += dt
        entry[1] = entry[1] or match is not None
        if match is None:
            self._streak = 0.0
            return
        self.session_distracted += dt
        self._streak += dt
        if self._streak >= NUDGE_AFTER_S and now - self._last_nudge >= NUDGE_EVERY_S:
            self._last_nudge = now
            self._streak = 0.0
            self.nudge.emit(label, match)

    def take_session(self) -> int:
        """Distracted seconds in the session that just ended (and start counting afresh)."""
        seconds, self.session_distracted = self.session_distracted, 0.0
        return int(round(seconds))

    def flush(self) -> None:
        today = date.today()
        for label, (seconds, distracting) in self._pending.items():
            if seconds >= 1:
                self.storage.add_app_time(today, label, int(round(seconds)), distracting)
        self._pending.clear()
