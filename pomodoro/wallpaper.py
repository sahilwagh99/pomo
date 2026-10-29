"""Focus wallpaper: while you focus, today's step on your desktop, and your own wallpaper back after.

Your screen itself reminds you what you're doing. Pomo draws a calm picture with the step on it,
puts it on every monitor when a focus session starts, and puts each monitor's own wallpaper back
when the session ends, when a break starts and when Pomo quits.

What it's about to change is written to a small file first (and a copy of your picture is kept if
Windows only has its own cached copy), so if Pomo or the PC stops mid-session, your wallpaper comes
back the next time Pomo starts. If you change the wallpaper yourself while you focus, Pomo leaves
your new one alone. A slideshow or Windows Spotlight is left alone too: putting one of those back
isn't reliable, so the focus wallpaper simply doesn't show.

The changes go through the shell's IDesktopWallpaper on a worker thread (Explorer can take a moment
to redraw), so the timer never stutters.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import random
import shutil
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QLinearGradient, QPainter, QRadialGradient

log = logging.getLogger(__name__)

FILL = 4  # DWPOS_FILL: covers the monitor, cropping what doesn't fit
SPOTLIGHT_HINTS = ("irisservice", "contentdeliverymanager", "desktopspotlight")


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(round(a.red() + (b.red() - a.red()) * t), round(a.green() + (b.green() - a.green()) * t),
                  round(a.blue() + (b.blue() - a.blue()) * t))


# ------------------------------------------------------------------ the picture
def render(theme, step: str, where: str, size: QSize) -> QImage:
    """A calm wallpaper: Pomo reading, and the step you're on, large and centred."""
    from .pomo import BODY_RY, Pomo

    w, h = max(640, size.width()), max(400, size.height())
    unit = min(w / 1920, h / 1080)
    image = QImage(w, h, QImage.Format_RGB32)
    p = QPainter(image)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    page, focus = QColor(theme.page), QColor(theme.focus)
    sky = QLinearGradient(0, 0, 0, h)
    sky.setColorAt(0, page)
    sky.setColorAt(1, _mix(page, focus, 0.2))
    p.fillRect(image.rect(), sky)
    p.setPen(Qt.NoPen)
    for fx, fy, fr, token, alpha in ((0.14, 0.2, 0.3, "focus", 70), (0.88, 0.78, 0.34, "short", 55),
                                     (0.8, 0.12, 0.2, "long", 50), (0.08, 0.88, 0.22, "short", 40)):
        centre, radius = QPointF(w * fx, h * fy), h * fr
        glow = QRadialGradient(centre, radius)
        tint = QColor(getattr(theme, token))
        tint.setAlpha(alpha // 2 if theme.dark else alpha)  # on dark themes, softer still
        glow.setColorAt(0, tint)
        tint.setAlpha(0)
        glow.setColorAt(1, tint)
        p.setBrush(glow)
        p.drawEllipse(centre, radius, radius)

    pomo = Pomo(random.Random(7))
    pomo.reading = True
    for _ in range(40):
        pomo.update(0.03)
    pomo_size = 170 * unit
    top = h * 0.24
    pomo.paint(p, QPointF(w / 2, top + pomo_size * BODY_RY), pomo_size)
    y = top + pomo_size * BODY_RY * 2 + 54 * unit

    font = QFont(p.font())
    font.setBold(True)
    font.setPixelSize(max(12, round(22 * unit)))
    font.setLetterSpacing(QFont.AbsoluteSpacing, 3 * unit)
    p.setFont(font)
    p.setPen(QColor(theme.muted))
    p.drawText(QRectF(0, y, w, 40 * unit), Qt.AlignHCenter | Qt.AlignTop, "FOCUSING ON")
    y += 44 * unit

    box_w = w * 0.62
    title_font = QFont(p.font())
    title_font.setLetterSpacing(QFont.PercentageSpacing, 100)
    title_font.setBold(True)
    for px in (66, 56, 46, 38):  # smaller for a long step, so it stays within three lines
        title_font.setPixelSize(max(14, round(px * unit)))
        rect = QFontMetricsF(title_font).boundingRect(QRectF(0, 0, box_w, h), Qt.TextWordWrap | Qt.AlignHCenter, step)
        if rect.height() <= px * unit * 1.35 * 3:
            break
    p.setFont(title_font)
    p.setPen(QColor(theme.ink))
    p.drawText(QRectF((w - box_w) / 2, y, box_w, rect.height() + 4), Qt.TextWordWrap | Qt.AlignHCenter, step)
    y += rect.height() + 22 * unit

    small = QFont(p.font())
    small.setBold(False)
    small.setPixelSize(max(11, round(26 * unit)))
    p.setFont(small)
    if where:
        p.setPen(QColor(theme.ink_2))
        p.drawText(QRectF((w - box_w) / 2, y, box_w, 80 * unit), Qt.TextWordWrap | Qt.AlignHCenter, where)
    small.setPixelSize(max(10, round(22 * unit)))
    p.setFont(small)
    p.setPen(QColor(theme.muted))
    p.drawText(QRectF(0, h * 0.86, w, 40 * unit), Qt.AlignHCenter | Qt.AlignTop, "One thing at a time.")
    p.end()
    return image


def unsupported(path: str) -> bool:
    """A Windows Spotlight picture (it changes by itself, so it can't be put back as it was)."""
    low = path.casefold()
    return any(hint in low for hint in SPOTLIGHT_HINTS)


def background_type() -> int | None:
    """Windows' own note of the background: 0 picture, 1 solid color, 2 slideshow, 3 Spotlight (not always set)."""
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\Wallpapers") as k:
            return int(winreg.QueryValueEx(k, "BackgroundType")[0])
    except (OSError, ValueError):
        return None


# ------------------------------------------------------------------ Windows' wallpaper
class WindowsWallpaper:
    """The shell's IDesktopWallpaper through ctypes. Made, used and released on the worker thread."""

    def __init__(self):
        import ctypes

        class GUID(ctypes.Structure):
            _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16),
                        ("d", ctypes.c_ubyte * 8)]

        def guid(text: str) -> GUID:
            g = GUID()
            ctypes.oledll.ole32.CLSIDFromString(text, ctypes.byref(g))
            return g

        self._c = ctypes
        self.ptr = ctypes.c_void_p()
        hr = ctypes.windll.ole32.CoCreateInstance(
            ctypes.byref(guid("{C2CF3110-460E-4fc1-B9D0-8A1C0C9CC4BD}")), None, 0x17,  # CLSCTX_ALL
            ctypes.byref(guid("{B92B56A9-8B55-4E14-9A89-0199BBB6F93B}")), ctypes.byref(self.ptr))
        if hr != 0 or not self.ptr.value:
            raise OSError(f"IDesktopWallpaper isn't available ({hr:#x})")

    def _call(self, index: int, argtypes: list, *args) -> int:
        c = self._c
        vtable = c.cast(self.ptr, c.POINTER(c.POINTER(c.c_void_p))).contents
        return c.WINFUNCTYPE(c.c_long, c.c_void_p, *argtypes)(vtable[index])(self.ptr, *args)

    def _string(self, out) -> str:
        c = self._c
        text = c.wstring_at(out.value) if out.value else ""
        if out.value:
            c.windll.ole32.CoTaskMemFree(c.c_void_p(out.value))
        return text

    def monitors(self) -> list[str]:
        c = self._c
        count = c.c_uint()
        if self._call(6, [c.POINTER(c.c_uint)], c.byref(count)) != 0:
            return []
        ids = []
        for i in range(count.value):
            out = c.c_void_p()
            if self._call(5, [c.c_uint, c.POINTER(c.c_void_p)], i, c.byref(out)) == 0:
                ids.append(self._string(out))
        return [m for m in ids if m]

    def get(self, monitor: str) -> str:
        c = self._c
        out = c.c_void_p()
        self._call(4, [c.c_wchar_p, c.POINTER(c.c_void_p)], monitor, c.byref(out))
        return self._string(out)

    def set(self, monitor: str, path: str) -> bool:
        return self._call(3, [self._c.c_wchar_p, self._c.c_wchar_p], monitor, path) == 0

    def clear(self) -> None:
        """No picture: the solid color shows (SPI_SETDESKWALLPAPER with an empty path)."""
        self._c.windll.user32.SystemParametersInfoW(0x14, 0, "", 0x01 | 0x02)

    def position(self) -> int:
        c = self._c
        out = c.c_int(FILL)
        self._call(11, [c.POINTER(c.c_int)], c.byref(out))
        return out.value

    def set_position(self, value: int) -> None:
        self._call(10, [self._c.c_int], value)

    def slideshow(self) -> bool:
        c = self._c
        out = c.c_int()
        self._call(17, [c.POINTER(c.c_int)], c.byref(out))
        return bool(out.value & 1) and bool(out.value & 2)  # DSS_ENABLED and DSS_SLIDESHOW

    def close(self) -> None:
        if self.ptr.value:
            self._call(2, [])
            self.ptr = self._c.c_void_p()


