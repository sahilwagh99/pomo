"""Dashboard window: today's progress, KPIs, charts and the session log."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .charts import Bar, CalendarHeatmap, ColumnChart, HBarChart, Meter
from .engine import State
from .stats import (
    Summary,
    daily_map,
    fmt_clock,
    fmt_day,
    fmt_hm,
    fmt_hour,
    plural,
    streaks,
    summarize,
)
from .storage import MODE_LABELS
from .theme import mode_color
from .winutil import set_dark_title_bar

RANGES = (("7 days", 7), ("30 days", 30), ("90 days", 90), ("All time", 0))
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
UP, DOWN = "▲", "▼"


def _label(text: str = "", role: str | None = None, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    if role:
        label.setProperty("role", role)
    label.setWordWrap(wrap)
    return label


class Card(QFrame):
    def __init__(self, title: str, subtitle: str = ""):
        super().__init__()
        self.setObjectName("card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(18, 16, 18, 16)
        self.body.setSpacing(2)
        self.body.addWidget(_label(title, "title"))
        if subtitle:
            self.body.addWidget(_label(subtitle, "muted", wrap=True))
        self.body.addSpacing(10)


class StatTile(QFrame):
    def __init__(self, label: str):
        super().__init__()
        self.setObjectName("tile")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(2)
        self._value = _label("-", "tileValue")
        self._note = _label("", "muted", wrap=True)
        lay.addWidget(_label(label, "muted"))
        lay.addWidget(self._value)
        lay.addWidget(self._note)
        lay.addStretch(1)

    def set(self, value: str, note: str = "", color: str | None = None) -> None:
        self._value.setText(value)
        self._note.setText(note)
        self._note.setStyleSheet(f"color: {color};" if color else "")


class TodayCard(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("card")
        self.setMinimumWidth(300)
        self.setMaximumWidth(360)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 20)
        lay.setSpacing(2)
        head = QHBoxLayout()
        head.addWidget(_label("Today", "title"))
        head.addStretch(1)
        self.date = _label("", "muted")
        head.addWidget(self.date)
        lay.addLayout(head)
        lay.addSpacing(6)
        self.hero = _label("0m", "hero")
        lay.addWidget(self.hero)
        lay.addWidget(_label("focused today", "secondary"))
        lay.addSpacing(16)
        row = QHBoxLayout()
        self.progress = _label("", "secondary")
        self.left = _label("", "muted")
        row.addWidget(self.progress)
        row.addStretch(1)
        row.addWidget(self.left)
        lay.addLayout(row)
        lay.addSpacing(6)
        self.meter = Meter()
        lay.addWidget(self.meter)
        lay.addSpacing(14)
        self.now = _label("", "muted", wrap=True)
        lay.addWidget(self.now)
        lay.addStretch(1)


class Dashboard(QWidget):
    def __init__(self, ctrl):
        super().__init__(None, Qt.Window)
        self.ctrl = ctrl
        self.theme = ctrl.theme
        self._dot_icons: dict[str, QIcon] = {}
        self.setWindowTitle("FocusOrb · Dashboard")
        self.setWindowIcon(ctrl.icon)
        self.resize(1200, 900)
        self.setMinimumSize(900, 620)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll)
        page = QWidget()
        page.setObjectName("page")
        scroll.setWidget(page)
        root = QVBoxLayout(page)
        root.setContentsMargins(28, 24, 28, 28)
        root.setSpacing(16)

        root.addLayout(self._build_header())

        row1 = QHBoxLayout()
        row1.setSpacing(16)
        self.today = TodayCard()
        row1.addWidget(self.today)
        grid = QGridLayout()
        grid.setSpacing(16)
        self.tiles: dict[str, StatTile] = {}
        tiles = [
            ("focus", "Focus time"), ("pomodoros", "Pomodoros"), ("avg", "Avg per active day"), ("rate", "Completion rate"),
            ("streak", "Current streak"), ("peak", "Peak hour"), ("best", "Best day"), ("breaks", "Break time"),
        ]
        for i, (key, label) in enumerate(tiles):
            self.tiles[key] = StatTile(label)
            grid.addWidget(self.tiles[key], i // 4, i % 4)
        row1.addLayout(grid, 1)
        root.addLayout(row1)

        self.daily_card = Card("Daily focus", "Minutes of focus per day. The line marks your daily goal.")
        self.daily = ColumnChart(self.theme, 230)
        self.daily_card.body.addWidget(self.daily)
        root.addWidget(self.daily_card)

        row3 = QHBoxLayout()
        row3.setSpacing(16)
        heat_card = Card("Consistency", "Each square is a day, shaded by how close you came to your daily goal.")
        self.heatmap = CalendarHeatmap(self.theme)
        heat_card.body.addWidget(self.heatmap)
        heat_card.body.addStretch(1)
        tasks_card = Card("Top tasks", "Where your focus time went.")
        self.tasks = HBarChart(self.theme)
        tasks_card.body.addWidget(self.tasks)
        tasks_card.body.addStretch(1)
        row3.addWidget(heat_card, 3)
        row3.addWidget(tasks_card, 2)
        root.addLayout(row3)

        row4 = QHBoxLayout()
        row4.setSpacing(16)
        hour_card = Card("Time of day", "Total focus by hour, across the selected range.")
        self.hours = ColumnChart(self.theme, 190)
        hour_card.body.addWidget(self.hours)
        week_card = Card("Weekday rhythm", "Average focus on each day of the week.")
        self.weekdays = ColumnChart(self.theme, 190)
        week_card.body.addWidget(self.weekdays)
        row4.addWidget(hour_card, 3)
        row4.addWidget(week_card, 2)
        root.addLayout(row4)

        log_card = Card("Session log", "Most recent first. Export the full history as CSV from the top right.")
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Date", "Start", "Type", "Task", "Length", "Result"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setShowGrid(False)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.setMinimumHeight(320)
        header = self.table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        for col in (0, 1, 2, 4, 5):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        log_card.body.addWidget(self.table)
        root.addWidget(log_card)

        ctrl.storage.changed.connect(self.refresh)
        ctrl.engine.changed.connect(self._refresh_today_note)
        self._clock = QTimer(self)
        self._clock.setInterval(60_000)
        self._clock.timeout.connect(self.refresh)
        self._clock.start()

    def _build_header(self) -> QHBoxLayout:
        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(_label("Focus dashboard", "h1"))
        self.subtitle = _label("", "secondary")
        titles.addWidget(self.subtitle)
        head.addLayout(titles)
        head.addStretch(1)

        segment = QFrame()
        segment.setObjectName("segment")
        seg_lay = QHBoxLayout(segment)
        seg_lay.setContentsMargins(3, 3, 3, 3)
        seg_lay.setSpacing(2)
        self.range_group = QButtonGroup(self)
        current = self.ctrl.settings.dashboard_range
        for text, days in RANGES:
            button = QPushButton(text)
            button.setObjectName("seg")
            button.setCheckable(True)
            button.setChecked(days == current)
            button.setCursor(Qt.PointingHandCursor)
            self.range_group.addButton(button, days)
            seg_lay.addWidget(button)
        self.range_group.idClicked.connect(self._set_range)
        head.addWidget(segment, 0, Qt.AlignVCenter)
        head.addSpacing(8)
        export = QPushButton("Export CSV")
        export.setCursor(Qt.PointingHandCursor)
        export.clicked.connect(self._export)
        head.addWidget(export, 0, Qt.AlignVCenter)
        return head

    # ---------------------------------------------------------------- events
    def showEvent(self, event) -> None:
        super().showEvent(event)
        set_dark_title_bar(self, self.theme.dark)
        self.refresh()

    def _set_range(self, days: int) -> None:
        self.ctrl.settings.dashboard_range = days
        self.ctrl.save_settings()
        self.refresh()

    def _export(self) -> None:
        default = str(Path.home() / f"focusorb-history-{date.today().isoformat()}.csv")
        path, _ = QFileDialog.getSaveFileName(self, "Export history", default, "CSV files (*.csv)")
        if path:
            n = self.ctrl.storage.export_csv(Path(path))
            QMessageBox.information(self, "Export complete", f"Saved {plural(n, 'session')} to\n{path}")

    # --------------------------------------------------------------- refresh
    def refresh(self) -> None:
        if not self.isVisible():
            return
        ctrl, s = self.ctrl, self.ctrl.settings
        records = ctrl.storage.sessions()
        today = date.today()
        days = s.dashboard_range
        if days:
            start = today - timedelta(days=days - 1)
        else:
            start = min((r.day for r in records), default=today)
        summary = summarize(records, start, today)
        prev = summarize(records, start - timedelta(days=days), start - timedelta(days=1)) if days else None
        current_streak, best_streak = streaks(records, today)
        focus_color = mode_color(self.theme, s, "focus")

        range_text = f"last {days} days" if days else f"all time, since {fmt_day(start, weekday=False)}"
        self.subtitle.setText(f"{today:%A}, {fmt_day(today, weekday=False)}  ·  showing {range_text}")

        self._refresh_today(summarize(records, today, today), focus_color)
        self._refresh_tiles(summary, prev, current_streak, best_streak)
        self._refresh_daily(summary, focus_color)
        self.heatmap.set_data(daily_map(records), today, s.daily_goal * s.focus_min * 60)
        self.tasks.set_data(
            [Bar(name, sec, f"{name}\n{fmt_hm(sec)} · {plural(n, 'pomodoro')}", fmt_hm(sec))
             for name, sec, n in summary.tasks[:7]],
            focus_color,
        )
        self.hours.set_data(
            [Bar(f"{h % 12 or 12}{'a' if h < 12 else 'p'}", sec / 60,
                 f"{fmt_hour(h)} to {fmt_hour(h + 1)}\n{fmt_hm(sec)} focused") for h, sec in enumerate(summary.by_hour)],
            focus_color, label_every=3,
        )
        counts = [0] * 7
        for d in summary.days:
            counts[d.day.weekday()] += 1
        self.weekdays.set_data(
            [Bar(WEEKDAYS[i], (sec / counts[i] if counts[i] else 0) / 60,
                 f"{WEEKDAYS[i]}\nAverage {fmt_hm(sec / counts[i] if counts[i] else 0)} focused")
             for i, sec in enumerate(summary.by_weekday)],
            focus_color, label_every=1,
        )
        self._refresh_table([r for r in records if start <= r.day <= today])

    def _refresh_today(self, today: Summary, color: QColor) -> None:
        goal = self.ctrl.settings.daily_goal
        done = today.pomodoros
        card = self.today
        card.date.setText(f"{today.start:%A}")
        card.hero.setText(fmt_hm(today.focus_sec))
        card.progress.setText(f"{done} of {plural(goal, 'pomodoro')}")
        card.left.setText("Goal reached" if done >= goal else f"{goal - done} to go")
        card.left.setStyleSheet(f"color: {self.theme.success}; font-weight: 600;" if done >= goal else "")
        card.meter.set(done, goal, color)
        self._refresh_today_note()

    def _refresh_today_note(self) -> None:
        e = self.ctrl.engine
        clock = fmt_clock(e.remaining)
        note = {
            State.RUNNING: f"Now: {e.mode.label}, {clock} left",
            State.PAUSED: f"Paused: {e.mode.label}, {clock} left",
            State.IDLE: f"Ready: {e.mode.label} ({clock})",
            State.FINISHED: f"Up next: {e.mode.label} ({clock})",
        }[e.state]
        if e.task:
            note += f"\nTask: {e.task}"
        self.today.now.setText(note)

    def _delta(self, cur: float, prev: float, fmt, n_days: int) -> tuple[str, str | None]:
        period = f"vs previous {n_days} days"
        if cur == prev:
            return f"Same as previous {n_days} days", None
        if prev == 0:
            return f"{UP} from 0 {period}", self.theme.success
        diff = cur - prev
        arrow = UP if diff > 0 else DOWN
        color = self.theme.success if diff > 0 else self.theme.danger
        return f"{arrow} {fmt(abs(diff), prev)} {period}", color

    def _refresh_tiles(self, sm: Summary, prev: Summary | None, cur_streak: int, best_streak: int) -> None:
        tiles = self.tiles
        if prev is not None:
            note, color = self._delta(sm.focus_sec, prev.focus_sec, lambda d, p: f"{d / p:.0%}", sm.n_days)
            tiles["focus"].set(fmt_hm(sm.focus_sec), note, color)
            note, color = self._delta(sm.pomodoros, prev.pomodoros, lambda d, p: f"{int(d)}", sm.n_days)
            tiles["pomodoros"].set(str(sm.pomodoros), note, color)
        else:
            tiles["focus"].set(fmt_hm(sm.focus_sec), f"across {plural(sm.n_days, 'day')}")
            tiles["pomodoros"].set(str(sm.pomodoros), f"{sm.pomodoros / max(1, sm.n_days):.1f} per day on average")
        tiles["avg"].set(fmt_hm(sm.avg_per_active_day), f"{sm.active_days} of {plural(sm.n_days, 'day')} active")
        rate = sm.completion_rate
        tiles["rate"].set("-" if rate is None else f"{rate:.0%}",
                          "no sessions yet" if rate is None else f"{sm.interrupted} stopped early")
        tiles["streak"].set(plural(cur_streak, "day"), f"Best: {plural(best_streak, 'day')}")
        peak = sm.peak_hour
        tiles["peak"].set("-" if peak is None else fmt_hour(peak),
                          "" if peak is None else f"Most focus between {fmt_hour(peak)} and {fmt_hour(peak + 1)}")
        best = sm.best_day
        tiles["best"].set("-" if best is None else fmt_hm(best.focus_sec),
                          "" if best is None else f"{fmt_day(best.day)} · {plural(best.pomodoros, 'pomodoro')}")
        ratio = f"{sm.focus_sec / sm.break_sec:.1f} : 1 focus to break" if sm.break_sec else "no breaks logged"
        tiles["breaks"].set(fmt_hm(sm.break_sec), ratio)

    def _refresh_daily(self, sm: Summary, color: QColor) -> None:
        s = self.ctrl.settings
        if sm.n_days <= 120:
            short = sm.n_days <= 7
            bars = [
                Bar(f"{d.day:%a}" if short else fmt_day(d.day, weekday=False), d.focus_sec / 60,
                    f"{fmt_day(d.day)}\n{fmt_hm(d.focus_sec)} focused · {plural(d.pomodoros, 'pomodoro')}")
                for d in sm.days
            ]
            goal = s.daily_goal * s.focus_min
            self.daily.set_data(bars, color, ref=goal, ref_label=f"Goal {fmt_hm(goal * 60)}")
            return
        weeks: dict[date, list[float]] = {}
        for d in sm.days:
            bucket = weeks.setdefault(d.day - timedelta(days=d.day.weekday()), [0.0, 0])
            bucket[0] += d.focus_sec
            bucket[1] += d.pomodoros
        bars = [
            Bar(fmt_day(ws, weekday=False), sec / 60,
                f"Week of {fmt_day(ws, weekday=False)}\n{fmt_hm(sec)} focused · {plural(int(n), 'pomodoro')}")
            for ws, (sec, n) in sorted(weeks.items())
        ]
        self.daily.set_data(bars, color)

    def _dot(self, mode: str) -> QIcon:
        if mode not in self._dot_icons:
            pm = QPixmap(12, 12)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            p.setBrush(mode_color(self.theme, self.ctrl.settings, mode))
            p.drawEllipse(2, 2, 8, 8)
            p.end()
            self._dot_icons[mode] = QIcon(pm)
        return self._dot_icons[mode]

    def _refresh_table(self, records) -> None:
        t = self.theme
        rows = list(reversed(records))[:300]
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            type_item = QTableWidgetItem(self._dot(r.mode), MODE_LABELS.get(r.mode, r.mode))
            result = QTableWidgetItem("Completed" if r.completed else "Stopped early")
            result.setForeground(QColor(t.ink_2 if r.completed else t.muted))
            length = QTableWidgetItem(fmt_hm(r.actual_sec))
            length.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            values = [
                QTableWidgetItem(fmt_day(r.day)),
                QTableWidgetItem(r.started_at.strftime("%H:%M")),
                type_item,
                QTableWidgetItem(r.task or ("-" if r.mode == "focus" else "")),
                length,
                result,
            ]
            for col, item in enumerate(values):
                self.table.setItem(i, col, item)
        self.table.resizeRowsToContents()
