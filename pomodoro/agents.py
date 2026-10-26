"""Live view of coding-agent sessions (Claude Code, Codex CLI, Gemini CLI), fed by Pomo's hook client (hook.py).

Each hook run connects to a local named pipe and sends one JSON line. Some keep
the connection open until the user answers in Pomo (or the wait runs out, and
the agent carries on in the terminal as usual):
  permission  a permission prompt: allow, deny (with a note for the agent), or in terminal
  question    Claude's multiple-choice question (AskUserQuestion): pick an answer
  reply       the end of a turn: the agent's own words, and a box to reply

When a turn ends after the agent changed files, review_ready offers them as focus work for you.
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
from .presence import user_at_terminal
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
AGENT_LABELS = {"claude": "Claude", "codex": "Codex", "gemini": "Gemini"}
EDIT_TOOLS = ("Edit", "MultiEdit", "Write", "NotebookEdit")


def activity_text(tool: str, detail: str) -> str:
    verb = VERBS.get(tool, f"Using {tool}" if tool else "Working")
    return f"{verb} {detail}".strip() if detail and tool != "TodoWrite" else verb


def project_name(cwd: str, agent: str = "claude") -> str:
    name = PureWindowsPath(cwd).name if cwd else ""
    return name or {"codex": "Codex", "gemini": "Gemini CLI"}.get(agent, "Claude Code")


@dataclass
class AgentSession:
    id: str
    project: str
    cwd: str
    agent: str = "claude"   # claude | codex | gemini
    status: str = "idle"
    activity: str = ""
    started: datetime = field(default_factory=datetime.now)
    last_event: datetime = field(default_factory=datetime.now)
    turn_started: datetime | None = None
    message: str = ""  # Claude's latest words to you (a notification or the end of a turn), as written
    tools: int = 0
    edits: int = 0
    commands: int = 0
    turn_files: dict = field(default_factory=dict)  # files changed since the last finished turn: path -> [added, removed]

    @property
    def status_text(self) -> str:
        return STATUS_TEXT.get(self.status, self.status)

    @property
    def agent_label(self) -> str:
        return AGENT_LABELS.get(self.agent, "Claude")


def review_of(session: AgentSession) -> dict:
    """The files a finished turn changed, as an item for the review queue (see reviews.py)."""
    files = sorted(session.turn_files.items(), key=lambda kv: -(kv[1][0] + kv[1][1]))
    return {"project": session.project, "agent": session.agent, "cwd": session.cwd,
            "files": [path for path, _ in files][:30],
            "added": sum(a for _, (a, _r) in files), "removed": sum(r for _, (_a, r) in files),
            "at": datetime.now().replace(microsecond=0).isoformat()}


@dataclass
class Approval:
    id: int
    session: AgentSession
    tool: str
    detail: str
    created: float
    socket: QLocalSocket | None = None
    timer: QTimer | None = None
    kind: str = "permission"  # permission | question | reply
    questions: list = field(default_factory=list)
    message: str = ""         # for a reply: what Claude said at the end of its turn


class AgentHub(QObject):
    changed = Signal()
    approval_requested = Signal(object)    # Approval
    approval_closed = Signal(object)       # Approval resolved or cancelled
    turn_finished = Signal(object, float)  # AgentSession, seconds the turn took
    needs_input = Signal(object, str)      # AgentSession, notification kind
    review_ready = Signal(object)          # a turn changed files: {"project", "agent", "files", "added", ...}

    def __init__(self, ctrl, parent: QObject | None = None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.sessions: dict[str, AgentSession] = {}
        self.pending: dict[int, Approval] = {}
        self.user_at_terminal = user_at_terminal  # replaced in tests
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
        if any(a.kind != "reply" for a in self.pending.values()) or any(
                s.status in ("approval", "waiting") for s in self.sessions.values()):
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
            agent = str(msg.get("agent") or "claude")
            session = self.sessions[sid] = AgentSession(sid, project_name(cwd, agent), cwd,
                                                        agent if agent in AGENT_LABELS else "claude")
        session.last_event = now

        if event == "SessionStart":
            session.status, session.activity = "idle", "Session started"
            self._record(session, event, detail=kind)
        elif event == "UserPromptSubmit":
            session.status, session.activity, session.turn_started = "working", "Thinking", now
            self._record(session, event, detail=detail)
        elif event == "PreToolUse" and msg.get("kind") == "question" and msg.get("wait"):
            self._open_question(session, msg.get("questions") or [], sock)
        elif event == "PreToolUse":
            if session.status not in ("approval",):
                session.status = "working"
            session.activity = activity_text(tool, detail)
            session.turn_started = session.turn_started or now
        elif event == "PostToolUse":
            session.tools += 1
            session.edits += tool in EDIT_TOOLS
            session.commands += tool == "Bash"
            if session.status == "approval":
                session.status = "working"
            files = self._changed_files(msg, tool, detail)
            if not files:
                self._record(session, event, tool, detail, int(msg.get("added") or 0), int(msg.get("removed") or 0))
            for path, added, removed in files:  # a patch can touch several files: one event each
                self._record(session, event, tool, path, added, removed)
                tally = session.turn_files.setdefault(path, [0, 0])
                tally[0] += added
                tally[1] += removed
        elif event == "PermissionRequest":
            session.status = "approval"
            session.activity = activity_text(tool, detail)
            self._open_approval(session, tool, detail, sock)
        elif event == "Notification":
            session.message = str(msg.get("message") or detail)
            if kind == "permission_prompt":
                session.status = "approval"
            elif kind in ("idle_prompt", "agent_needs_input", "elicitation_dialog"):
                session.status, session.activity = "waiting", detail or "Waiting for you"
                self.needs_input.emit(session, kind)
        elif event == "Stop":
            seconds = (now - session.turn_started).total_seconds() if session.turn_started else 0.0
            session.status, session.activity, session.turn_started = "done", "Finished", None
            session.message = str(msg.get("message") or detail)
            self._record(session, event, detail=detail)
            if msg.get("wait"):
                self._open_reply(session, session.message, sock)
            if session.turn_files:  # queued first, so the finish notice can offer it
                review, session.turn_files = review_of(session), {}
                if self.ctrl.settings.agent_reviews:
                    self.review_ready.emit(review)
            self.turn_finished.emit(session, seconds)
        elif event == "SessionEnd":
            self.sessions.pop(sid, None)
        self.changed.emit()

    @staticmethod
    def _changed_files(msg: dict, tool: str, detail: str) -> list[tuple[str, int, int]]:
        """(path, lines added, lines removed) for each file a tool call changed."""
        files = msg.get("files")
        if isinstance(files, list):
            out = []
            for item in files:
                if isinstance(item, list) and len(item) == 3 and isinstance(item[0], str) and item[0]:
                    out.append((item[0], int(item[1] or 0), int(item[2] or 0)))
            return out
        if tool in EDIT_TOOLS and detail:  # an older hook client: one file, in the detail
            return [(detail, int(msg.get("added") or 0), int(msg.get("removed") or 0))]
        return []

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

    def _open_question(self, session: AgentSession, questions: list, sock: QLocalSocket | None) -> None:
        """Claude asks multiple-choice questions: answer in Pomo, unless you're at the terminal anyway."""
        s = self.ctrl.settings
        if sock is None or not questions or not s.agent_questions or self.user_at_terminal():
            self._reply(sock, "pass")  # the question shows in the terminal right away
            return
        session.status, session.activity = "waiting", "Asking you a question"
        approval = Approval(next(self._ids), session, "AskUserQuestion", str(questions[0].get("question", "")),
                            time.monotonic(), sock, kind="question", questions=questions)
        self._wait(approval, s.approval_wait_s)

    def _open_reply(self, session: AgentSession, message: str, sock: QLocalSocket | None) -> None:
        """A turn ended: offer a reply in Pomo while you're away from the terminal (Claude waits a little)."""
        s = self.ctrl.settings
        pacer = getattr(self.ctrl, "pacer", None)
        holding = pacer is not None and pacer.should_hold()  # quiet focus: no waiting, the news waits for the break
        if sock is None or not s.agent_reply or holding or self.user_at_terminal():
            self._reply(sock, "pass")
            return
        approval = Approval(next(self._ids), session, "", message[:300], time.monotonic(), sock,
                            kind="reply", message=message)
        self._wait(approval, s.agent_reply_wait_s)

    def _wait(self, approval: Approval, seconds: int) -> None:
        approval.timer = QTimer(self)
        approval.timer.setSingleShot(True)
        approval.timer.timeout.connect(lambda a=approval: self.resolve(a.id, "timeout"))
        approval.timer.start(int(seconds) * 1000)
        self.pending[approval.id] = approval
        self.approval_requested.emit(approval)
        self.changed.emit()

    def awaiting_reply(self, session: AgentSession) -> bool:
        return any(a.kind == "reply" and a.session is session for a in self.pending.values())

    def resolve(self, approval_id: int, decision: str, payload=None) -> None:
        """decision: permission 'allow' / 'deny' (payload: a note for Claude); question 'answer' (payload:
        one answer per question); reply 'reply' (payload: your message); or 'terminal' / 'timeout' / 'pass'."""
        approval = self.pending.get(approval_id)
        if approval is None:
            return
        self._close_approval(approval, decision, reply=True, payload=payload)

    def _close_approval(self, approval: Approval, decision: str, reply: bool, payload=None) -> None:
        self.pending.pop(approval.id, None)
        if approval.timer is not None:
            approval.timer.stop()
        session = approval.session
        if approval.kind == "question":
            answered = decision == "answer" and isinstance(payload, list) and any(payload)
            if reply:
                self._reply(approval.socket, "answer" if answered else "pass", answers=payload if answered else None)
            session.status, session.activity = ("working", "Got your answer") if answered else (session.status, session.activity)
            self._record(session, "Question", approval.tool, "answered" if answered else decision)
        elif approval.kind == "reply":
            text = str(payload or "").strip() if decision == "reply" else ""
            if reply:
                self._reply(approval.socket, "reply" if text else "pass", message=text)
            if text:
                session.status, session.activity, session.turn_started = "working", "Reading your reply", datetime.now()
            self._record(session, "Reply", "", "replied" if text else decision)
        else:
            if reply:
                note = str(payload or "").strip() if decision == "deny" else ""
                self._reply(approval.socket, decision if decision in ("allow", "deny") else "pass", message=note)
            if decision in ("allow", "deny"):
                session.status = "working"
            self._record(session, "Approval", approval.tool, decision)
        self.approval_closed.emit(approval)
        self.changed.emit()

    @staticmethod
    def _reply(sock: QLocalSocket | None, decision: str, message: str = "", answers: list | None = None) -> None:
        try:
            if sock is None or sock.state() != QLocalSocket.ConnectedState:
                return
        except RuntimeError:  # already destroyed
            return
        reply: dict = {"decision": decision}
        if decision == "deny":
            reply["message"] = message or "The user denied this in Pomo."
        elif decision == "reply":
            reply["message"] = message
        elif decision == "answer":
            reply["answers"] = answers or []
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
            self._close_approval(approval, "terminal", reply=True)  # Claude Code carries on in the terminal
        self.server.close()
