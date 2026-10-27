"""Desktop toasts and alert sounds."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from .sounds import render_wav

try:
    import winsound
except ImportError:  # not on Windows
    winsound = None


class Notifier(QObject):
    def __init__(self, settings, cache_dir: Path, tray: QSystemTrayIcon | None, parent: QObject | None = None):
        super().__init__(parent)
        self.settings = settings
        self.cache_dir = cache_dir
        self.tray = tray
        self._lengths: dict[Path, float] = {}
        self._pending: list[QTimer] = []
        self.on_click = None  # what clicking the latest notification does, besides bringing the timer up

    def toast(self, title: str, message: str, on_click=None) -> bool:
        """A Windows notification. True if it was shown."""
        if not self.settings.desktop_notifications or self.tray is None:
            return False
        if self.tray.isVisible() and QSystemTrayIcon.supportsMessages():
            self.on_click = on_click
            self.tray.showMessage(title, message, self.tray.icon(), 8000)
            return True
        return False

    def clicked(self) -> None:
        action, self.on_click = self.on_click, None
        if action is not None:
            action()

    def alert(self) -> None:
        if self.settings.sound_enabled:
            self.play(self.settings.sound_name, self.settings.volume, self.settings.sound_repeat)

    def play(self, name: str, volume: int, repeat: int = 1) -> None:
        self.stop()
        path = self.cache_dir / f"{name.lower()}_{volume}.wav"
        if path not in self._lengths or not path.exists():
            self._lengths[path] = render_wav(name, volume, path)
        gap_ms = int(self._lengths[path] * 1000) + 350
        self._play_file(path)
        for i in range(1, max(1, repeat)):
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda p=path: self._play_file(p))
            timer.start(gap_ms * i)
            self._pending.append(timer)

    def stop(self) -> None:
        for timer in self._pending:
            timer.stop()
            timer.deleteLater()
        self._pending.clear()
        if winsound is not None:
            winsound.PlaySound(None, 0)

    @staticmethod
    def _play_file(path: Path) -> None:
        if winsound is not None and sys.platform == "win32":
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        else:
            QApplication.beep()
