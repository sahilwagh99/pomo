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
    rating: int = 0          # how it went, from the "How was that?" card: 1 tough, 2 okay, 3 great; 0 = not rated
    distracted_sec: int = 0  # time spent on distracting apps or sites (when the check-in is on)

    @property
    def day(self) -> date:
        return self.started_at.date()


@dataclass(frozen=True)
class Thought:
    """Something parked during a focus session, for later."""
    id: int
    created: datetime
    text: str
    done: bool = False


@dataclass(frozen=True)
class LogEntry:
    """A few sentences about what you learned that day, in English, and the AI's notes on the English."""
    id: int
    day: date
    created: datetime
    text: str
    feedback: str = ""


@dataclass(frozen=True)
class Card:
    """A review card: a question about something you studied, and its answer."""
    id: int
    topic: str
    question: str
    answer: str
    box: int      # how well you know it: 0 new or missed, up to 4 (see learnloop.INTERVALS)
    due: date


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
        columns = {row[1] for row in self._db.execute("PRAGMA table_info(sessions)")}
        for name in ("rating", "distracted_sec"):  # added in 1.4
            if name not in columns:
                self._db.execute(f"ALTER TABLE sessions ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0")
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS thoughts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created TEXT NOT NULL,
                text TEXT NOT NULL,
                done INTEGER NOT NULL DEFAULT 0
            )"""
        )
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS study_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day TEXT NOT NULL,
                created TEXT NOT NULL,
                text TEXT NOT NULL,
                feedback TEXT NOT NULL DEFAULT ''
            )"""
        )
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created TEXT NOT NULL,
                topic TEXT NOT NULL DEFAULT '',
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                box INTEGER NOT NULL DEFAULT 0,
                due TEXT NOT NULL
            )"""
        )
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS urges (
                ts TEXT NOT NULL,
                rode_out INTEGER NOT NULL DEFAULT 1
            )"""
        )
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS app_time (
                day TEXT NOT NULL,
                app TEXT NOT NULL,
                seconds INTEGER NOT NULL DEFAULT 0,
                distracting INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (day, app)
            )"""
        )
        self._db.commit()

    def add(self, rec: SessionRecord) -> None:
        self.add_many([rec])

    def add_many(self, records: list[SessionRecord]) -> None:
        self._db.executemany(
            "INSERT INTO sessions (started_at, ended_at, day, mode, planned_sec, actual_sec, completed, task,"
            " rating, distracted_sec) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    int(r.rating),
                    int(r.distracted_sec),
                )
                for r in records
            ],
        )
        self._db.commit()
        self.changed.emit()

    def sessions(self, since: date | None = None) -> list[SessionRecord]:
        sql = ("SELECT started_at, ended_at, mode, planned_sec, actual_sec, completed, task, rating, distracted_sec"
               " FROM sessions")
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
                rating=row[7] or 0,
                distracted_sec=row[8] or 0,
            )
            for row in self._db.execute(sql, args)
        ]

    def rate_last_focus(self, rating: int) -> bool:
        """Store how the most recent finished focus session went (1 tough, 2 okay, 3 great)."""
        row = self._db.execute(
            "SELECT id FROM sessions WHERE mode = 'focus' AND completed = 1 ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            return False
        self._db.execute("UPDATE sessions SET rating = ? WHERE id = ?", (max(0, min(3, int(rating))), row[0]))
        self._db.commit()
        self.changed.emit()
        return True

    def lifetime_pomodoros(self) -> int:
        return self._db.execute("SELECT COUNT(*) FROM sessions WHERE mode = 'focus' AND completed = 1").fetchone()[0]

    def lifetime_focus_sec(self) -> float:
        """All the time ever spent in focus, sessions stopped early included."""
        return self._db.execute("SELECT COALESCE(SUM(actual_sec), 0) FROM sessions WHERE mode = 'focus'").fetchone()[0]

    # ------------------------------------------------------------ thoughts
    def add_thought(self, text: str) -> Thought:
        now = datetime.now().replace(microsecond=0)
        cur = self._db.execute("INSERT INTO thoughts (created, text) VALUES (?, ?)",
                               (now.isoformat(timespec="seconds"), text.strip()))
        self._db.commit()
        return Thought(cur.lastrowid, now, text.strip())

    def thoughts(self, include_done: bool = False) -> list[Thought]:
        sql = "SELECT id, created, text, done FROM thoughts"
        if not include_done:
            sql += " WHERE done = 0"
        return [Thought(r[0], datetime.fromisoformat(r[1]), r[2], bool(r[3]))
                for r in self._db.execute(sql + " ORDER BY id")]

    def set_thought_done(self, thought_id: int, done: bool = True) -> None:
        self._db.execute("UPDATE thoughts SET done = ? WHERE id = ?", (int(done), thought_id))
        self._db.commit()

    def clear_done_thoughts(self) -> None:
        self._db.execute("DELETE FROM thoughts WHERE done = 1")
        self._db.commit()

    # ------------------------------------------------------------ study log
    def add_log(self, text: str, day: date | None = None) -> LogEntry:
        now = datetime.now().replace(microsecond=0)
        day = day or now.date()
        cur = self._db.execute("INSERT INTO study_log (day, created, text) VALUES (?, ?, ?)",
                               (day.isoformat(), now.isoformat(timespec="seconds"), text.strip()))
        self._db.commit()
        return LogEntry(cur.lastrowid, day, now, text.strip())

    def logs(self, limit: int = 200) -> list[LogEntry]:
        """Newest first."""
        rows = self._db.execute("SELECT id, day, created, text, feedback FROM study_log ORDER BY id DESC LIMIT ?",
                                (limit,))
        return [LogEntry(r[0], date.fromisoformat(r[1]), datetime.fromisoformat(r[2]), r[3], r[4]) for r in rows]

    def set_log_feedback(self, log_id: int, feedback: str) -> None:
        self._db.execute("UPDATE study_log SET feedback = ? WHERE id = ?", (feedback, log_id))
        self._db.commit()

    def log_days(self) -> set[date]:
        return {date.fromisoformat(r[0]) for r in self._db.execute("SELECT DISTINCT day FROM study_log")}

    # ------------------------------------------------------------ review cards
    def add_card(self, topic: str, question: str, answer: str, due: date) -> Card:
        cur = self._db.execute("INSERT INTO cards (created, topic, question, answer, due) VALUES (?, ?, ?, ?, ?)",
                               (datetime.now().replace(microsecond=0).isoformat(), topic.strip()[:120],
                                question.strip(), answer.strip(), due.isoformat()))
        self._db.commit()
        return Card(cur.lastrowid, topic.strip()[:120], question.strip(), answer.strip(), 0, due)

    def cards(self, due_by: date | None = None) -> list[Card]:
        """All cards, or the ones due by a day, oldest due first."""
        sql = "SELECT id, topic, question, answer, box, due FROM cards"
        args: tuple = ()
        if due_by is not None:
            sql += " WHERE due <= ?"
            args = (due_by.isoformat(),)
        rows = self._db.execute(sql + " ORDER BY due, id", args)
        return [Card(r[0], r[1], r[2], r[3], r[4], date.fromisoformat(r[5])) for r in rows]

    def review_card(self, card_id: int, box: int, due: date) -> None:
        self._db.execute("UPDATE cards SET box = ?, due = ? WHERE id = ?", (box, due.isoformat(), card_id))
        self._db.commit()

    def delete_card(self, card_id: int) -> None:
        self._db.execute("DELETE FROM cards WHERE id = ?", (card_id,))
        self._db.commit()

    # ------------------------------------------------------------ urges
    def add_urge(self, rode_out: bool) -> None:
        self._db.execute("INSERT INTO urges (ts, rode_out) VALUES (?, ?)",
                         (datetime.now().replace(microsecond=0).isoformat(), int(rode_out)))
        self._db.commit()

    def urges(self, since: date) -> tuple[int, int]:
        """(urges ridden out, urges in all) since a day."""
        row = self._db.execute("SELECT COALESCE(SUM(rode_out), 0), COUNT(*) FROM urges WHERE ts >= ?",
                               (since.isoformat(),)).fetchone()
        return int(row[0]), int(row[1])

    # ------------------------------------------------------------ app time
    def add_app_time(self, day: date, app: str, seconds: int, distracting: bool) -> None:
        """Time spent in front of an app or site during focus (names only, never window contents)."""
        self._db.execute(
            "INSERT INTO app_time (day, app, seconds, distracting) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(day, app) DO UPDATE SET seconds = seconds + excluded.seconds,"
            " distracting = excluded.distracting",
            (day.isoformat(), app, int(seconds), int(distracting)))
        self._db.commit()

    def app_time(self, since: date) -> list[tuple[str, int, bool]]:
        """(app, seconds, distracting) summed since a day, most time first."""
        return [(r[0], r[1], bool(r[2])) for r in self._db.execute(
            "SELECT app, SUM(seconds), MAX(distracting) FROM app_time WHERE day >= ? GROUP BY app"
            " ORDER BY SUM(seconds) DESC", (since.isoformat(),))]

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
        self._db.execute("DELETE FROM app_time")
        self._db.commit()
        self.changed.emit()
        self.agents_changed.emit()

    def export_csv(self, path: Path) -> int:
        records = self.sessions()
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["date", "start", "end", "type", "task", "planned_min", "actual_min", "result",
                             "rating", "distracted_min"])
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
                    {1: "tough", 2: "okay", 3: "great"}.get(r.rating, ""),
                    round(r.distracted_sec / 60, 2),
                ])
        return len(records)

    def close(self) -> None:
        self._db.close()
