"""Gaming mode: when a game is in front, the timer turns into a readout of what matters while you play.

No timer, no pomodoros: FPS, GPU and CPU use and temperatures, and memory, on the island's pill or the
orb's face, with more in the panel's Gaming tab. It switches on by itself when a game has been in front for
a few seconds (see gamedetect.is_game), but never during a focus session (the distraction check-in and app
lock look after that), and off again once you've been away from the game for a little while.

The readings come from telemetry.TelemetryWorker, on its own thread. Everything stays on this PC.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter

from .engine import Mode, State

ON_AFTER = 2    # foreground checks (about 2 s apart) a game must be in front before gaming mode starts
OFF_AFTER = 5   # ...and away before it stops: an alt-tab to check a guide doesn't end it
HISTORY = 60    # seconds of readings kept for the Gaming tab's little charts


@dataclass(frozen=True)
class Stat:
    value: str
    unit: str
    hot: bool = False  # a temperature worth a glance


def temp_hot(value: float | None) -> bool:
    return value is not None and value >= 80


def fmt_pct(value: float | None) -> str:
    return "" if value is None else f"{round(value)}%"


def fmt_temp(value: float | None) -> str:
    return "" if value is None else f"{round(value)}°"


def hud_stats(reading) -> list[tuple[str, list[Stat]]]:
    """The pill's groups, in order: FPS, GPU, CPU (each only when there's something to show)."""
    if reading is None:
        return []
    out = []
    if reading.fps is not None:
        out.append(("FPS", [Stat(str(round(reading.fps)), "")]))
    for name, use, temp in (("GPU", reading.gpu, reading.gpu_temp), ("CPU", reading.cpu, reading.cpu_temp)):
        stats = [Stat(fmt_pct(use), "")] if use is not None else []
        if temp is not None:
            stats.append(Stat(fmt_temp(temp), "", temp_hot(temp)))
        if stats:
            out.append((name, stats))
    if len(out) < 3 and reading.ram is not None:
        out.append(("RAM", [Stat(fmt_pct(reading.ram), "")]))
    return out


def game_name(fg) -> str:
    """A friendly name for the game: its window title, else its exe without .exe."""
    if fg is None:
        return ""
    title = (fg.title or "").strip()
    if title and len(title) <= 40:
        return title
    stem = fg.exe[:-4] if fg.exe.lower().endswith(".exe") else fg.exe
    return stem[:1].upper() + stem[1:]


# ------------------------------------------------------------------ painting, shared by the island and the orb
def _font(family: str, px: float, weight=QFont.Normal) -> QFont:
    font = QFont(family)
    font.setPixelSize(max(1, round(px)))
    font.setWeight(weight)
    return font


def paint_pill(p: QPainter, rect: QRectF, theme, reading, family: str, left: float) -> None:
    """The island's pill in gaming mode: groups like `144 FPS   GPU 87% 71°   CPU 42% 68°`."""
    groups = hud_stats(reading)
    if not groups:
        p.setFont(_font(family, 12))
        p.setPen(QColor(theme.muted))
        p.drawText(rect.adjusted(left, 0, -14, 0), Qt.AlignVCenter | Qt.AlignLeft, "Gaming mode")
        return
    value_font, unit_font = _font(family, 14, QFont.DemiBold), _font(family, 10.5, QFont.DemiBold)
    vfm, ufm = QFontMetricsF(value_font), QFontMetricsF(unit_font)
    x = rect.left() + left
    y = rect.center().y()
    gap = 12.0
    for name, stats in groups:
        if name == "FPS":
            text = stats[0].value
            p.setFont(value_font)
            p.setPen(QColor(theme.ink))
            p.drawText(QPointF(x, y + vfm.ascent() / 2 - 1), text)
            x += vfm.horizontalAdvance(text) + 3
            p.setFont(unit_font)
            p.setPen(QColor(theme.muted))
            p.drawText(QPointF(x, y + ufm.ascent() / 2 - 1), "FPS")
            x += ufm.horizontalAdvance("FPS") + gap
            continue
        p.setFont(unit_font)
        p.setPen(QColor(theme.muted))
        p.drawText(QPointF(x, y + ufm.ascent() / 2 - 1), name)
        x += ufm.horizontalAdvance(name) + 4
        for stat in stats:
            p.setFont(value_font)
            p.setPen(QColor(theme.danger if stat.hot else theme.ink))
            p.drawText(QPointF(x, y + vfm.ascent() / 2 - 1), stat.value)
            x += vfm.horizontalAdvance(stat.value) + 5
        x += gap - 5
        if x > rect.right() - 20:
            break


