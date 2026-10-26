"""Pomo on the taskbar: the countdown as progress on its button, with reset, play/pause and skip on hover.

Pomo's timer windows are tool windows, which have no taskbar button, so a small stand-in window
carries it. The stand-in stays minimized: clicking the button brings the timer up instead, and
the hover preview is a live picture of the timer (Pomo, the time and the ring) drawn for Windows
on request. It all goes through the shell's ITaskbarList3 and DWM, called with ctypes.

  progress     green while a session runs, yellow while paused, none when idle
  buttons      reset, play/pause, skip (like a music player)
  overlay      a small "!" when a coding agent needs you
  title        "12:34 · Focus", so the button's label counts down too
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import QWidget

from . import APP_NAME
from .engine import State
from .icons import draw_glyph
from .stats import fmt_clock
from .theme import FONT_FAMILY, mode_color

WM_COMMAND = 0x0111
WM_SYSCOMMAND = 0x0112
WM_DWMSENDICONICTHUMBNAIL = 0x0323
WM_DWMSENDICONICLIVEPREVIEWBITMAP = 0x0326
SC_RESTORE, SC_MAXIMIZE, SC_CLOSE = 0xF120, 0xF030, 0xF060
THBN_CLICKED = 0x1800
DWMWA_FORCE_ICONIC_REPRESENTATION, DWMWA_HAS_ICONIC_BITMAP, DWMWA_DISALLOW_PEEK = 7, 10, 11
TBPF_NOPROGRESS, TBPF_NORMAL, TBPF_PAUSED = 0, 2, 8
THB_ICON, THB_TOOLTIP, THB_FLAGS = 0x2, 0x4, 0x8
THBF_ENABLED = 0
BUTTONS = (("reset", 1), ("toggle", 2), ("skip", 3))
PROGRESS_STEPS = 1000

# ITaskbarList3 methods, by their place in the vtable
HR_INIT, SET_PROGRESS_VALUE, SET_PROGRESS_STATE = 3, 9, 10
THUMB_BAR_ADD_BUTTONS, THUMB_BAR_UPDATE_BUTTONS, SET_OVERLAY_ICON, SET_THUMBNAIL_TOOLTIP = 15, 16, 18, 19


def light_taskbar() -> bool:
    """True when Windows draws its taskbar (and the thumbnail buttons) light."""
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return bool(winreg.QueryValueEx(key, "SystemUsesLightTheme")[0])
    except OSError:
        return False


class TaskbarList:
    """A thin ctypes wrapper over the shell's ITaskbarList3 (None-safe: every call is a no-op off Windows)."""

    def __init__(self):
        self.ptr = None
        if sys.platform != "win32":
            return
        import ctypes

        class GUID(ctypes.Structure):
            _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16),
                        ("d", ctypes.c_ubyte * 8)]

        def guid(text: str) -> GUID:
            g = GUID()
            ctypes.oledll.ole32.CLSIDFromString(text, ctypes.byref(g))
            return g

        self._ctypes = ctypes
        clsid = guid("{56FDF344-FD6D-11d0-958A-006097C9A090}")
        iid = guid("{EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF}")
        ptr = ctypes.c_void_p()
        try:
            ctypes.windll.ole32.CoInitialize(None)  # Qt's UI thread usually has COM up already
            if ctypes.windll.ole32.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid),
                                                    ctypes.byref(ptr)) == 0 and ptr.value:
                self.ptr = ptr
                self._call(HR_INIT, [])
        except OSError:
            self.ptr = None

    def _call(self, index: int, argtypes: list, *args) -> int:
        if self.ptr is None:
            return -1
        ctypes = self._ctypes
        vtable = ctypes.cast(self.ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        fn = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtable[index])
        return fn(self.ptr, *args)

    def set_progress(self, hwnd: int, done: int, total: int) -> None:
        c = self._ctypes if self.ptr else None
        if c:
            self._call(SET_PROGRESS_VALUE, [c.c_void_p, c.c_ulonglong, c.c_ulonglong], hwnd, done, total)

    def set_state(self, hwnd: int, state: int) -> None:
        c = self._ctypes if self.ptr else None
        if c:
            self._call(SET_PROGRESS_STATE, [c.c_void_p, c.c_int], hwnd, state)

    def set_tooltip(self, hwnd: int, text: str) -> None:
        c = self._ctypes if self.ptr else None
        if c:
            self._call(SET_THUMBNAIL_TOOLTIP, [c.c_void_p, c.c_wchar_p], hwnd, text)

    def set_overlay(self, hwnd: int, hicon: int, description: str) -> None:
        c = self._ctypes if self.ptr else None
        if c:
            self._call(SET_OVERLAY_ICON, [c.c_void_p, c.c_void_p, c.c_wchar_p], hwnd, hicon or None, description)

    def buttons(self, hwnd: int, items: list[tuple[int, int, str]], add: bool) -> int:
        """Add (once) or update the thumbnail buttons: (id, icon handle, tooltip)."""
        if self.ptr is None:
            return -1
        c = self._ctypes
        from ctypes import wintypes

        class THUMBBUTTON(c.Structure):
            _fields_ = [("dwMask", wintypes.DWORD), ("iId", wintypes.UINT), ("iBitmap", wintypes.UINT),
                        ("hIcon", c.c_void_p), ("szTip", c.c_wchar * 260), ("dwFlags", wintypes.DWORD)]

        array = (THUMBBUTTON * len(items))()
        for slot, (button_id, hicon, tip) in zip(array, items):
            slot.dwMask = THB_ICON | THB_TOOLTIP | THB_FLAGS
            slot.iId = button_id
            slot.hIcon = hicon
            slot.szTip = tip[:259]
            slot.dwFlags = THBF_ENABLED
        index = THUMB_BAR_ADD_BUTTONS if add else THUMB_BAR_UPDATE_BUTTONS
        return self._call(index, [c.c_void_p, c.c_uint, c.c_void_p], hwnd, len(items), c.byref(array))


