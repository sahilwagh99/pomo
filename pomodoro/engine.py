"""Pomodoro timer state machine.

Time comes from a monotonic clock, so the countdown stays exact even when the UI
thread is busy; the QTimer only decides how often we look at the clock.
"""
from __future__ import annotations

import time
from datetime import datetime
from enum import Enum, auto
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from .storage import SessionRecord

MIN_LOG_SECONDS = 60  # abandoned sessions shorter than this are noise, not history
MAX_SECONDS = 180 * 60
MIN_REMAINING = 5
TICK_MS = 100


class Mode(str, Enum):
    FOCUS = "focus"
    SHORT = "short"
    LONG = "long"

    @property
    def label(self) -> str:
        return _LABELS[self]

    @property
    def is_break(self) -> bool:
        return self is not Mode.FOCUS


_LABELS = {Mode.FOCUS: "Focus", Mode.SHORT: "Short break", Mode.LONG: "Long break"}


class State(Enum):
    IDLE = auto()      # loaded, not started
    RUNNING = auto()
    PAUSED = auto()
    FINISHED = auto()  # previous session just ended; next one is loaded and waiting


class TimerEngine(QObject):
    ticked = Signal()
    changed = Signal()                    # mode, state, duration or cycle changed
    session_logged = Signal(object)       # SessionRecord
    finished = Signal(object, object, bool)  # finished Mode, next Mode, auto-started

    def __init__(
        self,
        settings,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = datetime.now,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self.settings = settings
        self._clock = clock
        self._now = now
        self._ticker = QTimer(self)
        self._ticker.setInterval(TICK_MS)
        self._ticker.timeout.connect(self._on_tick)

        self.mode = Mode.FOCUS
        self.state = State.IDLE
        self.cycle_index = 0  # focus sessions completed in the current long-break cycle
        self.task = ""
        self._total = 0.0
        self._remaining = 0.0  # remaining as of _mark (or now, when not running)
        self._mark: float | None = None
        self._banked = 0.0  # running seconds accumulated before _mark
        self._started_at: datetime | None = None
        self._loaded_default = 0
        self._load(Mode.FOCUS)

    # ------------------------------------------------------------------ queries
    def default_duration(self, mode: Mode) -> int:
        minutes = {
            Mode.FOCUS: self.settings.focus_min,
            Mode.SHORT: self.settings.short_min,
            Mode.LONG: self.settings.long_min,
        }[mode]
        return int(minutes) * 60

    @property
    def cycle_length(self) -> int:
        return max(1, int(self.settings.sessions_before_long))

    @property
    def total(self) -> float:
        return self._total

    @property
    def remaining(self) -> float:
        if self._mark is None:
            return self._remaining
        return max(0.0, self._remaining - (self._clock() - self._mark))

    @property
    def elapsed(self) -> float:
        if self._mark is None:
            return self._banked
        return self._banked + (self._clock() - self._mark)

    @property
    def fraction_left(self) -> float:
        if self._total <= 0:
            return 0.0
        return max(0.0, min(1.0, self.remaining / self._total))

    @property
    def is_running(self) -> bool:
        return self.state is State.RUNNING

    def skip_target(self) -> Mode:
        """Mode that skip() would move to."""
        return Mode.SHORT if self.mode is Mode.FOCUS else Mode.FOCUS

    # ----------------------------------------------------------------- commands
    def start(self) -> None:
        if self.state is State.RUNNING:
            return
        if self._started_at is None:
            self._started_at = self._now()
        self._mark = self._clock()
        self.state = State.RUNNING
        self._ticker.start()
        self.changed.emit()

    def pause(self) -> None:
        if self.state is not State.RUNNING:
            return
        self._bank()
        self._ticker.stop()
        self.state = State.PAUSED
        self.changed.emit()

    def toggle(self) -> None:
        if self.state is State.RUNNING:
            self.pause()
        else:
            self.start()

    def reset(self) -> None:
        self._log_partial()
        self._load(self.mode)
        self.changed.emit()

    def skip(self) -> None:
        self._log_partial()
        target = self.skip_target()
        if self.mode is Mode.LONG:
            self.cycle_index = 0
        self._load(target)
        self.changed.emit()

    def switch_mode(self, mode: Mode) -> None:
        self._log_partial()
        if self.mode is Mode.LONG and mode is not Mode.LONG:
            self.cycle_index = 0
        self._load(mode)
        self.changed.emit()

    def set_duration(self, seconds: float) -> None:
        """Change the length of a session that has not started yet."""
        if self.state not in (State.IDLE, State.FINISHED):
            self.set_remaining(seconds)
            return
        seconds = float(max(MIN_REMAINING, min(MAX_SECONDS, seconds)))
        self._total = self._remaining = seconds
        self.changed.emit()

    def set_remaining(self, seconds: float) -> None:
        """Change how much time is left in a running or paused session."""
        if self.state in (State.IDLE, State.FINISHED):
            self.set_duration(seconds)
            return
        seconds = float(max(MIN_REMAINING, min(MAX_SECONDS, seconds)))
        if self._mark is not None:
            self._bank()
            self._mark = self._clock()
        self._remaining = seconds
        self._total = self._banked + seconds
        self.changed.emit()

    def discount(self, seconds: float) -> float:
        """Take time back out of the session, as if it had been paused then (e.g. while you were away).

        The time goes back onto the clock and doesn't count as focus. Returns the seconds taken back.
        """
        if self.state not in (State.RUNNING, State.PAUSED):
            return 0.0
        running = self._mark is not None
        self._bank()
        taken = max(0.0, min(float(seconds), self._banked))
        self._banked -= taken
        self._remaining += taken
        if running:
            self._mark = self._clock()
        self.changed.emit()
        return taken

    def credit(self, seconds: float) -> float:
        """Count time as focus after all (the opposite of discount); finishes the session if that fills it."""
        if self.state not in (State.RUNNING, State.PAUSED):
            return 0.0
        running = self._mark is not None
        self._bank()
        given = max(0.0, min(float(seconds), self._remaining))
        self._remaining -= given
        self._banked += given
        if running:
            self._mark = self._clock()
        if self._remaining <= 0:
            self._complete()
        else:
            self.changed.emit()
        return given

    def finish_now(self) -> None:
        """End the session now and count it as done (say, to take your break while an agent works)."""
        if self.state in (State.RUNNING, State.PAUSED):
            self._complete()

    def adjust(self, delta: float) -> None:
        if self.state in (State.IDLE, State.FINISHED):
            self.set_duration(max(60.0, self._total + delta))
        else:
            self.set_remaining(self.remaining + delta)

    def apply_settings(self) -> None:
        """Pick up new default durations for a session that has not started."""
        if self.state in (State.IDLE, State.FINISHED):
            default = self.default_duration(self.mode)
            if default != self._loaded_default:
                self._total = self._remaining = float(default)
                self._loaded_default = default
        self.changed.emit()

    def shutdown(self) -> None:
        """Log an in-progress session (as stopped early) before the app exits."""
        self._log_partial()
        self._ticker.stop()

    # ---------------------------------------------------------------- internals
    def _load(self, mode: Mode) -> None:
        self._ticker.stop()
        self.mode = mode
        self.state = State.IDLE
        self._loaded_default = self.default_duration(mode)
        self._total = self._remaining = float(self._loaded_default)
        self._mark = None
        self._banked = 0.0
        self._started_at = None

    def _bank(self) -> None:
        if self._mark is None:
            return
        run = self._clock() - self._mark
        self._remaining = max(0.0, self._remaining - run)
        self._banked += run
        self._mark = None

    def _on_tick(self) -> None:
        if self.state is not State.RUNNING:
            return
        if self.remaining <= 0:
            self._complete()
        else:
            self.ticked.emit()

    def _complete(self) -> None:
        self._bank()
        finished = self.mode
        self._log(completed=True)
        if finished is Mode.FOCUS:
            self.cycle_index += 1
            upcoming = Mode.LONG if self.cycle_index >= self.cycle_length else Mode.SHORT
        else:
            upcoming = Mode.FOCUS
            if finished is Mode.LONG:
                self.cycle_index = 0
        self._load(upcoming)
        auto_start = self.settings.auto_start_breaks if upcoming.is_break else self.settings.auto_start_focus
        if auto_start:
            self.start()
        else:
            self.state = State.FINISHED
            self.changed.emit()
        self.finished.emit(finished, upcoming, bool(auto_start))

    def _log_partial(self) -> None:
        if self.state is State.RUNNING:
            self._bank()
        if self.state in (State.RUNNING, State.PAUSED):
            self._log(completed=False)

    def _log(self, completed: bool) -> None:
        if self._started_at is None:
            return
        actual = int(round(min(self._banked, self._total)))
        if not completed and actual < MIN_LOG_SECONDS:
            return
        self.session_logged.emit(
            SessionRecord(
                started_at=self._started_at.replace(microsecond=0),
                ended_at=self._now().replace(microsecond=0),
                mode=self.mode.value,
                planned_sec=int(round(self._total)),
                actual_sec=actual,
                completed=completed,
                task=self.task.strip() if self.mode is Mode.FOCUS else "",
            )
        )
        self._started_at = None