def paint_orb_face(p: QPainter, c: QPointF, k: float, theme, reading, family: str, accent: QColor,
                   label_opacity: float = 1.0) -> None:
    """The orb's face in gaming mode: FPS big in the middle (or GPU use without it), and the rest below.

    The GAMING label fades out (label_opacity) while the orb's buttons sit where it is.
    """
    base = p.opacity()
    if label_opacity > 0.01:
        p.setOpacity(label_opacity)
        p.setFont(_font(family, 10 * k, QFont.DemiBold))
        p.setPen(QColor(theme.ink_2))
        p.drawText(QRectF(c.x() - 60 * k, c.y() - 50 * k, 120 * k, 16 * k), Qt.AlignCenter, "GAMING")
        p.setOpacity(base)
    if reading is None:
        p.setFont(_font(family, 13 * k))
        p.setPen(QColor(theme.muted))
        p.drawText(QRectF(c.x() - 60 * k, c.y() - 12 * k, 120 * k, 24 * k), Qt.AlignCenter, "Reading…")
        return
    if reading.fps is not None:
        big, unit = str(round(reading.fps)), "FPS"
    elif reading.gpu is not None:
        big, unit = fmt_pct(reading.gpu), "GPU"
    else:
        big, unit = fmt_pct(reading.cpu), "CPU"
    p.setFont(_font(family, 40 * k, QFont.DemiBold))
    p.setPen(QColor(theme.ink))
    p.drawText(QRectF(c.x() - 70 * k, c.y() - 34 * k, 140 * k, 46 * k), Qt.AlignCenter, big)
    p.setFont(_font(family, 10 * k, QFont.DemiBold))
    p.setPen(QColor(theme.muted))
    p.drawText(QRectF(c.x() - 60 * k, c.y() + 8 * k, 120 * k, 14 * k), Qt.AlignCenter, unit)
    lines = []
    for name, use, temp in (("GPU", reading.gpu, reading.gpu_temp), ("CPU", reading.cpu, reading.cpu_temp)):
        if name == unit and temp is None:
            continue
        bits = [b for b in (fmt_pct(use) if name != unit else "", fmt_temp(temp)) if b]
        if bits:
            lines.append((f"{name}  " + "  ".join(bits), temp_hot(temp)))
    if reading.ram is not None and len(lines) < 2:
        lines.append((f"RAM  {fmt_pct(reading.ram)}", False))
    p.setFont(_font(family, 11 * k))
    for i, (text, hot) in enumerate(lines[:2]):
        p.setPen(QColor(theme.danger if hot else theme.ink_2))
        p.drawText(QRectF(c.x() - 66 * k, c.y() + (25 + 15 * i) * k, 132 * k, 15 * k), Qt.AlignCenter, text)


def ring_fraction(reading) -> float:
    """What the orb's ring shows in gaming mode: how busy the GPU is (or the CPU, without a GPU reading)."""
    if reading is None:
        return 0.0
    value = reading.gpu if reading.gpu is not None else reading.cpu
    return max(0.0, min(1.0, (value or 0.0) / 100))


