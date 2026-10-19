"""Session history and coding-agent activity in SQLite."""
from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from PySide6.QtCore import QObject, Signal

MODE_LABELS = {"focus": "Focus", "short": "Short break", "long": "Long break"}


@dataclass(frozen=True)
class SessionRecord:
    started_at: datetime
    ended_at: datetime
    mode: str  # "focus" | "short" | "long"
    planned_sec: int
    actual_sec: int
    completed: bool
    task: str = ""

    @property
    def day(self) -> date:
        return self.started_at.date()


@dataclass(frozen=True)
class AgentEvent:
    """One recorded coding-agent event: a session start, a prompt, a tool call, a finished turn or an approval."""
    ts: datetime
    session: str
    project: str
    event: str   # SessionStart | UserPromptSubmit | PostToolUse | Stop | Approval
    tool: str = ""
    detail: str = ""
    added: int = 0
    removed: int = 0

    @property
    def day(self) -> date:
        return self.ts.date()


class Storage(QObject):
    changed = Signal()         # pomodoro history
    agents_changed = Signal()  # agent activity (frequent, so kept separate)

    def __init__(self, path: Path, parent: QObject | None = None):
        super().__init__(parent)
        self._db = sqlite3.connect(str(path))
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                ended_at TEXT NOT NULL,
                day TEXT NOT NULL,
                mode TEXT NOT NULL,
                planned_sec INTEGER NOT NULL,
                actual_sec INTEGER NOT NULL,
                completed INTEGER NOT NULL,
                task TEXT NOT NULL DEFAULT ''
            )"""
        )
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_sessions_day ON sessions(day)")
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS agent_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                day TEXT NOT NULL,
                session TEXT NOT NULL,
                project TEXT NOT NULL,
                event TEXT NOT NULL,
                tool TEXT NOT NULL DEFAULT '',
                detail TEXT NOT NULL DEFAULT '',
                added INTEGER NOT NULL DEFAULT 0,
                removed INTEGER NOT NULL DEFAULT 0
            )"""
        )
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_agent_events_day ON agent_events(day)")
        self._db.commit()

    def add(self, rec: SessionRecord) -> None:
        self.add_many([rec])

    def add_many(self, records: list[SessionRecord]) -> None:
        self._db.executemany(
            "INSERT INTO sessions (started_at, ended_at, day, mode, planned_sec, actual_sec, completed, task)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    r.started_at.isoformat(timespec="seconds"),
                    r.ended_at.isoformat(timespec="seconds"),
                    r.day.isoformat(),
                    r.mode,
                    int(r.planned_sec),
                    int(r.actual_sec),
                    int(bool(r.completed)),
                    r.task,
                )
                for r in records
            ],
        )
        self._db.commit()
        self.changed.emit()

    def sessions(self, since: date | None = None) -> list[SessionRecord]:
        sql = "SELECT started_at, ended_at, mode, planned_sec, actual_sec, completed, task FROM sessions"
        args: tuple = ()
        if since is not None:
            sql += " WHERE day >= ?"
            args = (since.isoformat(),)
        sql += " ORDER BY started_at"
        return [
            SessionRecord(
                started_at=datetime.fromisoformat(row[0]),
                ended_at=datetime.fromisoformat(row[1]),
                mode=row[2],
                planned_sec=row[3],
                actual_sec=row[4],
                completed=bool(row[5]),
                task=row[6] or "",
            )
            for row in self._db.execute(sql, args)
        ]

    def add_agent_event(self, ev: AgentEvent) -> None:
        self._db.execute(
            "INSERT INTO agent_events (ts, day, session, project, event, tool, detail, added, removed)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ev.ts.isoformat(timespec="seconds"), ev.day.isoformat(), ev.session, ev.project,
             ev.event, ev.tool, ev.detail, int(ev.added), int(ev.removed)),
        )
        self._db.commit()
        self.agents_changed.emit()

    def agent_events(self, since: date | None = None) -> list[AgentEvent]:
        sql = "SELECT ts, session, project, event, tool, detail, added, removed FROM agent_events"
        args: tuple = ()
        if since is not None:
            sql += " WHERE day >= ?"
            args = (since.isoformat(),)
        sql += " ORDER BY ts"
        return [AgentEvent(datetime.fromisoformat(r[0]), r[1], r[2], r[3], r[4] or "", r[5] or "", r[6], r[7])
                for r in self._db.execute(sql, args)]

    def prune_agent_events(self, keep_days: int = 90) -> None:
        cutoff = date.fromordinal(date.today().toordinal() - keep_days).isoformat()
        self._db.execute("DELETE FROM agent_events WHERE day < ?", (cutoff,))
        self._db.commit()

    def count(self) -> int:
        return self._db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]

    def completed_focus_count(self, day: date) -> int:
        row = self._db.execute(
            "SELECT COUNT(*) FROM sessions WHERE day = ? AND mode = 'focus' AND completed = 1",
            (day.isoformat(),),
        ).fetchone()
        return row[0]

    def clear(self) -> None:
        self._db.execute("DELETE FROM sessions")
        self._db.execute("DELETE FROM agent_events")
        self._db.commit()
        self.changed.emit()
        self.agents_changed.emit()

    def export_csv(self, path: Path) -> int:
        records = self.sessions()
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["date", "start", "end", "type", "task", "planned_min", "actual_min", "result"])
            for r in records:
                writer.writerow([
                    r.day.isoformat(),
                    r.started_at.strftime("%H:%M:%S"),
                    r.ended_at.strftime("%H:%M:%S"),
                    MODE_LABELS.get(r.mode, r.mode),
                    r.task,
                    round(r.planned_sec / 60, 2),
                    round(r.actual_sec / 60, 2),
                    "completed" if r.completed else "stopped early",
                ])
        return len(records)

    def close(self) -> None:
        self._db.close()