# ------------------------------------------------------------------ swapping it, safely
class FocusWallpaper:
    """Shows the focus wallpaper and puts yours back. Every call returns at once; a worker thread does the rest."""

    def __init__(self, state_file: Path, image_dir: Path, make_backend=None):
        self.state_file = state_file
        self.image_dir = image_dir
        self._make = make_backend or WindowsWallpaper
        self._jobs: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self.showing = ""   # what the focus wallpaper on screen says ("" when it's yours)
        self._flip = False

    def show(self, image: QImage, key: str) -> None:
        if key == self.showing:
            return
        self.image_dir.mkdir(parents=True, exist_ok=True)
        self._flip = not self._flip  # a new file name, so Explorer doesn't reuse its cache of the old picture
        path = self.image_dir / f"focus_wallpaper_{'a' if self._flip else 'b'}.png"
        if not image.save(str(path)):
            log.warning("couldn't save the focus wallpaper to %s", path)
            return
        self.showing = key
        self._put(("show", str(path)))

    def restore(self) -> None:
        if self.showing or self.state_file.exists():
            self.showing = ""
            self._put(("restore", ""))

    def shutdown(self, wait_s: float = 4.0) -> None:
        """Pomo is quitting: your wallpaper back first, then the worker stops."""
        self.restore()
        if self._thread is not None:
            self._jobs.put(None)
            self._thread.join(wait_s)
            self._thread = None

    def wait(self, timeout: float = 5.0) -> None:
        """For tests: until the jobs so far are done."""
        done = threading.Event()
        self._put(("mark", done))
        done.wait(timeout)

    # ------------------------------------------------------------- the worker
    def _put(self, job) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="focus-wallpaper", daemon=True)
            self._thread.start()
        self._jobs.put(job)

    def _run(self) -> None:
        com = False
        if sys.platform == "win32":
            import ctypes

            com = ctypes.windll.ole32.CoInitializeEx(None, 0x2) in (0, 1)  # its own thread, so this is ours to undo
        backend = None
        try:
            while (job := self._jobs.get()) is not None:
                kind, arg = job
                if kind == "mark":
                    arg.set()
                    continue
                try:
                    if backend is None:
                        backend = self._make()
                    if kind == "show":
                        self._show(backend, arg)
                    else:
                        self._restore(backend)
                except Exception:  # never let the wallpaper take the timer down
                    log.exception("focus wallpaper: %s failed", kind)
        finally:
            if backend is not None and hasattr(backend, "close"):
                backend.close()
            if com:
                import ctypes

                ctypes.windll.ole32.CoUninitialize()

    def _read_state(self) -> dict | None:
        try:
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
            return state if isinstance(state, dict) and isinstance(state.get("monitors"), dict) else None
        except (OSError, ValueError):
            return None

    def _write_state(self, state: dict) -> None:
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(tmp, self.state_file)

    def _keep_copy(self, original: str, n: int) -> str:
        """Windows' own cached copy can be overwritten by the next wallpaper: keep one of ours to put back."""
        themes = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Themes"
        source = Path(original) if original and Path(original).is_file() else themes / "TranscodedWallpaper"
        inside_themes = themes.as_posix().casefold() in source.as_posix().casefold()
        if not inside_themes and source == Path(original):
            return original  # a picture of yours that stays where it is
        if not source.is_file():
            return original
        copy = self.image_dir / f"your_wallpaper_{n}.jpg"
        shutil.copyfile(source, copy)
        return str(copy)

    def _show(self, backend, path: str) -> None:
        state = self._read_state()
        if state is None:  # the first focus picture since yours: note yours down before touching anything
            if background_type() in (2, 3) or backend.slideshow():
                return
            originals = {m: backend.get(m) for m in backend.monitors()}
            if not originals or any(unsupported(p) for p in originals.values()):
                return
            originals = {m: self._keep_copy(p, i) if p else "" for i, (m, p) in enumerate(originals.items())}
            state = {"monitors": originals, "position": backend.position(), "ours": []}
        state["ours"] = (state["ours"] + [path])[-4:]
        self._write_state(state)
        backend.set_position(FILL)
        for monitor in state["monitors"]:
            backend.set(monitor, path)

    def _restore(self, backend) -> None:
        state = self._read_state()
        if state is None:
            return
        ours = {p.casefold() for p in state.get("ours", [])}
        cleared = False
        for monitor, original in state["monitors"].items():
            now = backend.get(monitor)
            if now and now.casefold() not in ours:
                continue  # you picked a new wallpaper while focusing: that one stays
            if original and Path(original).is_file():
                backend.set(monitor, original)
            elif not original and not cleared:
                backend.clear()  # it was a solid color
                cleared = True
        backend.set_position(int(state.get("position", FILL)))
        self.state_file.unlink(missing_ok=True)