class TaskbarButton(QWidget):
    """The stand-in window behind Pomo's taskbar button. It never shows itself: it stays minimized."""

    command = Signal(str)   # reset | toggle | skip
    activated = Signal()    # the button was clicked
    closed = Signal()       # "Close window" from the taskbar

    def __init__(self, ctrl):
        super().__init__(None, Qt.Window)
        self.ctrl = ctrl
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(ctrl.icon)
        self.setWindowOpacity(0.0)  # even the restore animation shows nothing
        self.resize(1, 1)
        self.move(-32000, -32000)
        self._list = None
        self._buttons_added = False
        self._icons: dict[str, int] = {}
        self._overlay_icon = 0
        self._overlay_on = False
        self._last = None
        self._created_msg = 0
        if sys.platform == "win32":
            import ctypes

            self._created_msg = ctypes.windll.user32.RegisterWindowMessageW("TaskbarButtonCreated")
        self._watch = QTimer(self)
        self._watch.setInterval(300)
        self._watch.timeout.connect(self._stay_minimized)

    # ------------------------------------------------------------- lifecycle
    @property
    def hwnd(self) -> int:
        return int(self.winId())

    def start(self) -> None:
        self.showMinimized()
        self._prepare_dwm()
        self._watch.start()
        QTimer.singleShot(500, self._setup)  # in case TaskbarButtonCreated came before we listened

    def stop(self) -> None:
        self._watch.stop()
        self.hide()
        self._buttons_added = False

    def _prepare_dwm(self) -> None:
        if sys.platform != "win32":
            return
        import ctypes

        on = ctypes.c_int(1)
        for attribute in (DWMWA_FORCE_ICONIC_REPRESENTATION, DWMWA_HAS_ICONIC_BITMAP, DWMWA_DISALLOW_PEEK):
            ctypes.windll.dwmapi.DwmSetWindowAttribute(self.hwnd, attribute, ctypes.byref(on), ctypes.sizeof(on))

    def _setup(self) -> None:
        if self._list is None:
            self._list = TaskbarList()
        if self._list.ptr is None:
            return
        items = self._button_items()
        # buttons can be added only once per button; after that (or if we were late) they're updated
        added = self._list.buttons(self.hwnd, items, add=True) == 0
        self._buttons_added = added or self._list.buttons(self.hwnd, items, add=False) == 0
        self._last = None
        self.refresh(force=True)

    def _stay_minimized(self) -> None:
        if self.isVisible() and not self.isMinimized():
            self.showMinimized()

    # ----------------------------------------------------------------- state
    def _glyph_icon(self, name: str) -> int:
        """An icon handle for a thumbnail button, cached (handles live as long as Pomo does)."""
        light = light_taskbar()
        key = f"{name}-{light}"
        if key not in self._icons:
            size = 32
            image = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
            image.fill(Qt.transparent)
            p = QPainter(image)
            p.setRenderHint(QPainter.Antialiasing)
            draw_glyph(p, name, QPointF(size / 2, size / 2), size * 0.78, QColor("#2b2b2b" if light else "#ffffff"))
            p.end()
            self._icons[key] = int(image.toHICON())
        return self._icons[key]

    def _button_items(self) -> list[tuple[int, int, str]]:
        e = self.ctrl.engine
        running = e.state is State.RUNNING
        tips = {"reset": "Reset", "toggle": "Pause" if running else "Start",
                "skip": f"Skip to {e.skip_target().label.lower()}"}
        glyphs = {"reset": "reset", "toggle": "pause" if running else "play", "skip": "skip"}
        return [(button_id, self._glyph_icon(glyphs[name]), tips[name]) for name, button_id in BUTTONS]

    def _overlay(self) -> int:
        if not self._overlay_icon:
            size = 32
            image = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
            image.fill(Qt.transparent)
            p = QPainter(image)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(QPen(QColor("#ffffff"), 2.5))
            p.setBrush(QColor(self.ctrl.theme.short))
            p.drawEllipse(QRectF(2, 2, size - 4, size - 4))
            p.setPen(QColor("#4a2420"))
            font = QFont(FONT_FAMILY)
            font.setPixelSize(22)
            font.setWeight(QFont.Bold)
            p.setFont(font)
            p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, "!")
            p.end()
            self._overlay_icon = int(image.toHICON())
        return self._overlay_icon

    def refresh(self, force: bool = False) -> None:
        """Bring the button up to date: progress, title, buttons and overlay (cheap when nothing changed)."""
        if not self.isVisible():
            return
        e = self.ctrl.engine
        clock = fmt_clock(e.remaining)
        alert = self.ctrl.agent_signal() == "alert"
        state = (e.state, e.mode, clock, alert)
        if state == self._last and not force:
            return
        previous, self._last = self._last, state
        label = "Paused" if e.state is State.PAUSED else e.mode.label
        self.setWindowTitle(f"{clock} · {label}" if e.state in (State.RUNNING, State.PAUSED) else APP_NAME)
        tl = self._list
        if tl is None or tl.ptr is None:
            return
        hwnd = self.hwnd
        if e.state in (State.RUNNING, State.PAUSED) and e.total > 0:
            tl.set_state(hwnd, TBPF_NORMAL if e.state is State.RUNNING else TBPF_PAUSED)
            tl.set_progress(hwnd, int(PROGRESS_STEPS * (1.0 - e.fraction_left)), PROGRESS_STEPS)
        else:
            tl.set_state(hwnd, TBPF_NOPROGRESS)
        tl.set_tooltip(hwnd, f"{APP_NAME} · {label} · {clock}")
        if force or previous is None or previous[0] != e.state or previous[1] != e.mode:
            if self._buttons_added:
                tl.buttons(hwnd, self._button_items(), add=False)
        if force or previous is None or previous[3] != alert:
            tl.set_overlay(hwnd, self._overlay() if alert else 0, "A coding agent needs you" if alert else "")
        if sys.platform == "win32":
            import ctypes

            ctypes.windll.dwmapi.DwmInvalidateIconicBitmaps(hwnd)  # the preview redraws when next shown

    def flash(self, times: int = 3) -> None:
        """Flash the button, say when a session ends."""
        if sys.platform != "win32" or not self.isVisible():
            return
        import ctypes
        from ctypes import wintypes

        class FLASHWINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("hwnd", wintypes.HWND), ("dwFlags", wintypes.DWORD),
                        ("uCount", wintypes.UINT), ("dwTimeout", wintypes.DWORD)]

        info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), self.hwnd, 0x2, times, 0)  # FLASHW_TRAY
        ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))

    # ------------------------------------------------------------- thumbnail
    def thumbnail(self, width: int, height: int) -> QImage:
        """The hover preview: Pomo, the time and the progress ring, on the theme's colors."""
        t, e = self.ctrl.theme, self.ctrl.engine
        image = QImage(max(32, width), max(32, height), QImage.Format_ARGB32_Premultiplied)
        image.fill(QColor(t.page))
        p = QPainter(image)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        w, h = image.width(), image.height()
        accent = mode_color(t, self.ctrl.settings, e.mode.value)
        side = min(w * 0.42, h * 0.78)
        ring = QRectF(w * 0.06, (h - side) / 2, side, side)
        track = QColor(accent)
        track.setAlpha(70)
        p.setPen(QPen(track, side * 0.07))
        p.drawEllipse(ring.adjusted(side * 0.05, side * 0.05, -side * 0.05, -side * 0.05))
        frac = e.fraction_left if e.state in (State.RUNNING, State.PAUSED) else 1.0
        if frac > 0:
            p.setPen(QPen(accent, side * 0.07, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(ring.adjusted(side * 0.05, side * 0.05, -side * 0.05, -side * 0.05),
                      round((90 - (1 - frac) * 360) * 16), round(-frac * 360 * 16))
        from .pomo import BODY_RY, Pomo

        pomo = Pomo()
        pomo.outfit = self.ctrl.current_outfit()
        pomo.mood = "focus" if e.state is State.RUNNING and not e.mode.is_break else "idle"
        body = side * 0.5
        pomo.paint(p, QPointF(ring.center().x(), ring.center().y() + body * BODY_RY * 0.4), body, ground_shadow=False)
        x = ring.right() + w * 0.06
        font = QFont(FONT_FAMILY)
        font.setPixelSize(max(10, int(h * 0.3)))
        font.setWeight(QFont.DemiBold)
        p.setFont(font)
        p.setPen(QColor(t.ink))
        p.drawText(QRectF(x, 0, w - x, h * 0.62), Qt.AlignLeft | Qt.AlignBottom, fmt_clock(e.remaining))
        font.setPixelSize(max(8, int(h * 0.13)))
        font.setWeight(QFont.Normal)
        p.setFont(font)
        p.setPen(QColor(t.ink_2))
        label = {State.RUNNING: e.mode.label, State.PAUSED: "Paused", State.IDLE: f"{e.mode.label} · ready",
                 State.FINISHED: f"Up next: {e.mode.label}"}[e.state]
        p.drawText(QRectF(x, h * 0.64, w - x, h * 0.3), Qt.AlignLeft | Qt.AlignTop, label)
        p.end()
        return image

    def _send_thumbnail(self, width: int, height: int) -> None:
        import ctypes

        image = self.thumbnail(width, height)
        bitmap = image.toHBITMAP()
        try:
            ctypes.windll.dwmapi.DwmSetIconicThumbnail(self.hwnd, ctypes.c_void_p(int(bitmap)), 0)
        finally:
            ctypes.windll.gdi32.DeleteObject(ctypes.c_void_p(int(bitmap)))

    # -------------------------------------------------------------- messages
    def nativeEvent(self, event_type, message):
        if bytes(event_type) != b"windows_generic_MSG":
            return False, 0
        from ctypes import wintypes

        msg = wintypes.MSG.from_address(int(message))
        if self._created_msg and msg.message == self._created_msg:
            QTimer.singleShot(0, self._setup)  # the button is new (or Explorer restarted): set it all up again
        elif msg.message == WM_COMMAND and (msg.wParam >> 16) & 0xFFFF == THBN_CLICKED:
            button_id = msg.wParam & 0xFFFF
            for name, known in BUTTONS:
                if known == button_id:
                    QTimer.singleShot(0, lambda n=name: self.command.emit(n))
            return True, 0
        elif msg.message == WM_SYSCOMMAND and msg.wParam & 0xFFF0 in (SC_RESTORE, SC_MAXIMIZE):
            QTimer.singleShot(0, self.activated.emit)  # stay minimized; bring up the timer instead
            return True, 0
        elif msg.message == WM_SYSCOMMAND and msg.wParam & 0xFFF0 == SC_CLOSE:
            QTimer.singleShot(0, self.closed.emit)
            return True, 0
        elif msg.message == WM_DWMSENDICONICTHUMBNAIL:
            self._send_thumbnail((msg.lParam >> 16) & 0xFFFF, msg.lParam & 0xFFFF)
            return True, 0
        elif msg.message == WM_DWMSENDICONICLIVEPREVIEWBITMAP:
            return True, 0  # peek is off for this window
        return False, 0

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange and self.isVisible() and not self.isMinimized():
            QTimer.singleShot(0, self.showMinimized)  # restored some other way (Alt+Tab): back down it goes
            QTimer.singleShot(0, self.activated.emit)

    def closeEvent(self, event) -> None:
        event.ignore()
        self.closed.emit()
