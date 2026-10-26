"""Agent-paced pomodoros: your focus sessions and your coding agents, in step.

- When an agent takes on a long task and you're well into a focus session, Pomo suggests
  taking your break now, while it works.
- When an agent takes on a long task and your timer is idle, Pomo suggests a focus session.
- During focus, agent updates that can wait (finished, waiting for you) are held and shown
  together when your break starts. Permission requests still come through right away.
"""
from __future__ import annotations

import time
from datetime import datetime

from PySide6.QtCore import QObject, QTimer, Signal

from .engine import Mode, State
from .stats import fmt_clock

CHECK_MS = 15_000
BREAK_HINT_AT = 0.6          # into the focus session before suggesting an early break
FOCUS_HINT_EVERY_S = 30 * 60


class AgentPacer(QObject):
    suggest_break = Signal(object)  # AgentSession busy with a long task while you're deep into focus
    suggest_focus = Signal(object)  # AgentSession busy with a long task while your timer is idle

    def __init__(self, ctrl, clock=time.monotonic, now=datetime.now, parent: QObject | None = None):
        super().__init__(parent)
        self.ctrl = ctrl
        self._clock = clock
        self._now = now
        self.held: list[tuple[str, str, str]] = []  # (kind, project, detail)
        self._hinted: set[tuple[str, datetime]] = set()
        self._last_focus_hint = -1e9
        self._timer = QTimer(self)
        self._timer.setInterval(CHECK_MS)
        self._timer.timeout.connect(self.check)
        self._timer.start()

    # ------------------------------------------------------------- suggestions
    def check(self) -> None:
        s, e = self.ctrl.settings, self.ctrl.engine
        if not s.agent_break_hint:
            return
        now = self._now()
        for agent in list(self.ctrl.agents.sessions.values()):
            if agent.status != "working" or agent.turn_started is None:
                continue
            key = (agent.id, agent.turn_started)
            if key in self._hinted or (now - agent.turn_started).total_seconds() < s.agent_break_after_s:
                continue
            if e.mode is Mode.FOCUS and e.state is State.RUNNING:
                if e.total > 0 and e.elapsed / e.total >= BREAK_HINT_AT:
                    self._hinted.add(key)
                    self.suggest_break.emit(agent)
                    return
            elif e.mode is Mode.FOCUS and e.state in (State.IDLE, State.FINISHED):
                if self._clock() - self._last_focus_hint >= FOCUS_HINT_EVERY_S:
                    self._hinted.add(key)
                    self._last_focus_hint = self._clock()
                    self.suggest_focus.emit(agent)
                    return

    # ----------------------------------------------------------- quiet focus
    def should_hold(self) -> bool:
        e = self.ctrl.engine
        return self.ctrl.settings.quiet_focus and e.mode is Mode.FOCUS and e.state is State.RUNNING

    def hold(self, kind: str, project: str, detail: str = "") -> None:
        self.held.append((kind, project, detail))

    def take_held(self) -> list[tuple[str, str, str]]:
        held, self.held = self.held, []
        return held

    @staticmethod
    def summary(held: list[tuple[str, str, str]]) -> str:
        """'While you focused: api finished (4:12), pomo is waiting for you.'"""
        if not held:
            return ""
        latest: dict[str, tuple[str, str]] = {}
        for kind, project, detail in held:
            latest[project] = (kind, detail)  # the newest news per project
        parts = []
        for project, (kind, detail) in latest.items():
            if kind == "finished":
                parts.append(f"{project} finished" + (f" ({detail})" if detail else ""))
            else:
                parts.append(f"{project} is waiting for you")
        return "While you focused: " + ", ".join(parts) + "."


def finished_detail(seconds: float) -> str:
    return fmt_clock(seconds) if seconds >= 60 else ""
