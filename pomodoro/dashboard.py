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

from . import garden, insights, quests, reviews, room, studylog, studypath
from .charts import Bar, CalendarHeatmap, ColumnChart, HBarChart, Meter
from .quests import BadgeShelf, QuestList
from .room import RoomGoals, RoomShelf, RoomWidget
from .engine import State
from .garden import GardenWidget
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
    summarize_agents,
)
from .storage import MODE_LABELS
from .theme import chart_color, mode_color
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
        self._table_rows: tuple = ()  # what the session log shows, to skip rebuilding it when nothing changed
        self.setWindowTitle("Pomo · Dashboard")
        self.setWindowIcon(ctrl.icon)
        self.resize(1200, 900)
        self.setMinimumSize(900, 620)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = self.scroll = QScrollArea()
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
        root.addLayout(self._build_study_row())

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

        root.addLayout(self._build_insights_row())
        root.addLayout(self._build_quests_row())
        root.addLayout(self._build_room_row())
        root.addWidget(self._build_agents_card())

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
        self._agents_timer = QTimer(self)
        self._agents_timer.setSingleShot(True)
        self._agents_timer.setInterval(1000)  # agent events can arrive many times a second
        self._agents_timer.timeout.connect(self._refresh_agents)
        ctrl.storage.agents_changed.connect(self._agents_timer.start)
        ctrl.agents.changed.connect(self._agents_timer.start)
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
        default = str(Path.home() / f"pomo-history-{date.today().isoformat()}.csv")
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
        focus_color = chart_color(self.theme, s)

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
        self._refresh_insights(records, start, today, summary, focus_color)
        self._refresh_quests(records, today)
        self._refresh_room()
        self.refresh_study()
        self._refresh_table([r for r in records if start <= r.day <= today])
        self._refresh_agents()

    # ------------------------------------------------------- garden, insights
    def _build_insights_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(16)
        garden_card = Card("Focus garden", "Your sakura grows with every pomodoro. Each blossom is one in this range.")
        self.garden = GardenWidget(self.theme)
        garden_card.body.addWidget(self.garden)
        self.garden_note = _label("", "secondary", wrap=True)
        garden_card.body.addWidget(self.garden_note)
        garden_card.body.addStretch(1)
        insights_card = Card("Insights", "What Pomo has learned about how you focus.")
        self.insight_lines: dict[str, QLabel] = {}
        for key in ("length", "ratings", "score"):
            line = _label("", "secondary", wrap=True)
            line.setTextFormat(Qt.RichText)
            insights_card.body.addWidget(line)
            self.insight_lines[key] = line
        insights_card.body.addSpacing(6)
        self.apps_title = _label("Where your focus time went", "title")
        insights_card.body.addWidget(self.apps_title)
        self.apps = HBarChart(self.theme)
        insights_card.body.addWidget(self.apps)
        insights_card.body.addStretch(1)
        row.addWidget(garden_card, 2)
        row.addWidget(insights_card, 3)
        return row

    def _refresh_insights(self, records, start: date, today: date, summary: Summary, color: QColor) -> None:
        s, storage = self.ctrl.settings, self.ctrl.storage
        lifetime = storage.lifetime_pomodoros()
        self.garden.set_data(lifetime, summary.pomodoros)
        _index, stage = garden.stage(lifetime)
        self.garden_note.setText(f"{stage}  ·  {plural(lifetime, 'pomodoro')} all time  ·  "
                                 f"{plural(summary.pomodoros, 'blossom')} in this range\n{garden.progress_text(lifetime)}")

        counts = insights.rating_counts(records, start, today)
        rated = sum(counts.values())
        tip = insights.length_tip(records, s.focus_min, today)
        lines = self.insight_lines
        if tip is not None:
            lines["length"].setText(f"<b>Best focus length:</b> {tip.text}")
        elif rated < insights.MIN_RATED and not s.rate_sessions:
            lines["length"].setText("<b>Best focus length:</b> turn on session ratings in <i>Settings &gt; Focus</i> "
                                    "and Pomo will learn what works for you.")
        elif rated < insights.MIN_RATED:
            lines["length"].setText(f"<b>Best focus length:</b> rate {insights.MIN_RATED - rated} more sessions "
                                    "(tap a face when one ends) and Pomo will spot what works best for you.")
        else:
            lines["length"].setText(f"<b>Best focus length:</b> your {s.focus_min} minutes suits you well.")
        lines["ratings"].setText(
            f"<b>How sessions felt:</b> {counts[3]} great, {counts[2]} okay, {counts[1]} tough." if rated
            else "<b>How sessions felt:</b> no ratings in this range yet.")

        app_rows = storage.app_time(start)
        score, distracted, _total = insights.focus_score(app_rows)
        if score is not None:
            lines["score"].setText(f"<b>Focus score:</b> {score:.0%} of focus time was away from distracting apps "
                                   f"({fmt_hm(distracted)} on them).")
        elif s.focus_guard:
            lines["score"].setText("<b>Focus score:</b> Pomo needs a little more focus time to tell.")
        else:
            lines["score"].setText("<b>Focus score:</b> turn on <i>Notice distracting apps</i> in "
                                   "<i>Settings &gt; Focus</i> to see where your focus time goes.")
        self.apps.set_data(
            [Bar(app, sec, f"{app}\n{fmt_hm(sec)} during focus" + ("  ·  on your distracting list" if bad else ""),
                 fmt_hm(sec) + ("  ·  distracting" if bad else "")) for app, sec, bad in app_rows[:6]],
            color)
        self.apps.setVisible(bool(app_rows))
        self.apps_title.setVisible(bool(app_rows))

    # --------------------------------------------------------- quests and badges
    def _build_quests_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(16)
        quests_card = Card("Today's quests", "Three small goals a day. Each one you finish earns its badge.")
        self.quest_list = QuestList(self.theme)
        quests_card.body.addWidget(self.quest_list)
        quests_card.body.addStretch(1)
        badges_card = Card("Badges", "Every badge you've earned. Hover one to see how.")
        self.badge_shelf = BadgeShelf(self.theme)
        badges_card.body.addWidget(self.badge_shelf)
        self.badge_note = _label("", "secondary", wrap=True)
        badges_card.body.addWidget(self.badge_note)
        badges_card.body.addStretch(1)
        row.addWidget(quests_card, 2)
        row.addWidget(badges_card, 3)
        return row

    def _refresh_quests(self, records, today: date) -> None:
        s = self.ctrl.settings
        rows = []
        if s.quests_on and s.quest_day == today.isoformat():
            for key in s.quests:
                quest = quests.QUESTS.get(key)
                if quest is not None:
                    rows.append((quest, quests.progress(quest, s, records, today), quests.target_of(quest, s),
                                 key in s.quests_done))
        self.quest_list.set_rows(rows)
        self.badge_shelf.set_badges(s.badges)
        earned = sum(1 for v in s.badges.values() if v)
        self.badge_note.setText(f"{plural(s.quests_total, 'quest')} finished  ·  {earned} of "
                                f"{len(quests.QUESTS) + 1} badges\n{quests.next_outfit_text(s.quests_total)}")

    # ------------------------------------------------------------ study coach
    def _build_study_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(16)
        path_card = Card("My path", "One goal, in small steps. Pomo offers today's step when you sit down.")
        self.path_goal = _label("", "title", wrap=True)
        self.path_status = _label("", "muted")
        self.path_meter = Meter()
        self.path_step = _label("", "secondary", wrap=True)
        self.path_step.setTextFormat(Qt.RichText)
        self.path_milestones = _label("", "muted", wrap=True)
        self.path_milestones.setTextFormat(Qt.RichText)
        buttons = QHBoxLayout()
        self.path_start = QPushButton()
        self.path_start.setObjectName("primary")
        self.path_start.setCursor(Qt.PointingHandCursor)
        self.path_start.clicked.connect(self._start_path_step)
        self.path_open = QPushButton()
        self.path_open.setCursor(Qt.PointingHandCursor)
        self.path_open.clicked.connect(self._open_or_make_path)
        buttons.addWidget(self.path_start)
        buttons.addWidget(self.path_open)
        buttons.addStretch(1)
        for w in (self.path_goal, self.path_status, self.path_meter, self.path_step, self.path_milestones):
            path_card.body.addWidget(w)
        path_card.body.addSpacing(8)
        path_card.body.addLayout(buttons)
        path_card.body.addStretch(1)

        log_card = Card("Study log", "A few sentences in English each day about what you learned.")
        self.log_streak = _label("", "hero")
        self.log_streak_note = _label("", "secondary")
        self.log_latest = _label("", "muted", wrap=True)
        write = QPushButton("Write today's log")
        write.setCursor(Qt.PointingHandCursor)
        write.clicked.connect(self.ctrl.open_study_log)
        log_card.body.addWidget(self.log_streak)
        log_card.body.addWidget(self.log_streak_note)
        log_card.body.addSpacing(8)
        log_card.body.addWidget(self.log_latest)
        log_card.body.addSpacing(8)
        log_card.body.addWidget(write, 0, Qt.AlignLeft)
        log_card.body.addStretch(1)
        row.addWidget(path_card, 3)
        row.addWidget(log_card, 2)
        return row

    def refresh_study(self) -> None:
        from html import escape

        s = self.ctrl.settings
        path = s.study_path
        today = date.today()
        has = bool(path)
        for w in (self.path_status, self.path_meter, self.path_milestones):
            w.setVisible(has)
        if not has:
            self.path_goal.setText("No path yet")
            self.path_step.setText("Not sure what to study, or where to start? Ask Pomo: a few short questions, "
                                   "then a path of small steps toward one goal, to try for 8 weeks.")
            self.path_start.setText(f"Just {s.starter_min} minutes")
            self.path_open.setText("Make my path with Ask Pomo")
        else:
            week, weeks = studypath.week_of(path, today)
            done, total = studypath.progress(path)
            self.path_goal.setText(path["goal"])
            self.path_status.setText(f"Week {min(week, weeks)} of {weeks}  ·  {done} of {plural(total, 'step')} done")
            self.path_meter.set(done, max(1, total), chart_color(self.theme, s))
            nxt = studypath.next_step(path)
            self.path_step.setText(f"<b>Today's step:</b> {escape(studypath.step_label(nxt[2]))}" if nxt
                                   else "<b>Every step is done.</b> Ask Pomo what's next.")
            parts = []
            for m in path["milestones"]:
                d, t = studypath.milestone_progress(m)
                mark = "✓" if t and d == t else ("●" if d else "○")
                parts.append(f"{mark} {escape(m['title'])} ({d}/{t})")
            self.path_milestones.setText("&nbsp;&nbsp;·&nbsp;&nbsp;".join(parts))
            self.path_start.setText(f"Start with {s.starter_min} minutes" if nxt else f"Just {s.starter_min} minutes")
            self.path_open.setText("Open my path")
        days = self.ctrl.storage.log_days()
        n = studylog.streak(days, today)
        self.log_streak.setText(plural(n, "day"))
        self.log_streak_note.setText("in a row" + ("  ·  today's is written" if today in days else "")
                                     + f"  ·  {plural(len(days), 'day')} in all")
        entries = self.ctrl.storage.logs(limit=1)
        if entries:
            text = entries[0].text.replace("\n", " ")
            self.log_latest.setText(f"{entries[0].day:%a %d %b}: {text[:160]}{'…' if len(text) > 160 else ''}")
        else:
            self.log_latest.setText("Nothing written yet. Two or three sentences is plenty, and Pomo can "
                                    "check your English gently.")

    def _start_path_step(self) -> None:
        nxt = studypath.next_step(self.ctrl.settings.study_path)
        if nxt is not None:
            self.ctrl.start_step(nxt[0], nxt[1], small=True)
        else:
            self.ctrl.start_small()

    def _open_or_make_path(self) -> None:
        if self.ctrl.settings.study_path:
            self.ctrl.open_path()
        else:
            from .ai import PATH_START_PROMPT

            self.ctrl.ask_pomo(PATH_START_PROMPT)

    # ------------------------------------------------------------- Pomo's room
    def _build_room_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(16)
        self.room_card = Card("Pomo's room", "It fills up as you focus and finish quests. "
                                             "Click anything in the room to swap it, or poke Pomo.")
        self.room = RoomWidget(self.theme)
        self.room.picked.connect(self._place)
        self.room_card.body.addWidget(self.room)
        self.room_note = _label("", "secondary", wrap=True)
        self.room_card.body.addSpacing(6)
        self.room_card.body.addWidget(self.room_note)
        items_card = Card("Room items", "What's next to earn, and everything so far. "
                                        "Click an item you've earned to put it in the room.")
        self.room_goals = RoomGoals(self.theme)
        items_card.body.addWidget(self.room_goals)
        items_card.body.addSpacing(10)
        self.room_shelf = RoomShelf(self.theme)
        self.room_shelf.picked.connect(self._place)
        items_card.body.addWidget(self.room_shelf)
        items_card.body.addStretch(1)
        row.addWidget(self.room_card, 3)
        row.addWidget(items_card, 2)
        return row

    def _refresh_room(self) -> None:
        s = self.ctrl.settings
        hours, quests_done, perfect, have = self.ctrl.room_progress()
        placed = room.layout(s, have)
        self.room.set_data(placed, have, s.badges, s.pomo_outfit)
        self.room_goals.set_rows(room.upcoming(hours, quests_done, perfect))
        self.room_shelf.set_items(have, placed)
        self.room_note.setText(f"{len(have)} of {len(room.ITEMS)} items  ·  {hours:.1f} focus hours  ·  "
                               f"{plural(quests_done, 'quest')}  ·  {plural(perfect, 'Perfect day')}")

    def _place(self, slot: str, key: str) -> None:
        self.ctrl.place_in_room(slot, key)
        self._refresh_room()
        self.room.cheer()

    def show_room(self) -> None:
        """Scroll down to the room (once the window is laid out)."""
        QTimer.singleShot(50, lambda: self.scroll.verticalScrollBar().setValue(self.room_card.y() - 16))

    # ------------------------------------------------------------ coding agents
    def _build_agents_card(self) -> Card:
        card = Card("Coding agents", "What your coding agents (Claude Code, Codex, Gemini) did today, as Pomo saw it.")
        grid = QGridLayout()
        grid.setSpacing(12)
        self.agent_tiles: dict[str, StatTile] = {}
        for i, (key, label) in enumerate((("sessions", "Sessions"), ("prompts", "Prompts"), ("tools", "Tool calls"),
                                          ("files", "Files edited"), ("commands", "Commands"),
                                          ("approvals", "Approved here"))):
            self.agent_tiles[key] = StatTile(label)
            grid.addWidget(self.agent_tiles[key], 0, i)
        for i, (key, label) in enumerate((("agent_time", "Agents worked"), ("with_focus", "While you focused"),
                                          ("with_breaks", "During your breaks"))):
            self.agent_tiles[key] = StatTile(label)
            grid.addWidget(self.agent_tiles[key], 1, i * 2, 1, 2)
        card.body.addLayout(grid)
        card.body.addSpacing(10)
        self.agent_live = _label("", "secondary", wrap=True)
        self.agent_live.setTextFormat(Qt.RichText)
        card.body.addWidget(self.agent_live)
        return card

    def _refresh_agents(self) -> None:
        if not self.isVisible():
            return
        today = date.today()
        sm = summarize_agents(self.ctrl.storage.agent_events(since=today), today)
        tiles = self.agent_tiles
        tiles["sessions"].set(str(sm.sessions), ", ".join(p for p, _ in sm.projects[:3]))
        tiles["prompts"].set(str(sm.prompts), f"{plural(sm.turns, 'finished turn')}")
        tiles["tools"].set(str(sm.tool_calls))
        tiles["files"].set(str(sm.files_edited), f"+{sm.lines_added} / -{sm.lines_removed} lines")
        tiles["commands"].set(str(sm.commands))
        tiles["approvals"].set(str(sm.approvals_allowed), f"{sm.approvals_denied} denied")
        overlap = insights.agent_overlap(self.ctrl.storage.agent_events(since=today),
                                         self.ctrl.storage.sessions(since=today), today)
        tiles["agent_time"].set(fmt_hm(overlap.agent_sec), plural(overlap.turns, "finished turn"))
        def share(seconds: float) -> str:
            return f"{seconds / overlap.agent_sec:.0%} of their time" if overlap.agent_sec else ""

        tiles["with_focus"].set(fmt_hm(overlap.during_focus_sec), share(overlap.during_focus_sec))
        tiles["with_breaks"].set(fmt_hm(overlap.during_break_sec), share(overlap.during_break_sec))
        hub = self.ctrl.agents
        from html import escape
        rows = [f"<b>{escape(a.project)}</b>" + (f" ({escape(a.agent_label)})" if a.agent != "claude" else "")
                + f" &middot; {escape(a.status_text)}"
                + (f" &middot; {escape(a.activity)}" if a.status == "working" and a.activity else "")
                for a in hub.live_sessions()[:6]]
        if rows:
            text = "Live now:<br>" + "<br>".join(rows)
        elif sm.sessions:
            text = "No sessions running right now."
        else:
            text = ("No agent activity yet today. Connect Claude Code, Codex or Gemini in "
                    "<b>Settings &gt; Agents</b> and Pomo will keep an eye on your sessions.")
        waiting = self.ctrl.settings.reviews
        if waiting:
            items = [f"<b>{escape(r['project'])}</b> &middot; {escape(reviews.summary(r))}" for r in reversed(waiting[-4:])]
            text += "<br><br>Waiting for your review (right-click the timer to start one):<br>" + "<br>".join(items)
        self.agent_live.setText(text)

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
        shown = tuple((r.started_at, r.mode, r.actual_sec, r.completed, r.task, fmt_day(r.day)) for r in rows)
        if shown == self._table_rows:
            return  # nothing new (the dashboard refreshes every minute)
        self._table_rows = shown
        header = self.table.horizontalHeader()
        for col in (0, 1, 2, 4, 5):  # sized to their contents once, after filling, not after every cell
            header.setSectionResizeMode(col, QHeaderView.Interactive)
        self.table.setUpdatesEnabled(False)
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
        for col in (0, 1, 2, 4, 5):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.table.resizeRowsToContents()
        self.table.setUpdatesEnabled(True)
