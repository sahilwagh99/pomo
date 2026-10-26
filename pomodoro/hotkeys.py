"""Global hotkeys: start/pause, skip, park a thought and more, from any app.

Uses Win32 RegisterHotKey. Windows posts WM_HOTKEY to the thread that registered the key,
and Qt hands every message it reads to native event filters, so one filter catches them.
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Qt, Signal
from PySide6.QtGui import QKeySequence

WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000

_NAMED_KEYS = {
    Qt.Key_Space: 0x20, Qt.Key_PageUp: 0x21, Qt.Key_PageDown: 0x22, Qt.Key_End: 0x23, Qt.Key_Home: 0x24,
    Qt.Key_Left: 0x25, Qt.Key_Up: 0x26, Qt.Key_Right: 0x27, Qt.Key_Down: 0x28, Qt.Key_Insert: 0x2D,
    Qt.Key_Delete: 0x2E, Qt.Key_Pause: 0x13,
}


def _key_value(key) -> int:
    return key.value if hasattr(key, "value") else int(key)


def virtual_key(key) -> int | None:
    """The Windows virtual-key code for a Qt key, or None for keys hotkeys don't support here."""
    k = _key_value(key)
    if _key_value(Qt.Key_A) <= k <= _key_value(Qt.Key_Z):
        return 0x41 + k - _key_value(Qt.Key_A)
    if _key_value(Qt.Key_0) <= k <= _key_value(Qt.Key_9):
        return 0x30 + k - _key_value(Qt.Key_0)
    if _key_value(Qt.Key_F1) <= k <= _key_value(Qt.Key_F24):
        return 0x70 + k - _key_value(Qt.Key_F1)
    for qt_key, vk in _NAMED_KEYS.items():
        if k == _key_value(qt_key):
            return vk
    return None


def parse(text: str) -> tuple[int, int] | None:
    """"Ctrl+Alt+Shift+P" -> (Windows modifier flags, virtual key), or None if it can't be a global hotkey.

    A hotkey needs Ctrl, Alt or Win (function keys may stand alone): a plain letter would swallow typing.
    """
    seq = QKeySequence.fromString(text or "", QKeySequence.PortableText)
    if seq.count() != 1:
        return None
    combo = seq[0]
    mods = combo.keyboardModifiers()
    flags = 0
    if mods & Qt.ControlModifier:
        flags |= MOD_CONTROL
    if mods & Qt.AltModifier:
        flags |= MOD_ALT
    if mods & Qt.ShiftModifier:
        flags |= MOD_SHIFT
    if mods & Qt.MetaModifier:
        flags |= MOD_WIN
    vk = virtual_key(combo.key())
    if vk is None:
        return None
    if not flags & (MOD_CONTROL | MOD_ALT | MOD_WIN) and not 0x70 <= vk <= 0x87:
        return None
    return flags, vk


class _HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, on_hotkey):
        super().__init__()
        self._on_hotkey = on_hotkey

    def nativeEventFilter(self, event_type, message):
        if bytes(event_type) == b"windows_generic_MSG":
            from ctypes import wintypes

            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY:
                self._on_hotkey(int(msg.wParam))
                return True, 0
        return False, 0


class GlobalHotkeys(QObject):
    """Registers named actions to key combinations; triggered(action) fires on the UI thread."""

    triggered = Signal(str)

    def __init__(self, app, parent: QObject | None = None):
        super().__init__(parent)
        self._ids: dict[int, str] = {}
        self._filter = None
        if sys.platform == "win32":
            self._filter = _HotkeyFilter(self._fire)
            app.installNativeEventFilter(self._filter)

    @property
    def available(self) -> bool:
        return self._filter is not None

    def register(self, bindings: dict[str, str]) -> dict[str, str]:
        """Replace all hotkeys with action -> "Ctrl+Alt+Shift+P". Returns action -> why it failed."""
        self.unregister_all()
        failed: dict[str, str] = {}
        if not self.available:
            return {action: "only on Windows" for action, text in bindings.items() if text}
        import ctypes

        user32 = ctypes.windll.user32
        for hotkey_id, (action, text) in enumerate(bindings.items(), start=1):
            if not text:
                continue
            parsed = parse(text)
            if parsed is None:
                failed[action] = "needs Ctrl, Alt or Win with a letter, digit or F-key"
                continue
            flags, vk = parsed
            if user32.RegisterHotKey(None, hotkey_id, flags | MOD_NOREPEAT, vk):
                self._ids[hotkey_id] = action
            else:
                failed[action] = "another app already uses it"
        return failed

    def unregister_all(self) -> None:
        if self._ids and self.available:
            import ctypes

            for hotkey_id in self._ids:
                ctypes.windll.user32.UnregisterHotKey(None, hotkey_id)
        self._ids.clear()

    def _fire(self, hotkey_id: int) -> None:
        action = self._ids.get(hotkey_id)
        if action:
            self.triggered.emit(action)
