"""Live view of Claude Code sessions, fed by FocusOrb's hook client (hook.py).

Each hook run connects to a local named pipe and sends one JSON line. Approval
requests keep the connection open until the user answers (or the wait runs out,
in which case Claude Code falls back to its own prompt in the terminal).
"""
from __future__ import annotations

import itertools
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import PureWindowsPath

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from .hook import PROTOCOL, pipe_name
from .storage import AgentEvent

STALE_AFTER = timedelta(hours=2)
WORKING_GRACE = timedelta(minutes=10)  # a "working" session with no events for this long is shown as quiet

VERBS = {
    "Bash": "Running", "Read": "Reading", "Edit": "Editing", "MultiEdit": "Editing", "Write": "Writing",
    "NotebookEdit": "Editing", "Grep": "Searching", "Glob": "Finding", "WebFetch": "Fetching",
    "WebSearch": "Searching the web for", "Task": "Delegating", "Agent": "Delegating", "TodoWrite": "Planning",
}
STATUS_TEXT = {"working": "working", "approval": "needs your OK", "waiting": "waiting for you",
               "done": "finished", "idle": "ready"}


def activity_text(tool: str, detail: str) -> str:
    verb = VERBS.get(tool, f"Using {tool}" if tool else "Working")
    return f"{verb} {detail}".strip() if detail and tool != "TodoWrite" else verb


def project_name(cwd: str) -> str:
    name = PureWindowsPath(cwd).name if cwd else ""
    return name or "Claude Code"


@dataclass
class AgentSession:
    id: str
    project: str
    cwd: str
    status: str = "idle"
    activity: str = ""
    started: datetime = field(default_factory=datetime.now)
    last_event: datetime = field(default_factory=datetime.now)
    turn_started: datetime | None = None
    tools: int = 0
    edits: int = 0
    commands: int = 0

    @property
    def status_text(self) -> str:
        return STATUS_TEXT.get(self.status, self.status)


@dataclass
class Approval:
    id: int
    session: AgentSession
    tool: str
    detail: str
    created: float
    socket: QLocalSocket | None = None
    timer: QTimer | None = None