# ------------------------------------------------------------------ the mode
class GamingMode(QObject):
    """Watches what's in front, decides when you're gaming, and passes the telemetry on."""

    changed = Signal(bool)    # gaming mode started (True) or ended (False)
    updated = Signal(object)  # a new telemetry Reading

    def __init__(self, ctrl, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.active = False
        self.game = None           # the Foreground of the game, while gaming
        self.reading = None
        self.since = 0.0           # when gaming mode started (monotonic)
        self.forced: bool | None = None  # you turned it on (True) or off (False) by hand
        self._yes = self._no = 0
        self._watchers = 0         # the Gaming tab, open: sample even outside gaming mode
        self.history: dict[str, deque] = {key: deque(maxlen=HISTORY) for key in ("fps", "gpu", "cpu", "ram")}
        self._libraries: list[str] | None = None
        self.worker = None

    # ------------------------------------------------------------- the worker
    def start(self) -> None:
        try:
            from .telemetry import TelemetryWorker
        except ImportError:
            return
        self.worker = TelemetryWorker()
        self.worker.foreground.connect(self._on_foreground)
        self.worker.reading.connect(self._on_reading)
        self.worker.start()
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.stop)

    def stop(self) -> None:
        if self.worker is not None:
            self.worker.stop()
            self.worker = None

    def _sync_worker(self) -> None:
        if self.worker is None:
            return
        self.worker.set_target(self.game.pid if self.active and self.game is not None else None)
        self.worker.set_sampling(self.active or self._watchers > 0)

    def watch(self, on: bool) -> None:
        """The Gaming tab shows live readings whenever it's open, gaming or not."""
        self._watchers = max(0, self._watchers + (1 if on else -1))
        self._sync_worker()

    # ------------------------------------------------------------- deciding
    def libraries(self) -> list[str]:
        if self._libraries is None:
            try:
                from .gamedetect import game_library_dirs

                self._libraries = game_library_dirs()
            except Exception:
                self._libraries = []
        return self._libraries

    def _focusing(self) -> bool:
        e = self.ctrl.engine
        return e.mode is Mode.FOCUS and e.state is State.RUNNING

    def _is_game(self, fg) -> bool:
        if fg is None:
            return False
        from .gamedetect import is_game

        s = self.ctrl.settings
        return is_game(fg, s.gaming_games, s.gaming_not_games, self.libraries())

    def _on_foreground(self, fg) -> None:
        game = self._is_game(fg)
        if self.forced is False:
            if not game:
                self.forced = None  # you left that game: noticing games again
            return
        if self.forced is True:
            if game:
                self.game = fg
                self._sync_worker()
            return
        wanted = game and self.ctrl.settings.gaming_on and not self._focusing()
        if wanted:
            self._yes, self._no = self._yes + 1, 0
            if not self.active and self._yes >= ON_AFTER:
                self._set(True, fg)
            elif self.active and fg is not None and self.game is not None and fg.pid != self.game.pid:
                self.game = fg  # another game now
                self._sync_worker()
        else:
            self._no, self._yes = self._no + 1, 0
            if self.active and (self._no >= OFF_AFTER or self._focusing()):
                self._set(False, None)

    def _set(self, on: bool, fg) -> None:
        if on == self.active and (fg is None or self.game is fg):
            return
        self.active = on
        self.game = fg if on else None
        self.since = time.monotonic() if on else 0.0
        if not on:
            self.reading = None if self._watchers == 0 else self.reading
            for h in self.history.values():
                h.clear()
        self._sync_worker()
        self.changed.emit(on)

    def _on_reading(self, reading) -> None:
        self.reading = reading
        for key in self.history:
            value = getattr(reading, key, None)
            if value is not None:
                self.history[key].append(value)
        self.updated.emit(reading)

    # ------------------------------------------------------------- by hand
    def toggle(self) -> None:
        """Turn gaming mode on now (even without a game in front), or off until you leave this game."""
        if self.active:
            self.forced = False
            self._set(False, None)
        else:
            self.forced = True
            self._set(True, self.game)

    def not_a_game(self) -> None:
        """What's in front isn't a game: remember that, and leave gaming mode."""
        if self.game is not None:
            exe = self.game.exe.lower()
            s = self.ctrl.settings
            s.gaming_games = [g for g in s.gaming_games if g != exe]
            if exe not in s.gaming_not_games:
                s.gaming_not_games.append(exe)
            self.ctrl.save_settings()
        self.forced = None
        self._set(False, None)

    def always_a_game(self) -> None:
        if self.game is not None:
            exe = self.game.exe.lower()
            s = self.ctrl.settings
            s.gaming_not_games = [g for g in s.gaming_not_games if g != exe]
            if exe not in s.gaming_games:
                s.gaming_games.append(exe)
            self.ctrl.save_settings()
