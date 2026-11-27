"""The panel's Pomo tabs: Timer, Notes, Music, Agents and Habits, and the cards Home borrows from them."""
from __future__ import annotations

import time
from datetime import date, datetime
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetricsF, QPainter
from PySide6.QtWidgets import (
    QCheckBox,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from . import habits, notes, studypath
from .config import FOCUS_SOUNDS
from .engine import Mode, State
from .panel import Card, IconButton, Page, label, row
from .pomo import Pomo, mood_for
from .stats import fmt_clock, fmt_hm, plural
from .theme import FONT_FAMILY, mode_color


def chip(text: str, fn, checkable: bool = False) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("chip")
    button.setCheckable(checkable)
    button.setCursor(Qt.PointingHandCursor)
    button.setFocusPolicy(Qt.NoFocus)
    button.clicked.connect(lambda _c=False: fn())
    return button


def _font(px: float, weight=QFont.Normal) -> QFont:
    font = QFont(FONT_FAMILY)
    font.setPixelSize(round(px))
    font.setWeight(weight)
    return font


# ------------------------------------------------------------------ the timer card
class TimerFace(QWidget):
    """Pomo, the time, the mode and your place in the cycle, drawn like the timer itself."""

    def __init__(self, ctrl, big: bool = False):
        super().__init__()
        self.ctrl, self.big = ctrl, big
        self.pomo = Pomo()
        self.setMinimumHeight(150 if big else 118)
        self._last = time.monotonic()
        self.frames = QTimer(self)
        self.frames.setInterval(60)
        self.frames.timeout.connect(self._step)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._last = time.monotonic()
        self.frames.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self.frames.stop()

    def _pomo_spot(self) -> tuple[QPointF, float]:
        size = 64.0 if self.big else 52.0
        return QPointF(size * 0.62, self.height() * 0.42), size

    def _step(self) -> None:
        now = time.monotonic()
        dt, self._last = now - self._last, now
        e, c = self.ctrl.engine, self.ctrl
        self.pomo.set_mood(mood_for(e.state, e.mode.is_break, c.idle_seconds()))
        self.pomo.outfit = c.current_outfit()
        self.pomo.signal = c.agent_signal()
        anchor, size = self._pomo_spot()
        cursor = self.mapFromGlobal(QCursor.pos())
        self.pomo.update(dt, ((cursor.x() - anchor.x()) / size, (cursor.y() - anchor.y()) / size))
        self.update()

    def mousePressEvent(self, event) -> None:
        anchor, size = self._pomo_spot()
        if (event.position() - anchor).manhattanLength() < size * 0.7:
            self.pomo.poke()

    def paintEvent(self, _event) -> None:
        c, e, t = self.ctrl, self.ctrl.engine, self.ctrl.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        anchor, size = self._pomo_spot()
        if c.settings.show_buddy:
            self.pomo.paint(p, anchor, size, ground_shadow=True)
        x0 = size * 1.35
        accent = mode_color(t, c.settings, e.mode.value)
        label_text = "PAUSED" if e.state is State.PAUSED else c.mode_label().upper()
        p.setPen(Qt.NoPen)
        p.setBrush(c.mode_dot() or accent)
        p.drawEllipse(QPointF(x0 + 3, 14), 3.4, 3.4)
        p.setFont(_font(11, QFont.DemiBold))
        p.setPen(QColor(t.ink_2))
        p.drawText(QRectF(x0 + 12, 4, self.width() - x0 - 12, 20), Qt.AlignLeft | Qt.AlignVCenter, label_text)
        p.setFont(_font(44 if self.big else 36, QFont.DemiBold))
        p.setPen(QColor(t.muted if e.state is State.PAUSED else t.ink))
        p.drawText(QRectF(x0 - 2, 22, self.width() - x0, 56 if self.big else 46), Qt.AlignLeft | Qt.AlignVCenter,
                   fmt_clock(e.remaining))
        y = 84 if self.big else 74
        n, done = e.cycle_length, min(e.cycle_index, e.cycle_length)
        for i in range(n):
            center = QPointF(x0 + 5 + i * 14, y)
            p.setPen(Qt.NoPen if i < done else QColor(t.muted))
            p.setBrush(accent if i < done else Qt.NoBrush)
            p.drawEllipse(center, 4, 4)
        info = f"{c.today_count}/{c.settings.daily_goal} today"
        if e.task:
            info += f"  ·  {e.task}"
        p.setFont(_font(12))
        p.setPen(QColor(t.muted))
        width = self.width() - x0
        p.drawText(QRectF(x0, y + 10, width, 18), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetricsF(p.font()).elidedText(info, Qt.ElideRight, width))
        bar = QRectF(x0, y + 36, width - 4, 4)
        track = QColor(accent)
        track.setAlpha(60)
        p.setBrush(track)
        p.drawRoundedRect(bar, 2, 2)
        if e.fraction_left > 0:
            p.setBrush(accent)
            p.drawRoundedRect(QRectF(bar.left(), bar.top(), max(4.0, bar.width() * e.fraction_left), 4), 2, 2)
        p.end()


class TimerCard(Card):
    def __init__(self, ctrl, width: int | None = 300, big: bool = False):
        super().__init__(ctrl, "", width)
        self.face = TimerFace(ctrl, big)
        self.face.setFixedHeight(150 if big else 128)
        self.body.addWidget(self.face)
        self.body.addStretch(1)
        self.reset = IconButton(ctrl, "reset", "Reset  (R)", 34)
        self.toggle = IconButton(ctrl, "play", "Start", 42)
        self.toggle.lit = True
        self.skip = IconButton(ctrl, "skip", "Skip", 34)
        self.task = chip("Set task", ctrl.edit_task)
        self.reset.clicked.connect(ctrl.engine.reset)
        self.toggle.clicked.connect(ctrl.engine.toggle)
        self.skip.clicked.connect(ctrl.engine.skip)
        self.body.addLayout(row(self.reset, self.toggle, self.skip, None, self.task))
        self.tick()

    def tick(self) -> None:
        e = self.ctrl.engine
        running = e.state is State.RUNNING
        self.toggle.glyph = "pause" if running else "play"
        self.toggle.setToolTip("Pause" if running else "Start")
        self.toggle.update()
        self.task.setText(("Task: " + e.task[:22]) if e.task else "Set task")
        self.face.update()


# ------------------------------------------------------------------ Timer
class TimerPage(Page):
    def __init__(self, ctrl):
        super().__init__(ctrl)
        self.timer = self.add(TimerCard(ctrl, 330, big=True))
        mode = Card(ctrl, "Mode", 210)
        self.modes = {}
        for m in Mode:
            self.modes[m] = chip(m.label, lambda m=m: (ctrl.engine.switch_mode(m), self.refresh()), checkable=True)
            mode.body.addWidget(self.modes[m])
        mode.body.addLayout(row(chip("+1 min", lambda: ctrl.engine.adjust(60)),
                                chip("+5 min", lambda: ctrl.engine.adjust(300)), None))
        mode.body.addWidget(chip(f"Just {ctrl.settings.starter_min} minutes", ctrl.start_small))
        mode.body.addStretch(1)
        self.add(mode)
        plan = Card(ctrl, "Plan")
        plan.action("chat", "Plan with Ask Pomo", ctrl.open_chat)
        self.plan = QListWidget()
        plan.body.addWidget(self.plan, 1)
        self.add(plan, 1)
        today = Card(ctrl, "Today", 200)
        self.today_big = label("", "big")
        self.today_info = label("", "secondary", wrap=True)
        today.body.addWidget(self.today_big)
        today.body.addWidget(self.today_info)
        today.body.addStretch(1)
        today.body.addWidget(chip("Dashboard", ctrl.open_dashboard))
        self.add(today)

    def refresh(self) -> None:
        c = self.ctrl
        for m, button in self.modes.items():
            button.setChecked(m is c.engine.mode)
        self.plan.clear()
        for i, item in enumerate(c.settings.task_queue[:12]):
            self.plan.addItem(f"{'Now: ' if i == 0 else ''}{item['task']}  ·  {plural(item['pomodoros'], 'pomodoro')}")
        if not c.settings.task_queue:
            empty = QListWidgetItem("No plan yet. Ask Pomo can split your work into pomodoros.")
            empty.setFlags(Qt.NoItemFlags)
            self.plan.addItem(empty)
        sessions = [s for s in c.storage.sessions(since=date.today()) if s.mode == "focus" and s.day == date.today()]
        self.today_big.setText(f"{c.today_count}/{c.settings.daily_goal}")
        focus = sum(s.actual_sec for s in sessions)
        self.today_info.setText(f"pomodoros today\n{fmt_hm(focus)} of focus" if focus else "pomodoros today")
        self.timer.tick()

    def tick(self) -> None:
        self.timer.tick()


# ------------------------------------------------------------------ Notes
class QuickNoteCard(Card):
    """Jot something down: it goes into today's note, or onto a sticky note."""

    def __init__(self, ctrl, width: int | None = None):
        super().__init__(ctrl, "Notes", width)
        self.action("popout", "Open notes", ctrl.open_notes)
        self.jot = QPlainTextEdit()
        self.jot.setObjectName("jot")
        self.jot.setPlaceholderText("Jot something down…")
        self.jot.setTabChangesFocus(True)
        self.body.addWidget(self.jot, 1)
        self.saved = label("", "muted")
        self.body.addLayout(row(chip("Add to today's note", self.to_daily), chip("Sticky", self.to_sticky),
                                None, self.saved))

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() & Qt.ControlModifier:
            self.to_daily()
        else:
            super().keyPressEvent(event)

    def _text(self) -> str:
        return self.jot.toPlainText().strip()

    def to_daily(self) -> None:
        text = self._text()
        if text and self.ctrl.add_to_daily_note(text):
            self.jot.clear()
            self.saved.setText("Added to today's note")
            QTimer.singleShot(2500, lambda: self.saved.setText(""))

    def to_sticky(self) -> None:
        text = self._text()
        note = self.ctrl.stickies.new_note()
        if text:
            note.body.setPlainText(text)
            self.jot.clear()


class NotesPage(Page):
    def __init__(self, ctrl):
        super().__init__(ctrl)
        self.add(QuickNoteCard(ctrl, 360))
        recent = Card(ctrl, "Recent notes")
        recent.action("plus", "New note", self._new)
        recent.action("calendar", "Today's note", ctrl.open_daily_note)
        self.recent = QListWidget()
        self.recent.itemClicked.connect(lambda item: ctrl.open_notes(Path(item.data(Qt.UserRole))))
        recent.body.addWidget(self.recent, 1)
        self.add(recent, 1)
        sticky = Card(ctrl, "Sticky notes", 300)
        sticky.action("plus", "New sticky note", lambda: ctrl.stickies.new_note())
        self.show_btn = sticky.action("popout", "Show them all", lambda: (ctrl.panel_aside(), ctrl.stickies.show_all()))
        self.stickies = QListWidget()
        self.stickies.itemClicked.connect(self._show_sticky)
        sticky.body.addWidget(self.stickies, 1)
        self.add(sticky)

    def _new(self) -> None:
        self.ctrl.open_notes()
        if self.ctrl.notes_window is not None:
            self.ctrl.notes_window.new_note()

    def _show_sticky(self, item: QListWidgetItem) -> None:
        index = item.data(Qt.UserRole)
        if isinstance(index, int) and index < self.ctrl.stickies.count():
            self.ctrl.panel_aside()
            self.ctrl.stickies.notes[index].show_note()

    def refresh(self) -> None:
        c = self.ctrl
        self.recent.clear()
        if c.settings.notes_dir:
            vault = c.notes_vault()
            ordered = sorted(vault.notes(), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
            for path in ordered[:8]:
                item = QListWidgetItem(path.stem)
                item.setData(Qt.UserRole, str(path))
                item.setToolTip(vault.rel(path))
                self.recent.addItem(item)
        if self.recent.count() == 0:
            empty = QListWidgetItem("No notes yet: jot something, or click +")
            empty.setFlags(Qt.NoItemFlags)
            self.recent.addItem(empty)
        self.stickies.clear()
        from .stickies import when_text
        now = datetime.now()
        for i, note in enumerate(c.stickies.notes):
            when = note.reminder()
            text = note.first_line() or "Empty sticky note"
            item = QListWidgetItem(f"{text}  ·  {when_text(when, now)}" if when else text)
            item.setData(Qt.UserRole, i)
            item.setToolTip("Click to bring it to the front")
            self.stickies.addItem(item)
        if not c.stickies.notes:
            empty = QListWidgetItem("None yet: click + for one")
            empty.setFlags(Qt.NoItemFlags)
            self.stickies.addItem(empty)


# ------------------------------------------------------------------ Music
class Cover(QWidget):
    def __init__(self, ctrl, size: int = 96):
        super().__init__()
        self.ctrl = ctrl
        self.setFixedSize(size, size)

    def paintEvent(self, _event) -> None:
        t = self.ctrl.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        art = getattr(self.ctrl, "track_art", None)
        from PySide6.QtGui import QPainterPath
        clip = QPainterPath()
        clip.addRoundedRect(rect, 14, 14)
        p.setClipPath(clip)
        if art is not None and not art.isNull():
            p.drawImage(rect, art)
        else:
            p.fillRect(rect, QColor(t.hover))
            from .icons import draw_glyph
            draw_glyph(p, "note", rect.center(), rect.width() * 0.4, QColor(t.muted))
        p.end()


class NowPlayingCard(Card):
    def __init__(self, ctrl, width: int | None = None):
        super().__init__(ctrl, "Now playing", width)
        self.cover = Cover(ctrl, 92)
        self.title = label("", "title", wrap=True)
        self.byline = label("", "secondary", wrap=True)
        self.app = label("", "muted")
        info = QVBoxLayout()
        info.setSpacing(2)
        for w in (self.title, self.byline, self.app):
            info.addWidget(w)
        info.addStretch(1)
        self.prev = IconButton(ctrl, "previous", "Previous", 32)
        self.play = IconButton(ctrl, "play", "Play", 40)
        self.play.lit = True
        self.next = IconButton(ctrl, "skip", "Next", 32)
        self.prev.clicked.connect(lambda: ctrl.media_command("previous"))
        self.play.clicked.connect(lambda: ctrl.media_command("toggle"))
        self.next.clicked.connect(lambda: ctrl.media_command("next"))
        info.addLayout(row(self.prev, self.play, self.next, None))
        self.body.addLayout(row(self.cover, info, stretch_at=1))
        self.refresh()

    def refresh(self) -> None:
        t = getattr(self.ctrl, "track", None)
        if t is None:
            self.title.setText("Nothing playing")
            self.byline.setText("Play something in Spotify, YouTube or any player, and it shows up here.")
            self.app.setText("")
        else:
            self.title.setText(t.title)
            self.byline.setText(t.byline or "")
            self.app.setText(t.app or "")
        for b in (self.prev, self.play, self.next):
            b.setEnabled(t is not None)
        if t is not None:
            self.play.glyph = "pause" if t.playing else "play"
            self.prev.setEnabled(t.can_previous)
            self.next.setEnabled(t.can_next)
        self.play.update()
        self.cover.update()

    def tick(self) -> None:
        self.refresh()


class SoundsCard(Card):
    def __init__(self, ctrl, width: int | None = None):
        super().__init__(ctrl, "Focus sounds", width)
        self.chips = {}
        line = row()
        for key, text in FOCUS_SOUNDS.items():
            self.chips[key] = chip(text, lambda k=key: (ctrl.set_focus_sound(k), self.refresh()), checkable=True)
            line.addWidget(self.chips[key])
        line.addStretch(1)
        self.body.addLayout(line)
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.sliderReleased.connect(lambda: ctrl.set_focus_sound_volume(self.volume.value()))
        self.body.addLayout(row(IconButton(ctrl, "speaker", "Volume", 26), self.volume, stretch_at=1))
        self.only = QCheckBox("Only while I focus")
        self.only.toggled.connect(ctrl.set_focus_sound_only_focus)
        self.body.addWidget(self.only)
        self.body.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        s = self.ctrl.settings
        for key, button in self.chips.items():
            button.setChecked(key == s.focus_sound)
        self.volume.blockSignals(True)
        self.volume.setValue(s.focus_sound_volume)
        self.volume.blockSignals(False)
        self.only.blockSignals(True)
        self.only.setChecked(s.focus_sound_only_focus)
        self.only.blockSignals(False)


class MusicPage(Page):
    def __init__(self, ctrl):
        super().__init__(ctrl)
        self.playing = self.add(NowPlayingCard(ctrl), 1)
        self.sounds = self.add(SoundsCard(ctrl, 470))

    def refresh(self) -> None:
        self.playing.refresh()
        self.sounds.refresh()

    def tick(self) -> None:
        self.playing.tick()


# ------------------------------------------------------------------ Agents
class AgentsCard(Card):
    def __init__(self, ctrl, width: int | None = None):
        super().__init__(ctrl, "Coding agents", width)
        self.action("gear", "Agent settings", lambda: ctrl.open_settings("Agents"))
        self.list = QListWidget()
        self.body.addWidget(self.list, 1)

    def refresh(self) -> None:
        self.list.clear()
        sessions = self.ctrl.agents.live_sessions()
        t = self.ctrl.theme
        for session in sessions[:10]:
            who = f" ({session.agent_label})" if session.agent != "claude" else ""
            line = f"{session.project}{who}: {session.status_text}"
            if session.status == "working" and session.activity:
                line += f"\n{session.activity[:60]}"
            item = QListWidgetItem(line)
            color = {"working": t.focus, "waiting": t.short, "approval": t.short}.get(session.status, t.muted)
            item.setForeground(QColor(t.ink))
            item.setData(Qt.DecorationRole, QColor(color))
            self.list.addItem(item)
        if not sessions:
            empty = QListWidgetItem("No coding agents running. Connect Claude Code, Codex or Gemini in Settings.")
            empty.setFlags(Qt.NoItemFlags)
            self.list.addItem(empty)

    def tick(self) -> None:
        self.refresh()


class AgentsPage(Page):
    def __init__(self, ctrl):
        super().__init__(ctrl)
        self.agents = self.add(AgentsCard(ctrl), 1)
        reviews = Card(ctrl, "Reviews waiting", 380)
        self.reviews = QListWidget()
        self.reviews.itemClicked.connect(self._review)
        reviews.body.addWidget(self.reviews, 1)
        self.add(reviews)

    def _review(self, item: QListWidgetItem) -> None:
        index = item.data(Qt.UserRole)
        items = self.ctrl.settings.reviews
        if isinstance(index, int) and index < len(items):
            self.ctrl.start_review(items[index])

    def refresh(self) -> None:
        from . import reviews
        self.agents.refresh()
        self.reviews.clear()
        for i, item in enumerate(self.ctrl.settings.reviews):
            entry = QListWidgetItem(f"{reviews.title(item)}  ·  {reviews.lines_text(item)}")
            entry.setData(Qt.UserRole, i)
            self.reviews.addItem(entry)
        if not self.ctrl.settings.reviews:
            empty = QListWidgetItem("Nothing to review. When an agent changes files, they wait here.")
            empty.setFlags(Qt.NoItemFlags)
            self.reviews.addItem(empty)

    def tick(self) -> None:
        self.agents.tick()


# ------------------------------------------------------------------ Habits
class TodayCard(Card):
    """Today's step of your path, and today's habits to tick."""

    def __init__(self, ctrl, width: int | None = None):
        super().__init__(ctrl, "Today", width)
        self.step = label("", "secondary", wrap=True)
        self.start = chip("Start it", self._start)
        self.body.addWidget(self.step)
        self.body.addLayout(row(self.start, chip("My path", ctrl.open_path), None))
        self.list = QListWidget()
        self.list.itemChanged.connect(self._ticked)
        self.body.addWidget(self.list, 1)
        self._filling = False

    def _start(self) -> None:
        nxt = studypath.next_step(self.ctrl.settings.study_path)
        if nxt is not None:
            self.ctrl.start_step(nxt[0], nxt[1])
        else:
            self.ctrl.start_small()

    def _ticked(self, item: QListWidgetItem) -> None:
        if self._filling:
            return
        hid = item.data(Qt.UserRole)
        done = item.checkState() == Qt.Checked
        if hid and done != (hid in self.ctrl.habit_ticks.get(date.today(), set())):
            self.ctrl.toggle_habit(hid, date.today())

    def refresh(self) -> None:
        c = self.ctrl
        nxt = studypath.next_step(c.settings.study_path)
        self.step.setText(f"Step: {studypath.step_label(nxt[2])}" if nxt else "No path yet: Ask Pomo can make you one.")
        self.start.setText("Start it" if nxt else f"Just {c.settings.starter_min} minutes")
        self._filling = True
        self.list.clear()
        today = date.today()
        done = c.habit_ticks.get(today, set())
        for h in habits.current(c.settings.habits, today):
            item = QListWidgetItem(h["name"])
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if h["id"] in done else Qt.Unchecked)
            item.setData(Qt.UserRole, h["id"])
            self.list.addItem(item)
        if self.list.count() == 0:
            empty = QListWidgetItem("No habits yet: add some in the habit tracker.")
            empty.setFlags(Qt.NoItemFlags)
            self.list.addItem(empty)
        self._filling = False


class HabitsPage(Page):
    def __init__(self, ctrl):
        super().__init__(ctrl)
        from .habit_window import MedalView
        rank = Card(ctrl, "Your rank", 300)
        self.medal = MedalView(30)
        self.rank = label("", "title")
        self.points = label("", "secondary", wrap=True)
        rank.body.addLayout(row(self.medal, None))
        rank.body.addWidget(self.rank)
        rank.body.addWidget(self.points)
        rank.body.addStretch(1)
        rank.body.addWidget(chip("Habit tracker", ctrl.open_habits))
        self.add(rank)
        self.today = self.add(TodayCard(ctrl), 1)

    def refresh(self) -> None:
        st = self.ctrl.habit_standing
        self.medal.set_rank(st.rank if st else None)
        self.rank.setText(st.rank.label if st else "No rank yet")
        self.points.setText(habits.today_line(st) if st else "Track a few habits and keeping them earns a rank.")
        self.today.refresh()