class AgentHub(QObject):
    changed = Signal()
    approval_requested = Signal(object)    # Approval
    approval_closed = Signal(object)       # Approval resolved or cancelled
    turn_finished = Signal(object, float)  # AgentSession, seconds the turn took
    needs_input = Signal(object, str)      # AgentSession, notification kind

    def __init__(self, ctrl, parent: QObject | None = None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.sessions: dict[str, AgentSession] = {}
        self.pending: dict[int, Approval] = {}
        self._ids = itertools.count(1)
        self._buffers: dict[QLocalSocket, bytearray] = {}
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.UserAccessOption)  # only this Windows user
        QLocalServer.removeServer(pipe_name())
        self.listening = self.server.listen(pipe_name())
        self.server.newConnection.connect(self._accept)
        self._sweeper = QTimer(self)
        self._sweeper.setInterval(60_000)
        self._sweeper.timeout.connect(self._sweep)
        self._sweeper.start()

    # ---------------------------------------------------------------- queries
    def live_sessions(self) -> list[AgentSession]:
        order = {"approval": 0, "waiting": 1, "working": 2, "done": 3, "idle": 4}
        return sorted(self.sessions.values(), key=lambda s: (order.get(s.status, 9), -s.last_event.timestamp()))

    def signal(self) -> str | None:
        """What Pomo should show: 'alert' (an agent needs you), 'busy' (agents working) or None."""
        now = datetime.now()
        if self.pending or any(s.status in ("approval", "waiting") for s in self.sessions.values()):
            return "alert"
        if any(s.status == "working" and now - s.last_event < WORKING_GRACE for s in self.sessions.values()):
            return "busy"
        return None

    def headline(self) -> str:
        """One short line for the island and tooltips, e.g. 'pomo: Editing app.py'."""
        sessions = self.live_sessions()
        if not sessions:
            return ""
        top = sessions[0]
        text = top.activity if top.status == "working" and top.activity else top.status_text
        more = f" (+{len(sessions) - 1})" if len(sessions) > 1 else ""
        return f"{top.project}: {text}{more}"

    # ------------------------------------------------------------- transport
    def _accept(self) -> None:
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            self._buffers[sock] = bytearray()
            sock.readyRead.connect(lambda s=sock: self._read(s))
            sock.disconnected.connect(lambda s=sock: self._closed(s))

    def _read(self, sock: QLocalSocket) -> None:
        buf = self._buffers.get(sock)
        if buf is None:
            return
        buf += bytes(sock.readAll())
        while b"\n" in buf:
            line, _, rest = bytes(buf).partition(b"\n")
            buf[:] = rest
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if isinstance(msg, dict):
                self.handle(msg, sock)

    def _closed(self, sock: QLocalSocket) -> None:
        try:
            if self._buffers.get(sock):
                self._read(sock)
        except RuntimeError:  # the socket was already destroyed (e.g. while shutting down)
            pass
        self._buffers.pop(sock, None)
        for approval in [a for a in self.pending.values() if a.socket is sock]:
            # Claude Code gave up waiting (hook timeout, interrupted turn): nothing to answer any more
            approval.socket = None
            self._close_approval(approval, "cancelled", reply=False)
        try:
            sock.deleteLater()
        except RuntimeError:
            pass

    # ----------------------------------------------------------------- events
    def handle(self, msg: dict, sock: QLocalSocket | None = None) -> None:
        if msg.get("v") != PROTOCOL:
            return
        event = str(msg.get("event", ""))
        sid = str(msg.get("session") or "unknown")
        tool, detail, kind = str(msg.get("tool", "")), str(msg.get("detail", "")), str(msg.get("kind", ""))
        now = datetime.now()
        session = self.sessions.get(sid)
        if session is None:
            if event == "SessionEnd":
                return
            cwd = str(msg.get("cwd", ""))
            session = self.sessions[sid] = AgentSession(sid, project_name(cwd), cwd)
        session.last_event = now

        if event == "SessionStart":
            session.status, session.activity = "idle", "Session started"
            self._record(session, event, detail=kind)
        elif event == "UserPromptSubmit":
            session.status, session.activity, session.turn_started = "working", "Thinking", now
            self._record(session, event, detail=detail)
        elif event == "PreToolUse":
            if session.status not in ("approval",):
                session.status = "working"
            session.activity = activity_text(tool, detail)
            session.turn_started = session.turn_started or now
        elif event == "PostToolUse":
            session.tools += 1
            session.edits += tool in ("Edit", "MultiEdit", "Write", "NotebookEdit")
            session.commands += tool == "Bash"
            if session.status == "approval":
                session.status = "working"
            self._record(session, event, tool, detail, int(msg.get("added") or 0), int(msg.get("removed") or 0))
        elif event == "PermissionRequest":
            session.status = "approval"
            session.activity = activity_text(tool, detail)
            self._open_approval(session, tool, detail, sock)
        elif event == "Notification":
            if kind == "permission_prompt":
                session.status = "approval"
            elif kind in ("idle_prompt", "agent_needs_input", "elicitation_dialog"):
                session.status, session.activity = "waiting", detail or "Waiting for you"
                self.needs_input.emit(session, kind)
        elif event == "Stop":
            seconds = (now - session.turn_started).total_seconds() if session.turn_started else 0.0
            session.status, session.activity, session.turn_started = "done", "Finished", None
            self._record(session, event, detail=detail)
            self.turn_finished.emit(session, seconds)
        elif event == "SessionEnd":
            self.sessions.pop(sid, None)
        self.changed.emit()

    def _record(self, session: AgentSession, event: str, tool: str = "", detail: str = "",
                added: int = 0, removed: int = 0) -> None:
        self.ctrl.storage.add_agent_event(AgentEvent(datetime.now().replace(microsecond=0), session.id,
                                                     session.project, event, tool, detail, added, removed))

    # -------------------------------------------------------------- approvals
    def _open_approval(self, session: AgentSession, tool: str, detail: str, sock: QLocalSocket | None) -> None:
        approval = Approval(next(self._ids), session, tool, detail, time.monotonic(), sock)
        s = self.ctrl.settings
        if not s.agent_approvals or sock is None:
            # observe only: Claude Code asks in the terminal as usual
            self._reply(sock, "pass")
            self.needs_input.emit(session, "permission_prompt")
            return
        approval.timer = QTimer(self)
        approval.timer.setSingleShot(True)
        approval.timer.timeout.connect(lambda a=approval: self.resolve(a.id, "timeout"))
        approval.timer.start(int(s.approval_wait_s) * 1000)
        self.pending[approval.id] = approval
        self.approval_requested.emit(approval)

    def resolve(self, approval_id: int, decision: str) -> None:
        """decision: 'allow', 'deny', 'terminal' (let Claude Code ask there) or 'timeout'."""
        approval = self.pending.get(approval_id)
        if approval is None:
            return
        self._close_approval(approval, decision, reply=True)

    def _close_approval(self, approval: Approval, decision: str, reply: bool) -> None:
        self.pending.pop(approval.id, None)
        if approval.timer is not None:
            approval.timer.stop()
        if reply:
            self._reply(approval.socket, decision if decision in ("allow", "deny") else "pass")
        session = approval.session
        if decision in ("allow", "deny"):
            session.status = "working"
        self._record(session, "Approval", approval.tool, decision)
        self.approval_closed.emit(approval)
        self.changed.emit()

    @staticmethod
    def _reply(sock: QLocalSocket | None, decision: str) -> None:
        try:
            if sock is None or sock.state() != QLocalSocket.ConnectedState:
                return
        except RuntimeError:  # already destroyed
            return
        reply = {"decision": decision}
        if decision == "deny":
            reply["message"] = "The user denied this in FocusOrb."
        sock.write((json.dumps(reply) + "\n").encode("utf-8"))
        sock.flush()
        sock.disconnectFromServer()

    # ------------------------------------------------------------ housekeeping
    def _sweep(self) -> None:
        cutoff = datetime.now() - STALE_AFTER
        stale = [sid for sid, s in self.sessions.items() if s.last_event < cutoff and s.status != "approval"]
        for sid in stale:
            del self.sessions[sid]
        if stale:
            self.changed.emit()

    def shutdown(self) -> None:
        for approval in list(self.pending.values()):
            self._close_approval(approval, "terminal", reply=True)
        self.server.close()
