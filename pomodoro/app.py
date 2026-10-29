"""Application controller: owns the engine, storage and windows and wires them up."""
from __future__ import annotations

import os
import sys
import time
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QImage
from PySide6.QtWidgets import QApplication, QInputDialog, QMenu, QSystemTrayIcon

from . import APP_NAME, APP_VERSION, agent_hooks, audio, claude_hooks, garden, insights, keystore, media, planner
from . import applock, providers, quests, reviews, room, soundscapes, studylog, studypath, updater
from .agents import EDIT_TOOLS, AgentHub, Approval
from .config import FOCUS_SOUNDS, HOTKEYS, OUTFITS, Settings
from .hotkeys import GlobalHotkeys
from .hotkeys import parse as parse_hotkey
from .pacing import AgentPacer, finished_detail
from .applock import AppLock
from .presence import AwayWatcher, FocusGuard, idle_seconds
from .pomo import SLEEPY_AFTER
from .engine import Mode, State, TimerEngine
from .icons import app_icon, render_tray_icon
from .island import IslandWidget
from .notifier import Notifier
from .orb import OrbWidget
from .stats import fmt_clock, fmt_day, plural, streaks
from .storage import Storage
from .theme import THEMES, build_palette, build_qss, ensure_qss_assets, mode_color, theme_for
from .toast import ToastCard
from .workspace import FrontTracker, ProjectFinder
from . import winutil

GOAL_PRESETS = (4, 6, 8, 10, 12, 16)
SOUND_VOLUMES = (20, 35, 50, 65, 80)
SOUND_PREVIEW_S = 8.0  # picking a sound outside focus plays a taste of it


def styled_menu(menu: QMenu) -> QMenu:
    """Frameless + translucent so the stylesheet's rounded corners render cleanly."""
    menu.setWindowFlags(menu.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
    menu.setAttribute(Qt.WA_TranslucentBackground)
    return menu


def _action(menu: QMenu, text: str, fn, *, checked: bool | None = None, enabled: bool = True):
    act = menu.addAction(text)
    if checked is not None:
        act.setCheckable(True)
        act.setChecked(checked)
    act.setEnabled(enabled)
    act.triggered.connect(lambda _checked=False: fn())
    return act


class PomoApp(QObject):
    sound_loaded = Signal(str, object)  # a focus sound finished loading (on a worker thread): name, Sound
    voices_found = Signal(list)         # Windows' voices, looked up on a worker thread for Settings
    update_status_changed = Signal()    # checking, ready, up to date... (Settings shows it)

    def __init__(self, qapp: QApplication, data_dir: Path, demo: bool = False):
        super().__init__()
        self.qapp = qapp
        self.data_dir = data_dir
        data_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir = data_dir / "cache"
        self.settings_path = data_dir / "settings.json"
        self.settings = Settings.load(self.settings_path)
        self.storage = Storage(data_dir / "history.db", self)
        if demo:
            from .demo import seed_demo
            seed_demo(self.storage)
        self.theme = theme_for(self.settings.theme)
        self.icon = QIcon()

        self.engine = TimerEngine(self.settings, parent=self)
        self.engine.task = self.settings.current_task
        self._today = date.today()
        if self.settings.cycle_date == self._today.isoformat():
            self.engine.cycle_index = max(0, self.settings.cycle_index)
        self.today_count = self.storage.completed_focus_count(self._today)

        self._apply_style()

        self._idle_since: float | None = time.monotonic()
        self.orb = OrbWidget(self)
        self.island = IslandWidget(self)
        self.toast = ToastCard(self)
        self.dashboard = None
        self.settings_dialog = None
        self.chat = None
        self.game = None
        self.music_card = None
        self.media: media.MediaWatcher | None = None
        self.track: media.Track | None = None  # what's playing on this PC, if anything
        self.track_art: QImage | None = None
        self.agents = AgentHub(self, self)
        self._approval_queue: list[Approval] = []
        self.breathe = None
        self.agent_card = None
        self.park_card = None
        self.thoughts_window = None
        self.path_window = None
        self.study_log = None
        self.pacer = AgentPacer(self, parent=self)
        self.away = AwayWatcher(parent=self)
        self.guard = FocusGuard(self.storage, self.settings, parent=self)
        self.applock = AppLock(self.settings, parent=self)
        self.lock_card = None
        self.hotkeys = GlobalHotkeys(qapp, self)
        self.hotkey_failures: dict[str, str] = {}
        self.lifetime = self.storage.lifetime_pomodoros()
        self._prev_timer = (self.engine.mode, self.engine.state)
        self._away_paused = False

        # what you're working on: for "Where was I?" after a break, and naming sessions after your project
        self.finder = ProjectFinder(extra_folders=lambda: [a.cwd for a in self.agents.sessions.values() if a.cwd])
        self.tracker = FrontTracker(self.finder, parent=self)
        self._recap_window = None      # the window you were in when focus stopped
        self._away_since: datetime | None = None
        self._break_over_pending = False
        self._auto_task = ""           # the task Pomo named after your project, if it did
        self._focus_paused = False     # this focus session was paused (for the Laser focus quest)
        self._cheered: set[str] = set()  # the moments Pomo already cheered in this focus session
        self.whisper = None              # Pomo's little speech bubble, made when first needed
        # focus sounds, talking, the taskbar
        self.sound_player = audio.LoopPlayer()
        self._sounds: dict[str, object] = {}  # name -> soundscapes.Sound, once decoded or made
        self._sound_loading: set[str] = set()
        self._sound_now: str | None = None
        self._preview_until = 0.0
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._sync_focus_sound)
        self.speaker = None
        self.talk = None
        self.voices: list | None = None  # Windows' voices, once looked up
        self.taskbar = None
        self._taskbar_dismissed = False
        self._fresh_review: dict | None = None
        # study coach
        self._path_task = ""             # the task Pomo took from your path (so the next step can replace it)
        self._starter_pending = False    # a "just 10 minutes" session is starting
        self._starter = False            # the focus session running is one
        self._step_news = None           # (milestone, step, the step) a finished session counted toward
        # updates from GitHub
        self.update_mode = updater.install_mode()
        self._update_worker = None
        self._update_error = ""
        self._updating = False
        self._update_manual = False      # the check under way was "Check now"
        self._update_snooze = 0.0        # an unanswered update card waits until then (monotonic seconds)
        self._update_toasted = ""        # the version a Windows notification already announced (this run)
        self._update_timer = QTimer(self)
        self._update_timer.setInterval(updater.CHECK_EVERY_MS)
        self._update_timer.timeout.connect(self.check_for_updates)
        self._quest_pending: tuple[list, list] = ([], [])  # finished quests and unlocked outfits not shown yet
        self._roll_quests()

        self.tray: QSystemTrayIcon | None = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(self.icon, self)
            self._tray_menu = styled_menu(QMenu())
            self._tray_menu.aboutToShow.connect(lambda: self._populate_menu(self._tray_menu))
            self._populate_menu(self._tray_menu)
            self.tray.setContextMenu(self._tray_menu)
            self.tray.activated.connect(self._on_tray_activated)
            self.tray.messageClicked.connect(self._on_message_clicked)
            self.tray.show()
        self.notifier = Notifier(self.settings, self.cache_dir, self.tray, self)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self._save_now)
        self._house_timer = QTimer(self)
        self._house_timer.setInterval(30_000)
        self._house_timer.timeout.connect(self._housekeeping)
        self._house_timer.start()
        self._idle_nudged = False
        self._tray_second = -1

        self.engine.session_logged.connect(self._on_session_logged)
        self.storage.changed.connect(self._on_history_changed)
        self.engine.finished.connect(self._on_finished)
        self.engine.changed.connect(self._on_engine_changed)
        self.engine.ticked.connect(self._on_tick)
        for view in (self.orb, self.island):
            view.dashboard_requested.connect(self.open_dashboard)
            view.settings_requested.connect(self.open_settings)
            view.chat_requested.connect(self.open_chat)
            view.menu_requested.connect(self._show_view_menu)
            view.interacted.connect(self._on_view_interacted)
        self.orb.moved.connect(self.save_position)
        self.agents.approval_requested.connect(self._on_approval_requested)
        self.agents.approval_closed.connect(self._on_approval_closed)
        self.agents.turn_finished.connect(self._on_agent_finished)
        self.agents.needs_input.connect(self._on_agent_needs_input)
        self.agents.review_ready.connect(self._on_review_ready)
        self.agents.changed.connect(self._on_agents_changed)
        self.sound_loaded.connect(self._on_sound_loaded)
        self.agents.changed.connect(self.island._update_targets)
        qapp.aboutToQuit.connect(self._on_quit)
        self.pacer.suggest_break.connect(self._on_suggest_break)
        self.pacer.suggest_focus.connect(self._on_suggest_focus)
        self.away.went_away.connect(self._on_went_away)
        self.away.came_back.connect(self._on_came_back)
        self.guard.nudge.connect(self._on_nudge)
        self.applock.blocked.connect(self._on_app_blocked)
        self.hotkeys.triggered.connect(self._on_hotkey)
        qapp.screenRemoved.connect(lambda *_: QTimer.singleShot(400, self.view.ensure_on_screen))
        qapp.primaryScreenChanged.connect(lambda *_: QTimer.singleShot(400, self.view.ensure_on_screen))

        self._apply_display_mode()
        self._on_engine_changed()
        self._sync_integrations()
        self._sync_media()
        self._sync_hotkeys()
        self._sync_presence()
        self.tracker.set_active(True)
        self._sync_focus_sound()
        QTimer.singleShot(1200, self._sync_taskbar)  # after the timer window, so it doesn't steal the first look
        QTimer.singleShot(2000, self._check_quests)  # today's history may already have met a quest
        QTimer.singleShot(2500, self._after_update)  # just updated? say so
        QTimer.singleShot(4000, self._maybe_morning_card)  # today's step
        QTimer.singleShot(updater.FIRST_CHECK_MS, self.check_for_updates)
        self._update_timer.start()
        if not self.settings.welcomed:
            QTimer.singleShot(1800, self._welcome)

    def _welcome(self) -> None:
        """First launch: Pomo says hi from the island and offers the orb instead."""
        self.settings.welcomed = True
        self.save_settings()
        if not self.view.isVisible() or self._approval_on_screen():
            return
        accent = mode_color(self.theme, self.settings, "focus")
        if self.settings.display_mode == "island":
            body = ("I live in this little island at the top of your screen. Hover me for the controls, "
                    "and feel free to poke me. Prefer a round timer you can drag anywhere?")
            actions = [("Keep the island", lambda: None, True),
                       ("Switch to orb", lambda: self.set_display_mode("orb"), False)]
            self.island.pin(30.0)
        else:
            body = ("I sit on top of your timer. Hover it for the controls, and feel free to poke me. "
                    "Prefer a slim island at the top of your screen?")
            actions = [("Keep the orb", lambda: None, True),
                       ("Switch to island", lambda: self.set_display_mode("island"), False)]
        self.view.pomo.hello()
        self.toast.show_message("Hi, I'm Pomo!", body, accent, actions, self.view.anchor_rect_global(),
                                timeout_ms=30000)

    def _sync_integrations(self) -> None:
        """Keep Windows start-up and Claude Code hooks pointing at this copy of Pomo.

        Pomo may have moved, been upgraded, or been renamed from FocusOrb since they were set up.
        """
        frozen = getattr(sys, "frozen", False)
        on = self.settings.start_with_windows or winutil.starts_with_windows()
        if on and frozen:  # a source checkout must never take over the installed app's entry
            winutil.set_start_with_windows(True)
        if on != self.settings.start_with_windows:
            self.settings.start_with_windows = on
            self.save_settings()
        if frozen:
            try:
                claude_hooks.refresh()
            except (claude_hooks.HookConfigError, OSError):
                pass
            for agent in agent_hooks.AGENTS:
                try:
                    agent_hooks.refresh(agent)
                except (claude_hooks.HookConfigError, OSError):
                    pass

    def _uses_agents(self) -> bool:
        """Does this person use coding agents with Pomo? (Agent quests are only picked if so.)"""
        if self.settings.reviews or self.agents.sessions:
            return True
        try:
            if claude_hooks.status() != "off":
                return True
            return any(agent_hooks.status(a) != "off" for a in agent_hooks.AGENTS)
        except OSError:
            return False

    def set_theme(self, key: str) -> None:
        new = self.settings.copy()
        new.theme = key
        new.clamp()
        self.apply_settings(new)

    def outfit_unlocked(self, key: str) -> bool:
        return garden.is_unlocked(key, self.lifetime, self.settings.quests_total)

    def set_outfit(self, key: str) -> None:
        if not self.outfit_unlocked(key):
            return
        self.settings.pomo_outfit = key
        self.settings.clamp()
        self.save_settings()
        self.view.pomo.outfit = key
        self.view.pomo.hop(1.8)  # a little twirl to show it off

    # ------------------------------------------------------------ study coach
    def path_changed(self, path: dict | None = None) -> None:
        """The path was made, edited or ticked: save it, and today's step becomes the task when nothing else is."""
        s, e = self.settings, self.engine
        if path is not None:
            s.study_path = path
        s.study_path = studypath.clean(s.study_path)
        self.save_settings()
        nxt = studypath.next_step(s.study_path)
        idle = e.state not in (State.RUNNING, State.PAUSED)
        if nxt is not None and idle and not s.task_queue and (not e.task or e.task == self._path_task):
            self._use_step(nxt[2]["title"])
        self._refresh_study()

    def _use_step(self, title: str) -> None:
        self.set_task(title)
        self._path_task = title

    def _refresh_study(self) -> None:
        if self.path_window is not None and self.path_window.isVisible():
            self.path_window.refresh()
        if self.dashboard is not None and self.dashboard.isVisible():
            self.dashboard.refresh_study()

    def complete_step(self, mi: int, si: int, done: bool = True) -> None:
        """Tick a step off your path (or back on)."""
        path = self.settings.study_path
        try:
            step = path["milestones"][mi]["steps"][si]
        except (KeyError, IndexError):
            return
        milestone_done = studypath.set_done(path, mi, si, done, date.today())
        if self.engine.task == step["title"]:
            self._path_task = step["title"]  # so the next step takes over as the task
        self.path_changed()
        if not done:
            return
        self.quest_event("path_step", announce=False)
        self.view.celebrate(big=milestone_done)
        nxt = studypath.next_step(path)
        s = self.settings
        if s.popup_card and self.view.isVisible() and not self._approval_on_screen() and not self.toast.showing:
            if milestone_done:
                title = f"Milestone done: {path['milestones'][mi]['title']}!"
            else:
                title = "Step done!"
            body = f"Next on your path: {nxt[2]['title']}." if nxt else "That was the last step on your path. Wow!"
            news = self._quest_news(self._take_quest_news())[0]
            if news:
                body += f"\n{news}"
            actions = ([(f"Start with {s.starter_min} minutes", lambda: self.start_step(nxt[0], nxt[1], True), True),
                        ("Later", lambda: None, False)] if nxt else [("Yay!", lambda: None, True)])
            self.toast.show_message(title, body, mode_color(self.theme, s, "long"), actions,
                                    self.view.anchor_rect_global(), timeout_ms=20000,
                                    extras=[("My path", self.open_path)])
        else:
            QTimer.singleShot(700, self._flush_quest_news)

    def start_step(self, mi: int, si: int, small: bool = False) -> None:
        """Work on a step now: it becomes the task, and a session starts (a short starter one if small)."""
        try:
            title = self.settings.study_path["milestones"][mi]["steps"][si]["title"]
        except (KeyError, IndexError):
            return
        self._use_step(title)
        if small:
            self.start_small()
        else:
            self._start_focus_now()

    def start_small(self) -> None:
        """"Just 10 minutes": a short focus session, because starting is the hard part."""
        e = self.engine
        if e.mode is Mode.FOCUS and e.state is State.RUNNING:
            return
        if e.mode is not Mode.FOCUS or e.state is State.PAUSED:
            e.switch_mode(Mode.FOCUS)
        e.set_duration(self.settings.starter_min * 60)
        self._starter_pending = True
        e.start()

    def _keep_going(self) -> None:
        """After a starter session: straight into a full one, same task."""
        e = self.engine
        if e.mode is not Mode.FOCUS or e.state in (State.RUNNING, State.PAUSED):
            e.switch_mode(Mode.FOCUS)
        e.start()

    def _maybe_morning_card(self) -> None:
        """The first card of the day: today's step and a small way to start (or, now and then, an offer of a path)."""
        s, e = self.settings, self.engine
        now = datetime.now()
        today = now.date()
        if not s.morning_card or s.morning_date == today.isoformat() or now.hour < 5:
            return
        if e.state is State.RUNNING or self.toast.showing or self._approval_on_screen():
            return
        if not (s.popup_card and self.view.isVisible()) or idle_seconds() > updater.AWAY_S:
            return
        path = s.study_path
        first_time = not s.morning_date
        s.morning_date = today.isoformat()
        self.save_settings()
        accent = mode_color(self.theme, s, "focus")
        anchor = self.view.anchor_rect_global()
        current, _best = streaks(self.storage.sessions(since=today - timedelta(days=60)), today)
        streak = f" {current} days of focus in a row: keep it going!" if current >= 2 else ""
        from .ai import PATH_START_PROMPT

        if path and studypath.review_due(path, today):
            done, total = studypath.progress(path)
            self.view.pomo.hello()
            self.toast.show_message(
                f"{path['weeks']} weeks on your path",
                f"“{path['goal']}”: {done} of {plural(total, 'step')} done. Keep going for "
                f"{studypath.EXTEND_WEEKS} more weeks, or look back with Ask Pomo and maybe change direction?",
                accent, [("Keep going", self._keep_path, True), ("Look back", lambda: self._rethink_path(), False)],
                anchor, timeout_ms=60000, extras=[("My path", self.open_path)])
            return
        nxt = studypath.next_step(path) if path else None
        hello = "Good morning!" if now.hour < 12 else "Hello again!"
        if nxt is not None:
            mi, si, st = nxt
            week, weeks = studypath.week_of(path, today)
            self.view.pomo.hello()
            self.toast.show_message(
                f"{hello} Today's step:",
                f"{studypath.step_label(st)}\nWeek {min(week, weeks)} of {weeks} on “{path['goal']}”.{streak}\n"
                f"Phone in another room? Then start with just {s.starter_min} minutes.",
                accent, [(f"Start {s.starter_min} minutes", lambda: self.start_step(mi, si, True), True),
                         ("Later", lambda: None, False)],
                anchor, timeout_ms=60000,
                extras=[("Full session", lambda: self.start_step(mi, si, False)), ("My path", self.open_path)])
        elif not path and (first_time or today.weekday() == 0):  # no path: offer one now and then, not daily
            self.toast.show_message(
                f"{hello} What will you work on?",
                "Not sure where to start? Ask Pomo to make you a path: one goal, small steps, and you'll always "
                f"know what's next.{streak}",
                accent, [("Make my path", lambda: self.ask_pomo(PATH_START_PROMPT), True), ("Not now", lambda: None, False)],
                anchor, timeout_ms=45000, extras=[(f"Just {s.starter_min} minutes", self.start_small)])

    def _keep_path(self) -> None:
        studypath.keep_going(self.settings.study_path, date.today())
        self.path_changed()

    def _rethink_path(self) -> None:
        from .ai import PATH_RETHINK_PROMPT

        studypath.snooze_review(self.settings.study_path, date.today())
        self.path_changed()
        self.ask_pomo(PATH_RETHINK_PROMPT)

    def _maybe_log_prompt(self) -> None:
        """In the evening, after a day with some focus: a few sentences in English about what you learned."""
        s, e = self.settings, self.engine
        now = datetime.now()
        today = now.date()
        if not s.log_prompt or s.log_prompt_date == today.isoformat() or now.hour < (s.review_hour or 19):
            return
        if self.today_count < 1 or e.state is State.RUNNING or self.toast.showing or self._approval_on_screen():
            return
        if not (s.popup_card and self.view.isVisible()) or idle_seconds() > updater.AWAY_S:
            return
        s.log_prompt_date = today.isoformat()
        self.save_settings()
        if today in self.storage.log_days():
            return  # already written
        days = self.storage.log_days()
        n = studylog.streak(days, today)
        streak = f" Keep your {n}-day streak going." if n else ""
        self.toast.show_message(
            "Study log",
            f"Write two or three sentences in English about what you learned today. A little every day is how "
            f"your English and your memory grow.{streak}",
            mode_color(self.theme, s, "long"), [("Write it", self.open_study_log, True), ("Not today", lambda: None, False)],
            self.view.anchor_rect_global(), timeout_ms=45000)

    def add_study_log(self, text: str):
        entry = self.storage.add_log(text)
        self.quest_event("log")
        self._refresh_study()
        return entry

    def open_path(self) -> None:
        if self.path_window is None:
            from .path_window import PathWindow
            self.path_window = PathWindow(self)
        self._show_window(self.path_window)

    def open_study_log(self) -> None:
        if self.study_log is None:
            self.study_log = studylog.StudyLogWindow(self)
        self._show_window(self.study_log)

    @staticmethod
    def _show_window(window) -> None:
        window.show()
        window.setWindowState(window.windowState() & ~Qt.WindowMinimized)
        window.raise_()
        window.activateWindow()

    # ------------------------------------------------------------ Pomo's room
    def room_progress(self) -> tuple[float, int, int, list[str]]:
        """Focus hours, quests finished, Perfect days, and the room items they've earned."""
        s = self.settings
        hours = self.storage.lifetime_focus_sec() / 3600
        perfect = int(s.badges.get(quests.PERFECT_DAY, 0))
        return hours, s.quests_total, perfect, room.earned_keys(hours, s.quests_total, perfect)

    def place_in_room(self, slot: str, key: str) -> None:
        self.settings.room[slot] = key
        self.save_settings()

    def open_room(self) -> None:
        self.open_dashboard()
        self.dashboard.show_room()

    def _room_news(self) -> str:
        """'New for Pomo's room: Monstera!' for items earned since the last news (they move straight in)."""
        s = self.settings
        fresh = room.newly_earned(self.room_progress()[3], s.room_seen)
        if not fresh:
            return ""
        room.move_in(s, fresh)
        self.save_settings()
        return room.news_text(fresh)

    def current_outfit(self) -> str:
        """What Pomo wears right now: dressed for what you're doing, or your pick when nothing's on."""
        s = self.settings
        if s.outfit_follows:
            for outfit in self._activity_outfits():  # all from the wardrobe or the garden, none quest rewards
                if garden.is_unlocked(outfit, self.lifetime):
                    return outfit
        return s.pomo_outfit

    def _activity_outfits(self):
        """Outfits for what's going on, most specific first."""
        def shown(window) -> bool:
            return window is not None and window.isVisible() and not window.isMinimized()

        if shown(self.game):
            yield "party"
        if shown(self.breathe):
            yield "flowercrown"
        if shown(self.chat):
            yield "beret"
        if shown(self.dashboard):
            yield "glasses"
        if self.pomo_listening():
            yield "headphones"
        if self.agent_signal() == "busy":
            yield "hardhat"
        e = self.engine
        if e.state is State.RUNNING:
            yield "sunhat" if e.mode.is_break else "headband"
        elif self.idle_seconds() >= SLEEPY_AFTER:
            yield "nightcap"

    def feed_pomo(self) -> None:
        if self.settings.treats <= 0:
            return
        self.settings.treats -= 1
        self.settings.treats_fed += 1
        self.save_settings()
        self.view.pomo.munch()
        self.view.update()

    def open_game(self, key: str | None = None) -> None:
        """The games window: the picker, or straight into one game."""
        if self.game is None:
            from .game import GameWindow
            self.game = GameWindow(self)
        self.game.open(key)
        self.game.show()
        self.game.setWindowState(self.game.windowState() & ~Qt.WindowMinimized)
        self.game.raise_()
        self.game.activateWindow()

    # ----------------------------------------------------------------- music
    def _sync_media(self) -> None:
        """Watch what's playing while music controls are on (and Windows supports it)."""
        want = self.settings.music_controls and media.available()
        if want and self.media is None:
            self.media = media.MediaWatcher(self)
            self.media.changed.connect(self._on_track)
            self.media.start()
        elif not want and self.media is not None:
            self.media.changed.disconnect(self._on_track)  # drop any update still on its way
            self.media.stop()
            self.media = None
            self._on_track(None)

    def _on_track(self, track) -> None:
        if track is not None and self.media is None:
            return  # music controls were just turned off
        previous, self.track = self.track, track
        if track is None or track.art is None:
            self.track_art = None
        elif previous is None or previous.art != track.art:
            image = QImage.fromData(track.art)
            self.track_art = None if image.isNull() else image
        started = track is not None and track.playing and (
            previous is None or not previous.playing or previous.key != track.key)
        if started and self.pomo_listening():
            self.view.pomo.new_song()
        self.island.on_music_changed()
        self.orb.update()
        if self.music_card is not None:
            self.music_card.refresh()

    def music_playing(self) -> bool:
        return self.track is not None and self.track.playing

    def pomo_listening(self) -> bool:
        """Pomo puts on headphones: for your music, or for Pomo's own focus sounds."""
        s = self.settings
        return s.pomo_listens and ((s.music_controls and self.music_playing()) or self.focus_sound_playing())

    def game_played(self, key: str) -> None:
        """A round of a mini game ended. Off focus time, it counts toward the Playtime quest."""
        e = self.engine
        if not (e.mode is Mode.FOCUS and e.state is State.RUNNING):
            self.quest_event("play")

    # ------------------------------------------------------------ focus sounds
    def focus_sound_playing(self) -> bool:
        return self._sound_now is not None and self.sound_player.playing

    def _sound_volume(self) -> float:
        return (self.settings.focus_sound_volume / 100) ** 1.25

    def _wanted_sound(self) -> str | None:
        """The focus sound that should play right now, if any."""
        s, e = self.settings, self.engine
        if s.focus_sound == "off":
            return None
        if not s.focus_sound_only_focus or time.monotonic() < self._preview_until:
            return s.focus_sound
        return s.focus_sound if e.mode is Mode.FOCUS and e.state is State.RUNNING else None

    def _sync_focus_sound(self) -> None:
        want = self._wanted_sound()
        if want is None:
            if self._sound_now is not None:
                self._sound_now = None
                self.sound_player.stop(fade_out_s=1.5)
                self.view.update()
            return
        if want == self._sound_now and self.sound_player.playing:
            self.sound_player.set_volume(self._sound_volume())
            return
        sound = self._sounds.get(want)
        if sound is None:  # decoded or made the first time it's wanted, off the UI thread
            if want not in self._sound_loading:
                self._sound_loading.add(want)
                import threading

                def load(name=want, cache=self.cache_dir):
                    try:
                        made = soundscapes.load(cache, name)
                    except Exception:  # a full disk or the like: no sound, no crash
                        made = None
                    self.sound_loaded.emit(name, made)

                threading.Thread(target=load, name="pomo-sound-maker", daemon=True).start()
            return
        self._sound_now = want
        self.sound_player.play(sound.samples, sound.rate, self._sound_volume(), fade_in_s=2.5,
                               channels=sound.channels)
        if self.pomo_listening():
            self.view.pomo.new_song()

    def _on_sound_loaded(self, name: str, sound) -> None:
        self._sound_loading.discard(name)
        if sound is not None:
            self._sounds[name] = sound
            self._sync_focus_sound()

    def set_focus_sound(self, name: str) -> None:
        s = self.settings
        s.focus_sound = name if name in FOCUS_SOUNDS else "off"
        self.save_settings()
        e = self.engine
        focusing = e.mode is Mode.FOCUS and e.state is State.RUNNING
        if s.focus_sound != "off" and s.focus_sound_only_focus and not focusing:
            self._preview_until = time.monotonic() + SOUND_PREVIEW_S  # a taste now; the rest when you focus
            self._preview_timer.start(int(SOUND_PREVIEW_S * 1000) + 50)
        else:
            self._preview_until = 0.0
        if self._sound_now is not None and self._sound_now != s.focus_sound:
            self.sound_player.stop(fade_out_s=0.6)
            self._sound_now = None
        self._sync_focus_sound()

    def set_focus_sound_volume(self, volume: int) -> None:
        self.settings.focus_sound_volume = volume
        self.settings.clamp()
        self.save_settings()
        self._sync_focus_sound()

    def set_focus_sound_only_focus(self, only: bool) -> None:
        self.settings.focus_sound_only_focus = only
        self.save_settings()
        self._sync_focus_sound()

    def fill_sounds_menu(self, menu: QMenu) -> None:
        s = self.settings
        for key, label in FOCUS_SOUNDS.items():
            _action(menu, label, lambda k=key: self.set_focus_sound(k), checked=key == s.focus_sound)
        menu.addSeparator()
        _action(menu, "Only while I focus", lambda: self.set_focus_sound_only_focus(not s.focus_sound_only_focus),
                checked=s.focus_sound_only_focus)
        volume = styled_menu(menu.addMenu(f"Volume: {s.focus_sound_volume}%"))
        for v in SOUND_VOLUMES:
            _action(volume, f"{v}%", lambda v=v: self.set_focus_sound_volume(v), checked=v == s.focus_sound_volume)

    def open_sounds_menu(self, pos) -> None:
        menu = styled_menu(QMenu())
        self.fill_sounds_menu(menu)
        menu.exec(pos)
        menu.deleteLater()

    def media_command(self, name: str) -> None:
        if self.media is None or self.track is None:
            return
        self.media.command(name)
        if name == "toggle":  # show it right away; the watcher confirms a moment later
            self._on_track(replace(self.track, playing=not self.track.playing))

    def open_music_card(self) -> None:
        if self.track is None:
            return
        if self.music_card is None:
            from .music_card import MusicCard
            self.music_card = MusicCard(self)
        self.music_card.show_near(self.view.anchor_rect_global())

    def set_start_with_windows(self, on: bool) -> None:
        if winutil.set_start_with_windows(on):
            self.settings.start_with_windows = on
            self.save_settings()

    # ----------------------------------------------------------------- views
    @property
    def view(self):
        """The timer window in use: the floating orb or the top-of-screen island."""
        return self.island if self.settings.display_mode == "island" else self.orb

    def _apply_display_mode(self) -> None:
        active, other = (self.island, self.orb) if self.settings.display_mode == "island" else (self.orb, self.island)
        other.hide()
        active.place()
        active.show()
        active.hello()

    def set_display_mode(self, mode: str) -> None:
        if mode == self.settings.display_mode:
            return
        self.settings.display_mode = mode
        self.settings.clamp()
        self.save_settings()
        self.toast.dismiss()
        self._apply_display_mode()

    def agent_signal(self) -> str | None:
        hub = getattr(self, "agents", None)
        return hub.signal() if hub is not None else None

    def _on_view_interacted(self) -> None:
        # clicking the timer closes an ordinary card, never a pending approval
        if not isinstance(self.toast.tag, Approval):
            self.toast.dismiss()

    def idle_seconds(self) -> float:
        """How long no timer has been running (0 while one runs)."""
        return 0.0 if self._idle_since is None else time.monotonic() - self._idle_since

    # ----------------------------------------------------------------- style
    def _apply_style(self) -> None:
        accent = mode_color(self.theme, self.settings, "focus")
        assets = ensure_qss_assets(self.theme, self.cache_dir)
        self.qapp.setPalette(build_palette(self.theme, accent))
        self.qapp.setStyleSheet(build_qss(self.theme, accent, assets))
        self.icon = app_icon()
        self.qapp.setWindowIcon(self.icon)

    # -------------------------------------------------------------- settings
    def save_settings(self) -> None:
        self._save_timer.start()

    def _save_now(self) -> None:
        try:
            self.settings.save(self.settings_path)
        except OSError:
            pass

    def save_position(self) -> None:
        center = self.orb.circle_center_global()
        self.settings.orb_cx, self.settings.orb_cy = center.x(), center.y()
        self.settings.orb_x = self.settings.orb_y = None
        self.save_settings()

    def set_default_minutes(self, mode: Mode, minutes: int) -> None:
        attr = {Mode.FOCUS: "focus_min", Mode.SHORT: "short_min", Mode.LONG: "long_min"}[mode]
        setattr(self.settings, attr, minutes)
        self.settings.clamp()
        self.engine.apply_settings()
        self.save_settings()

    def set_goal(self, goal: int) -> None:
        self.settings.daily_goal = goal
        self.settings.clamp()
        self.save_settings()
        self.view.update()
        self._update_tray()
        if self.dashboard is not None:
            self.dashboard.refresh()

    def apply_settings(self, new: Settings) -> None:
        autostart_changed = new.start_with_windows != self.settings.start_with_windows
        mode_changed = new.display_mode != self.settings.display_mode
        self.settings.update_from(new)
        if autostart_changed and not winutil.set_start_with_windows(self.settings.start_with_windows):
            self.settings.start_with_windows = False
        self.theme = theme_for(self.settings.theme)
        self._apply_style()
        self.engine.apply_settings()
        self._sync_media()
        self._sync_hotkeys()
        self._sync_presence()
        self._sync_focus_sound()
        self._taskbar_dismissed = False
        self._sync_taskbar()
        if self.taskbar is not None:
            self.taskbar.refresh(force=True)
        if self.speaker is not None and not self.settings.voice_replies:
            self.speaker.stop()
        self.orb.apply_settings()
        self.island.apply_settings()
        if mode_changed:
            self.toast.dismiss()
            self._apply_display_mode()
        self.toast.update()
        self._update_tray()
        if self.chat is not None and self.chat.worker is None:
            visible, geometry = self.chat.isVisible(), self.chat.geometry()
            self.chat.close()
            self.chat.deleteLater()
            self.chat = None
            if visible:
                self.open_chat()
                self.chat.setGeometry(geometry)
        if self.dashboard is not None:
            visible, geometry = self.dashboard.isVisible(), self.dashboard.geometry()
            self.dashboard.close()
            self.dashboard.deleteLater()
            self.dashboard = None
            if visible:
                self.open_dashboard()
                self.dashboard.setGeometry(geometry)
        self.save_settings()

    # --------------------------------------------------------------- windows
    def open_dashboard(self) -> None:
        if self.dashboard is None:
            from .dashboard import Dashboard
            self.dashboard = Dashboard(self)
        self.dashboard.show()
        self.dashboard.setWindowState(self.dashboard.windowState() & ~Qt.WindowMinimized)
        self.dashboard.raise_()
        self.dashboard.activateWindow()

    @property
    def ai_key_path(self) -> Path:
        """Where the key for the provider in use is kept (encrypted)."""
        return providers.key_file(self.data_dir, self.settings.ai_provider)

    def key_path_for(self, provider_key: str) -> Path:
        return providers.key_file(self.data_dir, provider_key)

    def ai_key(self, provider_key: str | None = None) -> str | None:
        """The saved key, or None (for Claude, the SDK then uses ANTHROPIC_API_KEY or an `ant` login)."""
        return keystore.load_key(providers.key_file(self.data_dir, provider_key or self.settings.ai_provider))

    def env_ai_key(self, provider_key: str | None = None) -> str | None:
        """A key from the environment for the OpenAI-compatible providers (OPENAI_API_KEY and so on)."""
        key = provider_key or self.settings.ai_provider
        return None if key == "anthropic" else providers.env_key(key)

    def has_ai_key(self, provider_key: str | None = None) -> bool:
        key = provider_key or self.settings.ai_provider
        return bool(self.ai_key(key) or providers.env_key(key))

    def open_chat(self) -> None:
        if self.chat is None:
            from .chat import ChatWindow
            self.chat = ChatWindow(self)
        self.chat.show()
        self.chat.setWindowState(self.chat.windowState() & ~Qt.WindowMinimized)
        self.chat.raise_()
        self.chat.activateWindow()

    def open_settings(self, tab: str | None = None) -> None:
        if self.settings_dialog is not None:
            if tab:
                self.settings_dialog.show_tab(tab)
            self.settings_dialog.raise_()
            self.settings_dialog.activateWindow()
            return
        from .settings_dialog import SettingsDialog
        self.settings_dialog = SettingsDialog(self)
        if tab:
            self.settings_dialog.show_tab(tab)
        self.settings_dialog.finished.connect(lambda _result: setattr(self, "settings_dialog", None))
        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()

    def show_timer(self) -> None:
        self.view.show()
        self.view.ensure_on_screen()
        self.view.raise_()

    def _on_message_clicked(self) -> None:
        """A Windows notification was clicked: bring the timer up, and do what that notification was about."""
        self.show_timer()
        QTimer.singleShot(250, self.notifier.clicked)  # once the timer is up, so a card can sit beside it

    def hide_timer(self) -> None:
        if self.tray is not None:
            self.view.hide()
            self.toast.dismiss()

    def edit_task(self) -> None:
        items = [self.engine.task] if self.engine.task else []
        project = self.tracker.recent_project()
        if project is not None and project.task not in items:
            items.append(project.task)  # the project in your editor, ready to pick
        items += [t for t in self.settings.recent_tasks if t not in items]
        text, ok = QInputDialog.getItem(None, "Current task", "What are you focusing on?", items or [""], 0, True)
        if ok:
            self.settings.remember_task(text)
            self.engine.task = self.settings.current_task
            self.save_settings()
            self.view.update()

    def set_task(self, task: str) -> None:
        self.settings.remember_task(task)
        self.engine.task = self.settings.current_task
        self._auto_task = ""
        self.save_settings()
        self.view.update()

    def plan_changed(self) -> None:
        """The plan was set or changed: its first task becomes the current one."""
        head = planner.current_item(self.settings)
        if head is not None and head["task"] != self.engine.task:
            self.set_task(head["task"])
        else:
            self.save_settings()

    def next_planned_task(self) -> None:
        if self.settings.task_queue:
            self.settings.task_queue.pop(0)
            self.plan_changed()

    def clear_plan(self) -> None:
        self.settings.task_queue = []
        self.save_settings()

    def clear_task(self) -> None:
        self.settings.current_task = ""
        self.engine.task = ""
        self.save_settings()
        self.view.update()

    def _custom_goal(self) -> None:
        value, ok = QInputDialog.getInt(None, "Daily goal", "Pomodoros to finish per day:", self.settings.daily_goal, 1, 48)
        if ok:
            self.set_goal(value)

    def quit(self) -> None:
        self.qapp.quit()

    # ------------------------------------------------------------------ menu
    def _populate_menu(self, menu: QMenu) -> None:
        menu.clear()
        e = self.engine
        label = {State.RUNNING: "Pause", State.PAUSED: "Resume"}.get(e.state, "Start")
        _action(menu, f"{label}\tSpace", e.toggle)
        _action(menu, "Reset\tR", e.reset)
        _action(menu, f"Skip to {e.skip_target().label.lower()}\tS", e.skip)
        menu.addSeparator()

        switch = styled_menu(menu.addMenu("Switch to"))
        for mode in Mode:
            _action(switch, mode.label, lambda m=mode: e.switch_mode(m), checked=mode is e.mode)
        add = styled_menu(menu.addMenu("Add time"))
        for minutes in (1, 5, 10):
            _action(add, f"+{minutes} min", lambda m=minutes: e.adjust(m * 60))
        _action(add, "-1 min", lambda: e.adjust(-60))

        if e.task:
            task = styled_menu(menu.addMenu(f"Task: {e.task[:28]}"))
            _action(task, "Change task…", self.edit_task)
            _action(task, "Clear task", self.clear_task)
        else:
            _action(menu, "Set task…", self.edit_task)
        if self.settings.task_queue:
            plan = styled_menu(menu.addMenu(f"Plan ({plural(len(self.settings.task_queue), 'task')})"))
            for i, item in enumerate(self.settings.task_queue[:10]):
                line = f"{item['task'][:36]}  ·  {plural(item['pomodoros'], 'pomodoro')}"
                _action(plan, ("Now: " if i == 0 else "") + line, lambda: None, enabled=False)
            plan.addSeparator()
            _action(plan, "Skip to the next task", self.next_planned_task)
            _action(plan, "Clear the plan", self.clear_plan)
        thoughts = len(self.storage.thoughts())
        _action(menu, f"Park a thought…\t{self.settings.hotkey_park}" if self.settings.hotkeys_on else "Park a thought…",
                self.park_thought)
        if thoughts:
            _action(menu, f"Parked thoughts ({thoughts})…", self.open_thoughts)

        goal = styled_menu(menu.addMenu(f"Daily goal: {self.settings.daily_goal}"))
        for g in GOAL_PRESETS:
            _action(goal, plural(g, "pomodoro"), lambda g=g: self.set_goal(g), checked=g == self.settings.daily_goal)
        goal.addSeparator()
        _action(goal, "Custom…", self._custom_goal)
        style = styled_menu(menu.addMenu("Timer style"))
        _action(style, "Floating orb", lambda: self.set_display_mode("orb"),
                checked=self.settings.display_mode == "orb")
        _action(style, "Top island", lambda: self.set_display_mode("island"),
                checked=self.settings.display_mode == "island")
        themes = styled_menu(menu.addMenu("Theme"))
        for theme in THEMES.values():
            _action(themes, theme.label, lambda k=theme.key: self.set_theme(k), checked=theme.key == self.settings.theme)
        outfits = styled_menu(menu.addMenu("Pomo's outfit"))
        for key, label in OUTFITS.items():
            if self.outfit_unlocked(key):
                _action(outfits, label, lambda k=key: self.set_outfit(k), checked=key == self.settings.pomo_outfit)
            else:
                _action(outfits, f"{label}  ({garden.unlock_hint(key)})", lambda: None, enabled=False)
        _action(menu, "Pomo's room…", self.open_room)
        menu.addSeparator()

        _action(menu, "Ask Pomo…", self.open_chat)
        talk = self.settings.hotkey_talk if self.settings.hotkeys_on and self.settings.hotkey_talk else ""
        _action(menu, f"Talk to Pomo\t{talk}" if talk else "Talk to Pomo", lambda: self.start_talk(None))
        study = styled_menu(menu.addMenu("Study"))
        nxt = studypath.next_step(self.settings.study_path)
        if nxt is not None:
            _action(study, f"Today: {studypath.step_label(nxt[2])}", lambda: None, enabled=False)
            _action(study, f"Start today's step with just {self.settings.starter_min} minutes",
                    lambda: self.start_step(nxt[0], nxt[1], small=True))
        else:
            _action(study, f"Start with just {self.settings.starter_min} minutes", self.start_small)
        _action(study, "My path…", self.open_path)
        _action(study, "Study log…", self.open_study_log)
        _action(menu, "Play a game…", self.open_game)
        _action(menu, "Breathe with Pomo…", self.open_breathe)
        sound = self.settings.focus_sound
        sounds = styled_menu(menu.addMenu(f"Focus sounds: {FOCUS_SOUNDS[sound]}" if sound != "off" else "Focus sounds"))
        self.fill_sounds_menu(sounds)
        if self.settings.quests_on and self.settings.quests:
            done = len(self.settings.quests_done)
            quest_menu = styled_menu(menu.addMenu(f"Today's quests ({done}/{len(self.settings.quests)})"))
            records = self.storage.sessions(since=date.today())
            for key in self.settings.quests:
                quest = quests.QUESTS.get(key)
                if quest is None:
                    continue
                mark = "✓ " if key in self.settings.quests_done else ""
                _action(quest_menu, f"{mark}{quests.status_line(quest, self.settings, records, date.today())}"
                        f"  ·  {quest.goal}", lambda: None, enabled=False)
            quest_menu.addSeparator()
            _action(quest_menu, "Badges on the dashboard…", self.open_dashboard)
        if self.track is not None:
            t = self.track
            title = t.title if len(t.title) <= 30 else t.title[:29] + "…"
            music = styled_menu(menu.addMenu(f"Music: {title}"))
            _action(music, t.byline or t.app, lambda: None, enabled=False)
            music.addSeparator()
            _action(music, "Pause" if t.playing else "Play", lambda: self.media_command("toggle"), enabled=t.can_toggle)
            _action(music, "Next", lambda: self.media_command("next"), enabled=t.can_next)
            _action(music, "Previous", lambda: self.media_command("previous"), enabled=t.can_previous)
        if self.settings.reviews:
            waiting = styled_menu(menu.addMenu(f"Reviews waiting ({len(self.settings.reviews)})"))
            for item in reversed(self.settings.reviews):
                _action(waiting, f"{reviews.title(item)}  ·  {reviews.lines_text(item)}",
                        lambda r=item: self.start_review(r))
            waiting.addSeparator()
            _action(waiting, "Clear the list", self.clear_reviews)
        sessions = self.agents.live_sessions()
        agents = styled_menu(menu.addMenu(f"Coding agents ({len(sessions)})" if sessions else "Coding agents"))
        for session in sessions[:8]:
            who = f" ({session.agent_label})" if session.agent != "claude" else ""
            line = f"{session.project}{who}: {session.status_text}"
            if session.status == "working" and session.activity:
                line += f" · {session.activity[:40]}"
            _action(agents, line, lambda: None, enabled=False)
        if not sessions:
            _action(agents, "No sessions right now", lambda: None, enabled=False)
        agents.addSeparator()
        _action(agents, "Agent settings…", lambda: self.open_settings("Agents"))
        _action(menu, "Dashboard\tD", self.open_dashboard)
        _action(menu, "Settings…", self.open_settings)
        if self.update_ready():
            _action(menu, f"Update to Pomo {self.settings.update_version}", self.install_update)
        menu.addSeparator()
        _action(menu, "Run on startup", lambda: self.set_start_with_windows(not self.settings.start_with_windows),
                checked=self.settings.start_with_windows)
        if self.view.isVisible():
            _action(menu, "Hide timer", self.hide_timer, enabled=self.tray is not None)
        else:
            _action(menu, "Show timer", self.show_timer)
        _action(menu, f"Quit {APP_NAME}", self.quit)

    def _show_view_menu(self, pos) -> None:
        menu = styled_menu(QMenu())
        self._populate_menu(menu)
        menu.exec(pos)
        menu.deleteLater()

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.Trigger:
            if self.view.isVisible():
                self.view.raise_()
                self.view.flash(1)
            else:
                self.show_timer()
        elif reason == QSystemTrayIcon.DoubleClick:
            self.open_dashboard()

    # ---------------------------------------------------------------- engine
    def _on_tick(self) -> None:
        second = int(self.engine.remaining)
        if second != self._tray_second:
            self._tray_second = second
            self._update_tray()
            self._refresh_taskbar()
            self._maybe_cheer()

    def _maybe_cheer(self) -> None:
        """Halfway, 5 minutes left, the last minute: a few warm words from Pomo, once each per session."""
        from . import cheer

        e, s = self.engine, self.settings
        if not s.cheer_on or e.mode is not Mode.FOCUS or e.state is not State.RUNNING:
            return
        moment = cheer.due(e.total, e.remaining, self._cheered)
        if moment is None or not self.view.isVisible() or self.toast.showing or self._approval_on_screen():
            return  # a card is up: let it be, this moment has passed
        if s.show_buddy:
            self.view.pomo.cheer()
        if self.whisper is None:
            self.whisper = cheer.Whisper(self)
        self.whisper.say(cheer.line(moment, e.task), self.view.whisper_anchor())

    def _on_session_logged(self, record) -> None:
        if record.mode == "focus":
            distracted = self.guard.take_session()
            if distracted:
                record = replace(record, distracted_sec=min(distracted, record.actual_sec))
        self.storage.add(record)
        if record.mode == "focus" and record.completed:  # counted for today's quests
            s = self.settings
            if not self._focus_paused and not record.distracted_sec:
                quests.count(s, "clean")
            if self.focus_sound_playing():
                quests.count(s, "sound_session")
            review = reviews.find(s, record.task)
            if review is not None:  # that review is done
                reviews.remove(s, review)
                quests.count(s, "review")
            self._step_news = studypath.count_session(s.study_path, record.task) if s.study_path else None
            self.save_settings()

    def _on_engine_changed(self) -> None:
        e = self.engine
        (prev_mode, prev_state), self._prev_timer = self._prev_timer, (e.mode, e.state)
        focusing = e.mode is Mode.FOCUS and e.state is State.RUNNING
        was_focusing = prev_mode is Mode.FOCUS and prev_state is State.RUNNING
        if focusing != was_focusing:
            self._sync_presence()
            self._follow_music(focusing)
            self._sync_focus_sound()
            if was_focusing:
                QTimer.singleShot(0, self._flush_held)  # a finished session shows them on its card instead
                self._recap_window = self.tracker.last_work  # for "Where was I?" when the break ends
                self._away_since = datetime.now()
            elif prev_mode is not Mode.FOCUS or prev_state in (State.IDLE, State.FINISHED):
                self._focus_paused = False  # a fresh session, not a resumed one
                self._cheered = set()
                self._starter, self._starter_pending = self._starter_pending, False
                QTimer.singleShot(0, self._offer_project_task)
        if not focusing and self.whisper is not None:
            self.whisper.fade_out()
        if e.mode is Mode.FOCUS and e.state is State.PAUSED and prev_state is State.RUNNING:
            self._focus_paused = True
        if (e.mode.is_break and e.state is State.RUNNING and not (prev_mode.is_break and prev_state is State.RUNNING)
                and self.agent_signal() == "busy"):
            self.quest_event("agent_break", announce=False)  # shown on the next card
        if prev_mode.is_break and prev_state in (State.RUNNING, State.PAUSED) and e.mode is Mode.FOCUS:
            self._break_over_pending = True  # a finished break shows "Where was I?" on its card; a skipped one here
            QTimer.singleShot(0, self._break_over)
        self._refresh_taskbar()
        if e.state is State.RUNNING:
            self._idle_since = None
            self._idle_nudged = False
        elif self._idle_since is None:
            self._idle_since = time.monotonic()
        if e.cycle_index != self.settings.cycle_index or self.settings.cycle_date != date.today().isoformat():
            self.settings.cycle_index = e.cycle_index
            self.settings.cycle_date = date.today().isoformat()
            self.save_settings()
        self._update_tray()

    def _on_history_changed(self) -> None:
        self.today_count = self.storage.completed_focus_count(date.today())
        self.view.update()
        self._update_tray()

    def _update_tray(self) -> None:
        if self.tray is None:
            return
        e = self.engine
        accent = mode_color(self.theme, self.settings, e.mode.value)
        track = QColor(accent)
        track.setAlpha(70)
        frac = e.fraction_left if e.state in (State.RUNNING, State.PAUSED) else 1.0
        self.tray.setIcon(QIcon(render_tray_icon(64, accent, frac, QColor(self.theme.orb_face), track)))
        state = {State.RUNNING: "", State.PAUSED: " (paused)", State.IDLE: " (ready)", State.FINISHED: " (up next)"}[e.state]
        tip = (f"{APP_NAME}\n{e.mode.label}{state} · {fmt_clock(e.remaining)}\n"
               f"{self.today_count}/{self.settings.daily_goal} pomodoros today")
        headline = self.agents.headline()
        if headline:
            tip += f"\n{headline}"
        self.tray.setToolTip(tip[:127])  # Windows truncates tray tooltips at 128 characters

    def _on_finished(self, finished: Mode, upcoming: Mode, auto_started: bool) -> None:
        s = self.settings
        count, goal = self.today_count, s.daily_goal
        minutes = round(self.engine.total / 60)
        if finished is Mode.FOCUS:
            if count == goal:
                title = "Daily goal reached!"
                body = f"That's {count} of {goal} pomodoros today. Enjoy a {minutes}-minute {upcoming.label.lower()}."
            elif upcoming is Mode.LONG:
                title = "Time for a long break"
                body = (f"{plural(self.engine.cycle_length, 'pomodoro')} in a row, nice work. "
                        f"Take {minutes} minutes off. ({count}/{goal} today)")
            else:
                title = "Focus session complete"
                body = f"Pomodoro {count} of {goal} done. Time for a {minutes}-minute break."
        else:
            title = "Break is over"
            if count < goal:
                body = f"Ready for pomodoro {count + 1} of {goal}? {minutes} minutes of focus."
            else:
                body = f"Goal already met. Up for another {minutes}-minute focus session?"
        extras: list[tuple[str, object]] = []
        rate = None
        recap = finished.is_break and upcoming is Mode.FOCUS and s.break_recap
        if finished.is_break:
            self._break_over_pending = False  # this card is the "Where was I?"
        if recap:
            title = "Where was I?"
            lines, recap_extras = self._recap_lines()
            body = "\n".join([body] + lines)
            extras += recap_extras
        if finished is Mode.FOCUS:
            next_task, plan_done = planner.advance(s)
            if next_task and next_task != self.engine.task:
                self.set_task(next_task)
                body += f"\nNext up: {next_task}"
            elif plan_done:
                body += "\nThat was the last task in your plan. Nice work!"
            held = self.pacer.summary(self.pacer.take_held())
            if held:
                body += f"\n{held}"
            self.lifetime = self.storage.lifetime_pomodoros()
            fresh = garden.newly_unlocked(self.lifetime, s.unlocks_seen)
            if fresh:  # usually one; several right after an update, for people with a long history
                s.unlocks_seen += fresh
                plural_s = "s" if len(fresh) > 1 else ""
                body += f"\nNew outfit{plural_s} unlocked: {', '.join(OUTFITS[k] for k in fresh)}!"
                extras.append(("Wear it", lambda k=fresh[-1]: self.set_outfit(k)))
            if s.rate_sessions:
                rate = self._rate_session
        self._check_quests()
        quest_text, quest_outfit = self._quest_news(self._take_quest_news())
        if quest_text:
            body += f"\n{quest_text}"
            if quest_outfit and not any(x[0] == "Wear it" for x in extras):
                extras.insert(0, ("Wear it", lambda k=quest_outfit: self.set_outfit(k)))
        room_text = self._room_news()
        if room_text:
            body += f"\n{room_text}"
            if len(extras) < 3 and not any(x[0] == "Wear it" for x in extras):  # the card has room for three
                extras.insert(0, ("See room", self.open_room))
        starter = finished is Mode.FOCUS and self._starter
        if starter:  # "just 10 minutes" done: the hard part's over, so invite a full session
            self._starter = False
            title = "You started!"
            first, _sep, rest = body.partition("\n")
            body = (f"{s.starter_min} minutes done. Starting is the hardest part, and you did it. Keep going for a "
                    f"full {s.focus_min}-minute pomodoro?" + (f"\n{rest}" if rest else ""))
        if finished is Mode.FOCUS:
            locked = applock.tries_text(self.applock.take_tries())
            if locked:
                body += f"\n{locked}"
        step_news, self._step_news = (self._step_news if finished is Mode.FOCUS else None), None
        if step_news is not None:
            mi, si, st = step_news
            n, spent = st["pomodoros"], st["spent"]
            if spent >= n and not st["done"]:
                body += f"\nPath: {plural(spent, 'pomodoro')} on “{st['title']}”. Is it done?"
                extras.insert(0, ("Step done", lambda mi=mi, si=si: self.complete_step(mi, si)))
            else:
                body += f"\nPath: {min(spent, n)} of {n} pomodoros on this step."
        if finished is Mode.FOCUS and s.break_card and upcoming.is_break:
            extras.append(("Breathe", self.open_breathe))
            waiting = len(self.storage.thoughts())
            if waiting:
                extras.append((f"Thoughts ({waiting})", self.open_thoughts))
            if len(extras) < 3:  # the card has room for three
                extras.append(("Play", self.open_game))
        elif self.engine.task and upcoming is Mode.FOCUS and not recap:
            body += f"\nTask: {self.engine.task}"

        self.notifier.alert()
        self.notifier.toast(title, body)
        view = self.view
        if finished is Mode.FOCUS:
            view.celebrate(big=count == goal or bool(room_text) or any(e[0].startswith("Wear") for e in extras))
            if s.treats < 9:  # a dango for Pomo, fed from the badge beside him
                s.treats += 1
            self.save_settings()
        if auto_started:
            view.flash()
        if self.taskbar is not None:
            self.taskbar.flash()
        if s.popup_card and view.isVisible() and not self._approval_on_screen():
            e = self.engine
            back = self._recap_window if recap and self._window_alive(self._recap_window) else None
            if auto_started and back is not None:
                actions = [(self._back_label(back), self._back_to_work, True), ("Pause", e.pause, False)]
            elif auto_started:
                actions = [("Pause", e.pause, True), ("Skip", e.skip, False)]
            elif back is not None:
                actions = [(self._back_label(back), lambda: self._back_to_work(start=True), True),
                           ("Start here", e.start, False)]
            else:
                actions = [(f"Start {upcoming.label.lower()}", e.start, True), ("Skip", e.skip, False)]
            if starter:
                actions = [("Keep going", self._keep_going, True),
                           ("Enjoy the break", lambda: None, False) if auto_started else ("Take a break", e.start, False)]
            accent = mode_color(self.theme, s, upcoming.value)
            self.toast.show_message(title, body, accent, actions, view.anchor_rect_global(),
                                    timeout_ms=30000 if rate or extras else 20000, rate=rate, extras=extras[:3])

    # ----------------------------------------------------------- where was I?
    @staticmethod
    def _window_alive(window) -> bool:
        if window is None or sys.platform != "win32":
            return False
        import ctypes

        return bool(ctypes.windll.user32.IsWindow(window.hwnd))

    def _recap_lines(self) -> tuple[list[str], list[tuple[str, object]]]:
        """"Where was I?": your task, the window you were in, your agents' news from the break, parked thoughts."""
        s, e = self.settings, self.engine
        lines: list[str] = []
        extras: list[tuple[str, object]] = []
        if e.task:
            lines.append(f"Task: {e.task}")
        if self._window_alive(self._recap_window):
            lines.append(f"You were in: {self._recap_window.label}")
        news = self._agent_news_since(self._away_since)
        if news:
            lines.append(f"While you were away: {news}.")
        thoughts = self.storage.thoughts()
        if thoughts:
            first = thoughts[0].text if len(thoughts[0].text) <= 40 else thoughts[0].text[:39] + "…"
            more = f" and {len(thoughts) - 1} more" if len(thoughts) > 1 else ""
            lines.append(f"Parked: “{first}”{more}")
            extras.append((f"Thoughts ({len(thoughts)})", self.open_thoughts))
        review = reviews.latest(s)
        if review is not None and not s.task_queue and reviews.title(review) != e.task:
            lines.append(f"Waiting for you: {reviews.title(review)} ({reviews.lines_text(review)})")
            extras.append(("Review it", lambda r=review: self.start_review(r)))
        return lines, extras

    def _agent_news_since(self, since: datetime | None) -> str:
        """'api finished and changed 4 files, web is waiting for you'."""
        if since is None:
            return ""
        per: dict[str, list] = {}
        for ev in self.storage.agent_events(since=since.date()):
            if ev.ts < since.replace(microsecond=0):
                continue
            entry = per.setdefault(ev.project, [0, set()])
            if ev.event == "Stop":
                entry[0] += 1
            elif ev.event == "PostToolUse" and ev.tool in EDIT_TOOLS:
                entry[1].add(ev.detail)
        waiting = list(dict.fromkeys(a.project for a in self.agents.live_sessions()
                                     if a.status in ("approval", "waiting")))
        parts = [f"{project} is waiting for you" for project in waiting]
        for project, (turns, files) in per.items():
            if project in waiting or not turns:
                continue
            changed = f" and changed {plural(len(files), 'file')}" if files else ""
            parts.append(f"{project} finished{changed}")
        return ", ".join(parts[:3])

    @staticmethod
    def _back_label(window) -> str:
        app = window.app if len(window.app) <= 14 else window.app[:13] + "…"
        return f"Back to {app}"

    def _back_to_work(self, start: bool = False) -> None:
        """One click: the window you were working in comes back to the front (and focus starts)."""
        if start and self.engine.state is not State.RUNNING:
            self._start_focus_now()
        if self._recap_window is not None:
            winutil.activate_window(self._recap_window.hwnd)

    def _break_over(self) -> None:
        """A break was skipped or switched away from (not finished): "Where was I?" on its own card."""
        if not self._break_over_pending:
            return
        self._break_over_pending = False
        s = self.settings
        if not s.break_recap or not s.popup_card or not self.view.isVisible() or self._approval_on_screen():
            return
        lines, extras = self._recap_lines()
        if not lines:
            return
        back = self._recap_window if self._window_alive(self._recap_window) else None
        if self.engine.state is State.RUNNING:
            actions = [(self._back_label(back), self._back_to_work, True)] if back else [("OK", lambda: None, True)]
        elif back is not None:
            actions = [(self._back_label(back), lambda: self._back_to_work(start=True), True),
                       ("Start here", self._start_focus_now, False)]
        else:
            actions = [("Start focus", self._start_focus_now, True), ("Later", lambda: None, False)]
        self.toast.show_message("Where was I?", "\n".join(lines), mode_color(self.theme, s, "focus"), actions,
                                self.view.anchor_rect_global(), timeout_ms=30000, extras=extras)

    # ---------------------------------------------------- automatic task names
    def _offer_project_task(self) -> None:
        """A focus session started without a task: name it after the project in your editor (asking first)."""
        s, e = self.settings, self.engine
        if not (e.mode is Mode.FOCUS and e.state is State.RUNNING):
            return
        nxt = studypath.next_step(s.study_path)
        if nxt is not None and not e.task and not s.task_queue:
            self._use_step(nxt[2]["title"])  # no task? today's step: that's what the path is for
            return
        if s.auto_task == "off":
            return
        if e.task and e.task != self._auto_task:
            return  # you named it yourself
        project = self.tracker.recent_project()
        if project is None or project.task == e.task:
            return
        if s.auto_task == "auto":
            self._use_project(project.task)
            return
        if not s.popup_card or not self.view.isVisible() or self._approval_on_screen() or self.toast.showing:
            return
        task = project.task
        self.toast.show_message(
            f"Working on {project.name}?",
            f"Pomo saw {project.described}. Name this focus session after it, and your dashboard shows "
            "your focus per project.",
            mode_color(self.theme, s, "focus"),
            [(f"Use “{task}”", lambda: self._use_project(task), True), ("No thanks", lambda: None, False)],
            self.view.anchor_rect_global(), timeout_ms=20000,
            extras=[("Always, without asking", lambda: self._always_use_project(task))])

    def _use_project(self, task: str) -> None:
        self.set_task(task)
        self._auto_task = task

    def _always_use_project(self, task: str) -> None:
        self.settings.auto_task = "auto"
        self._use_project(task)

    # ------------------------------------------------------------ daily quests
    def quest_event(self, counter: str, announce: bool = True) -> None:
        """Something a quest counts happened (a breathing break, a parked thought...)."""
        if not self.settings.quests_on:
            return
        quests.count(self.settings, counter)
        self.save_settings()
        self._check_quests()
        if announce:
            QTimer.singleShot(700, self._flush_quest_news)

    def _check_quests(self) -> None:
        """Note today's newly finished quests; their news waits for the next card (see _flush_quest_news)."""
        s = self.settings
        if not s.quests_on:
            return
        self._roll_quests()
        before = s.quests_total
        fresh = quests.check(s, self.storage.sessions(since=date.today()), date.today())
        if not fresh:
            return
        unlocked = quests.unlocked_by(before, s.quests_total)
        s.unlocks_seen += [k for k in unlocked if k not in s.unlocks_seen]
        self.save_settings()
        self._quest_pending[0].extend(fresh)
        self._quest_pending[1].extend(unlocked)

    def _roll_quests(self) -> bool:
        """A new day: today's quests. (Which quests fit is only worked out then: it reads the agents' settings.)"""
        s = self.settings
        if s.quest_day == date.today().isoformat() and s.quests:
            return False
        self._quest_pending = ([], [])
        studies = bool(s.study_path) or bool(self.storage.log_days())
        return quests.roll_over(s, datetime.now(), self._uses_agents(), studies)

    def _take_quest_news(self) -> tuple[list, list]:
        news, self._quest_pending = self._quest_pending, ([], [])
        return news

    @staticmethod
    def _quest_news(news: tuple[list, list]) -> tuple[str, str | None]:
        """('Quest complete: Hat trick! New outfit unlocked: Bunny ears!', 'bunnyears')."""
        fresh, unlocked = news
        if not fresh:
            return "", None
        names = [q.title for q in fresh if q.key != quests.PERFECT_DAY]
        text = f"Quest complete: {', '.join(names)}!" if names else ""
        if any(q.key == quests.PERFECT_DAY for q in fresh):
            text += " That's all three today: Perfect day!"
        if unlocked:
            text += f" New outfit unlocked: {', '.join(OUTFITS[k] for k in unlocked)}!"
        return text.strip(), (unlocked[-1] if unlocked else None)

    def _flush_quest_news(self) -> None:
        """Show finished quests on a card of their own, when that won't get in the way."""
        fresh, unlocked = self._quest_pending
        if not fresh or self.pacer.should_hold():
            return  # during focus they wait for the break card
        s = self.settings
        if s.popup_card and (self.toast.showing or not self.view.isVisible() or self._approval_on_screen()):
            return  # try again later (housekeeping, or the next card)
        self._quest_pending = ([], [])
        quest = next((q for q in fresh if q.key != quests.PERFECT_DAY), fresh[0])
        perfect = any(q.key == quests.PERFECT_DAY for q in fresh)
        title = "Perfect day!" if quest.key == quests.PERFECT_DAY else f"Quest complete: {quest.title}"
        times = s.badges.get(quest.key, 1)
        body = f"{quest.goal}. The {quest.title} badge is yours" + (f" (×{times})." if times > 1 else ".")
        if len(fresh) > 1:
            body += "\n" + self._quest_news((fresh, []))[0]
        if perfect and quest.key != quests.PERFECT_DAY:
            body += "\nAll three of today's quests done!"
        if unlocked:
            body += f"\nNew outfit unlocked: {', '.join(OUTFITS[k] for k in unlocked)}!"
        room_text = self._room_news()
        if room_text:
            body += f"\n{room_text}"
        if not s.popup_card:
            self.notifier.toast(title, body)
            return
        actions = [("Yay!", lambda: None, True)]
        if unlocked:
            actions = [("Wear it", lambda k=unlocked[-1]: self.set_outfit(k), True), ("Later", lambda: None, False)]
        self.view.celebrate(big=perfect or bool(unlocked) or bool(room_text))
        extras = [("Badges", self.open_dashboard)] + ([("See room", self.open_room)] if room_text else [])
        self.toast.show_message(title, body, mode_color(self.theme, s, "long"), actions,
                                self.view.anchor_rect_global(), timeout_ms=15000, extras=extras)

    # ------------------------------------------------------- agent review queue
    def _on_review_ready(self, review: dict) -> None:
        item = reviews.add(self.settings, review)
        self.save_settings()
        self._fresh_review = item  # the finish notice that follows offers it
        if self.pacer.should_hold():
            self.pacer.hold("review", item["project"], reviews.lines_text(item))

    def start_review(self, item: dict) -> None:
        """Make reviewing an agent's changes your next focus task."""
        title = reviews.title(item)
        e, queue = self.engine, self.settings.task_queue
        if e.mode is Mode.FOCUS and e.state in (State.RUNNING, State.PAUSED) and e.task and e.task != title:
            # you're mid-session: the review comes right after it, then the rest of that task
            rest = 0
            if queue and queue[0]["task"] == e.task:
                rest = queue.pop(0)["pomodoros"] - 1
            queue[0:0] = [{"task": e.task, "pomodoros": 1}, {"task": title, "pomodoros": 1}] + (
                [{"task": e.task, "pomodoros": rest}] if rest > 0 else [])
            del queue[20:]
            self.save_settings()
        elif queue:
            queue.insert(0, {"task": title, "pomodoros": 1})
            del queue[20:]
            self.plan_changed()
        else:
            self.set_task(title)
        if self.view.isVisible():
            self.view.pomo.hello()

    def clear_reviews(self) -> None:
        self.settings.reviews = []
        self.save_settings()

    # ---------------------------------------------------------------- taskbar
    def _sync_taskbar(self) -> None:
        want = self.settings.taskbar_button and sys.platform == "win32" and not self._taskbar_dismissed
        if want and self.taskbar is None:
            from .taskbar import TaskbarButton

            self.taskbar = TaskbarButton(self)
            self.taskbar.command.connect(self._on_taskbar_command)
            self.taskbar.activated.connect(self._on_taskbar_activated)
            self.taskbar.closed.connect(self._on_taskbar_closed)
            self.taskbar.start()
        elif not want and self.taskbar is not None:
            self.taskbar.stop()
            self.taskbar.deleteLater()
            self.taskbar = None

    def _refresh_taskbar(self) -> None:
        if self.taskbar is not None:
            self.taskbar.refresh()

    def _on_taskbar_command(self, name: str) -> None:
        e = self.engine
        {"reset": e.reset, "toggle": e.toggle, "skip": e.skip}.get(name, lambda: None)()

    def _on_taskbar_activated(self) -> None:
        self.show_timer()
        self.view.flash(1)

    def _on_taskbar_closed(self) -> None:
        """"Close window" on the taskbar button: the button goes, Pomo stays in the tray."""
        self._taskbar_dismissed = True
        self._sync_taskbar()
        if self.tray is not None:
            self.notifier.toast("Pomo is still here", "It's in the tray by the clock. Turn the taskbar button off "
                                                       "for good in Settings > General.")

    # ----------------------------------------------------------- talk to Pomo
    def _ensure_voice(self) -> None:
        if self.speaker is None:
            from .voice import Speaker

            self.speaker = Speaker(self.cache_dir, self)
            self.speaker.started.connect(lambda: self._set_talking(True))
            self.speaker.finished.connect(lambda: self._set_talking(False))
        if self.talk is None:
            from .voice import Talk

            self.talk = Talk(self, self)
            self.talk.heard.connect(self._on_heard)

    def start_talk(self, held_key: int | None = None) -> None:
        """Listen for a question. held_key: listen while that key is held (the shortcut); else until you stop."""
        self._ensure_voice()
        if self.talk.recorder.recording:
            self.talk.end()  # a second press ends listening
            return
        self.talk.begin(held_key)

    def _on_heard(self, text: str) -> None:
        self.ask_pomo(text)

    def _set_talking(self, talking: bool) -> None:
        """Pomo's mouth moves while a reply is read aloud: on the timer and in Ask Pomo."""
        for pomo in (self.orb.pomo, self.island.pomo, self.chat.avatar.pomo if self.chat is not None else None):
            if pomo is not None:
                pomo.talking = talking

    def speak_reply(self, text: str) -> None:
        """Read one of Pomo's replies aloud, if you asked for that."""
        if not self.settings.voice_replies or not text.strip():
            return
        self._ensure_voice()
        self.speaker.speak(text, self.settings.voice_name)

    # ---------------------------------------------------------------- updates
    def check_for_updates(self, manual: bool = False) -> None:
        """Ask GitHub for a newer Pomo. The installed app also downloads and verifies it, ready to offer."""
        s = self.settings
        if self._update_worker is not None:
            return
        if not manual and (not s.auto_update or self.update_mode == "source"):
            return
        download = self.update_mode == "installer"
        self._update_error = ""
        self._update_manual = manual
        self._update_worker = worker = updater.UpdateWorker(APP_VERSION, self.data_dir / "updates", download, self)
        worker.checked.connect(self._on_update_checked)
        worker.ready.connect(self._on_update_ready)
        worker.failed.connect(self._on_update_failed)
        worker.finished.connect(self._update_done)
        worker.start()
        self.update_status_changed.emit()

    def _update_done(self) -> None:
        if self._update_worker is not None:
            self._update_worker.deleteLater()
        self._update_worker = None
        self.update_status_changed.emit()

    def _on_update_checked(self, release) -> None:
        s = self.settings
        s.update_checked = datetime.now().replace(microsecond=0).isoformat()
        if release is None:
            if s.update_version and not updater.is_newer(s.update_version, APP_VERSION):
                self._clear_update()
        else:
            if release.version != s.update_version:  # a new one: start afresh
                s.update_file, s.update_tries, s.update_offered = "", 0, ""
            s.update_version, s.update_page, s.update_notes = release.version, release.page, release.notes
            if self.update_mode != "installer":
                QTimer.singleShot(0, self._tell_about_update)
        self.save_settings()

    def _on_update_ready(self, release, path: str) -> None:
        self.settings.update_file = path
        self.save_settings()
        self.update_status_changed.emit()
        manual = self._update_manual
        QTimer.singleShot(0, lambda: self._maybe_offer_update(manual))

    def _on_update_failed(self, message: str, worth_telling: bool) -> None:
        self._update_error = message
        if worth_telling and self.settings.update_version:
            QTimer.singleShot(0, self._tell_about_update)  # it can't be one click: point to the download page

    def update_ready(self) -> bool:
        """A newer Pomo is downloaded and verified, one click from installing."""
        s = self.settings
        return (self.update_mode == "installer" and bool(s.update_file) and Path(s.update_file).exists()
                and updater.is_newer(s.update_version, APP_VERSION) and s.update_tries < updater.MAX_TRIES)

    def _update_waits(self) -> bool:
        """News of an update waits for your break (mid-focus) or for you to come back (away)."""
        e = self.engine
        focusing = e.mode is Mode.FOCUS and e.state in (State.RUNNING, State.PAUSED)
        return focusing or idle_seconds() > updater.AWAY_S

    def _maybe_offer_update(self, manual: bool = False) -> None:
        """'Pomo 1.6.0 is available. Update now?' as soon as it's downloaded and verified: at your break if
        you're focusing, when you're back if you're away, again in a while if the card went unanswered,
        and tomorrow if you said Later. With the timer hidden (or cards off), a Windows notification."""
        s = self.settings
        if not self.update_ready():
            if (s.update_file and s.update_tries >= updater.MAX_TRIES) or self.update_mode == "manual":
                self._tell_about_update()  # it can't be one click here: offer the download instead
            return
        if not manual:
            if s.update_offered == date.today().isoformat() or time.monotonic() < self._update_snooze:
                return
            if self._update_waits():
                return
        if self.toast.showing or self._approval_on_screen():
            return  # housekeeping tries again in a moment
        if not s.popup_card or not self.view.isVisible():
            if self._update_toasted != s.update_version and self.notifier.toast(
                    f"Pomo {s.update_version} is available",
                    "A new version of Pomo is ready to install. Click here to update.",
                    on_click=self._offer_update_card):
                self._update_toasted = s.update_version
            return
        self._offer_update_card()

    def _offer_update_card(self) -> None:
        s = self.settings
        if not self.update_ready():
            return
        self._update_snooze = time.monotonic() + updater.OFFER_AGAIN_S  # unanswered, it comes back later
        notes = f"\n{s.update_notes}" if s.update_notes else ""
        self.view.pomo.hello()
        self.toast.show_message(
            f"Pomo {s.update_version} is available",
            f"A new version of Pomo is here.{notes}\nUpdate now and Pomo restarts on it in a few seconds. "
            "Your history and settings stay just as they are.",
            mode_color(self.theme, s, "long"),
            [("Update now", self.install_update, True), ("Later", self._update_later, False)],
            self.view.anchor_rect_global(), timeout_ms=60000,
            extras=[("What's new", self._open_update_page)])

    def _update_later(self) -> None:
        self.settings.update_offered = date.today().isoformat()  # ask again tomorrow
        self.save_settings()

    def install_update(self) -> None:
        """Run the downloaded installer silently and quit: it updates Pomo and starts it again."""
        s = self.settings
        if not self.update_ready() or self._updating:
            self._tell_about_update()
            return
        s.update_tries += 1
        s.last_version = APP_VERSION  # so the new version knows it was just updated
        self._save_now()
        folder = updater.app_dir()
        log = self.data_dir / "updates" / "install.log"
        args = updater.installer_args(Path(s.update_file), folder, log, Path(sys.executable))
        if updater.launch_installer(args):
            self._updating = True
            self.quit()
        else:
            self._update_error = "Windows wouldn't start the installer."
            self._tell_about_update()

    def _tell_about_update(self) -> None:
        """A newer Pomo that can't be a one-click update here: say so once, with a download link."""
        s = self.settings
        if not s.update_version or s.update_notified == s.update_version or not updater.is_newer(
                s.update_version, APP_VERSION):
            return
        if self._update_waits() or self.toast.showing or self._approval_on_screen():
            return  # housekeeping tries again
        title = f"Pomo {s.update_version} is available"
        if not s.popup_card or not self.view.isVisible():
            if self.notifier.toast(title, "A new version of Pomo is out. Click here to download it.",
                                   on_click=self._open_update_page):
                s.update_notified = s.update_version
                self.save_settings()
            return
        s.update_notified = s.update_version
        self.save_settings()
        notes = f"{s.update_notes}\n" if s.update_notes else ""
        self.toast.show_message(
            title,
            f"A new version of Pomo is out.\n{notes}Get it from GitHub: run the new installer and your "
            "history and settings carry over.",
            mode_color(self.theme, s, "long"),
            [("Download", self._open_update_page, True), ("Later", lambda: None, False)],
            self.view.anchor_rect_global(), timeout_ms=60000)

    def _open_update_page(self) -> None:
        self._open_page(self.settings.update_page)

    def _clear_update(self) -> None:
        s = self.settings
        if s.update_file:
            try:
                Path(s.update_file).unlink()
            except OSError:
                pass
        s.update_version = s.update_file = s.update_notes = s.update_offered = ""
        s.update_tries = 0

    def _after_update(self) -> None:
        """At start: if Pomo was just updated, say so (with what's new), and tidy up the installer."""
        s = self.settings
        updated = bool(s.last_version) and updater.is_newer(APP_VERSION, s.last_version)
        page, notes = s.update_page, s.update_notes
        if s.update_version and not updater.is_newer(s.update_version, APP_VERSION):
            self._clear_update()  # we're on it now (or past it)
        s.last_version = APP_VERSION
        self.save_settings()
        if not updated or not s.popup_card or not self.view.isVisible() or self._approval_on_screen():
            return
        self.view.celebrate()
        self.toast.show_message(
            f"Pomo is now {APP_VERSION}",
            (f"{notes}\n" if notes else "") + "Thanks for keeping Pomo up to date!",
            mode_color(self.theme, s, "focus"),
            [("What's new", lambda: self._open_page(page), True), ("OK", lambda: None, False)],
            self.view.anchor_rect_global(), timeout_ms=30000)

    @staticmethod
    def _open_page(url: str) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl(url or updater.RELEASES_PAGE))

    def update_status_text(self) -> str:
        """For Settings: where updates stand."""
        s = self.settings
        if self._update_worker is not None:
            return "Checking GitHub for a new version…"
        newer = s.update_version and updater.is_newer(s.update_version, APP_VERSION)
        if newer and self.update_ready():
            return f"Pomo {s.update_version} is ready. Click Update now, or wait for Pomo to offer it."
        if newer and self.update_mode == "installer" and s.update_tries < updater.MAX_TRIES and not self._update_error:
            return f"Pomo {s.update_version} is out. Pomo is getting it ready."
        if newer:
            return f"Pomo {s.update_version} is out. Download it from GitHub to update."
        if self._update_error:
            return self._update_error
        if self.update_mode == "source":
            return f"Pomo {APP_VERSION}, running from source: updates come from git."
        if s.update_checked:
            try:
                when = datetime.fromisoformat(s.update_checked)
                return f"Pomo {APP_VERSION} is up to date (checked {fmt_day(when.date())}, {when:%H:%M})."
            except ValueError:
                pass
        return f"Pomo {APP_VERSION}."

    # ----------------------------------------------------------- housekeeping
    def _housekeeping(self) -> None:
        today = date.today()
        if today != self._today:
            self._today = today
            self.today_count = self.storage.completed_focus_count(today)
            if self.engine.state in (State.IDLE, State.FINISHED):
                self.engine.cycle_index = 0
                self.engine.changed.emit()
            self.view.update()
            self._update_tray()

        minutes = self.settings.idle_reminder_min
        if (minutes and not self._idle_nudged and self._idle_since is not None
                and time.monotonic() - self._idle_since >= minutes * 60):
            self._idle_nudged = True
            title = "Ready for a pomodoro?"
            body = (f"No timer has run for {minutes} minutes. "
                    f"{self.today_count}/{self.settings.daily_goal} done today.")
            self.notifier.toast(title, body)
            if self.settings.sound_enabled:
                self.notifier.play("Soft", self.settings.volume, 1)
            if self.settings.popup_card and self.view.isVisible() and not self._approval_on_screen():
                accent = mode_color(self.theme, self.settings, "focus")
                actions = [("Start focus", self._start_focus_now, True), ("Later", lambda: None, False)]
                self.toast.show_message(title, body, accent, actions, self.view.anchor_rect_global())
        self._maybe_morning_card()
        self._maybe_log_prompt()
        self._maybe_offer_review()
        if self.settings.quests_on and self._roll_quests():
            self.save_settings()
        self._check_quests()  # e.g. a quest your history already met when Pomo started
        self._flush_quest_news()
        self._maybe_offer_update()

    def _maybe_offer_review(self) -> None:
        s, e = self.settings, self.engine
        now = datetime.now()
        if (not s.review_hour or now.hour < s.review_hour or s.review_date == now.date().isoformat()
                or self.today_count < 1 or e.state is State.RUNNING or not self.has_ai_key()):
            return
        s.review_date = now.date().isoformat()
        self.save_settings()
        if not (s.popup_card and self.view.isVisible()) or self._approval_on_screen():
            return
        from .ai import quick_prompt

        self.toast.show_message(
            "Your day with Pomo", f"{plural(self.today_count, 'pomodoro')} today. Want a short review of your day?",
            mode_color(self.theme, s, "long"),
            [("Review my day", lambda: self.ask_pomo(quick_prompt("day", "")), True), ("Not today", lambda: None, False)],
            self.view.anchor_rect_global(), timeout_ms=30000)

    def _start_focus_now(self) -> None:
        if self.engine.mode is not Mode.FOCUS:
            self.engine.switch_mode(Mode.FOCUS)
        self.engine.start()

    # ---------------------------------------------------------- coding agents
    def _approval_on_screen(self) -> bool:
        card = self.agent_card
        return card is not None and card.isVisible() and card.approval is not None

    def _on_approval_requested(self, approval: Approval) -> None:
        self._approval_queue.append(approval)
        project, who = approval.session.project, approval.session.agent_label
        if approval.kind != "reply":  # a reply is optional: the card itself is the "Claude finished" news
            self.view.flash(2)
            if self.settings.sound_enabled:
                self.notifier.play("Soft", self.settings.volume, 1)
            if approval.kind == "question":
                self.notifier.toast(f"{project}: {who} has a question", approval.detail)
            else:
                self.notifier.toast(f"{project}: {who} needs your OK", self._approval_text(approval))
        if not self._approval_on_screen():
            self._show_next_approval()

    @staticmethod
    def _approval_text(approval: Approval) -> str:
        detail = approval.detail
        # let long commands and paths wrap: QLabel only breaks at spaces
        detail = "".join(ch + ("​" if ch in "/\\.,;&|=" else "") for ch in detail[:600])
        return f"{approval.tool}: {detail}" if detail else approval.tool

    def _show_next_approval(self) -> None:
        while self._approval_queue and self._approval_queue[0].id not in self.agents.pending:
            self._approval_queue.pop(0)
        if not self._approval_queue:
            return
        approval = self._approval_queue[0]
        wait = self.settings.agent_reply_wait_s if approval.kind == "reply" else self.settings.approval_wait_s
        left = max(1, round(wait - (time.monotonic() - approval.created)))
        if not self.view.isVisible():
            self.show_timer()
        if self.agent_card is None:
            from .agent_card import AgentCard

            self.agent_card = AgentCard(self)
        self.toast.dismiss()  # the card goes where toasts go
        self.agent_card.show_for(approval, self.view.anchor_rect_global(), left)

    def _on_approval_closed(self, approval: Approval) -> None:
        if self.agent_card is not None:
            self.agent_card.close_for(approval)
        if approval in self._approval_queue:
            self._approval_queue.remove(approval)
        QTimer.singleShot(250, self._show_next_approval)

    def _on_agent_finished(self, session, seconds: float) -> None:
        s = self.settings
        review, self._fresh_review = self._fresh_review, None
        if review is not None and review.get("project") != session.project:
            review = None
        if self.pacer.should_hold():  # quiet focus: it'll be on your break card
            if s.agent_notify_finish and seconds >= s.agent_notify_after_s:
                self.pacer.hold("finished", session.project, finished_detail(seconds))
            return
        if self.view.isVisible() and self.view.pomo is not None:
            self.view.pomo.celebrate()
        if not s.agent_notify_finish or seconds < s.agent_notify_after_s:
            return
        title = f"{session.project}: {session.agent_label} finished"
        if self.agents.awaiting_reply(session):  # its card already shows the agent's words, with a reply box
            self.notifier.toast(title, session.message[:200] or "Your turn when you're ready.")
            return
        body = f"Done after {fmt_clock(seconds)}. Your turn when you're ready."
        actions = [("OK", lambda: None, False)]
        if review is not None:  # its changes, as focus work for you
            body = (f"Done after {fmt_clock(seconds)}. It changed {reviews.summary(review)}.\n"
                    "Review them in your next focus session?")
            actions = [("Review next", lambda r=review: self.start_review(r), True), ("Later", lambda: None, False)]
        elif self.engine.mode.is_break:
            body = f"Done after {fmt_clock(seconds)}. Back to focus to look at it?"
            actions = [("Start focus", self._start_focus_now, True), ("Later", lambda: None, False)]
        self.notifier.toast(title, body)
        if s.popup_card and self.view.isVisible() and not self._approval_on_screen():
            self.toast.show_message(title, body, mode_color(self.theme, s, "focus"), actions,
                                    self.view.anchor_rect_global(), timeout_ms=12000)

    def _on_agent_needs_input(self, session, kind: str) -> None:
        self.view.flash(1)
        if kind == "idle_prompt":
            return  # Claude finished a while ago; the finish notice already covered it
        if kind != "permission_prompt" and self.pacer.should_hold():
            self.pacer.hold("waiting", session.project)
            return
        title = f"{session.project}: {session.agent_label} is waiting for you"
        body = session.message or ("It's asking for permission in the terminal." if kind == "permission_prompt"
                                   else session.activity or "It needs your input in the terminal.")
        self.notifier.toast(title, body)
        if self.settings.popup_card and self.view.isVisible() and not self._approval_on_screen():
            self.toast.show_message(title, body, mode_color(self.theme, self.settings, "long"),
                                    [("OK", lambda: None, False)], self.view.anchor_rect_global(), timeout_ms=12000)

    def _flush_held(self) -> None:
        """Focus stopped early (paused, reset, skipped): show the agent news Pomo held back."""
        held = self.pacer.summary(self.pacer.take_held())
        if held and self.settings.popup_card and self.view.isVisible() and not self._approval_on_screen():
            self.toast.show_message("Agent news", held, mode_color(self.theme, self.settings, "long"),
                                    [("OK", lambda: None, False)], self.view.anchor_rect_global(), timeout_ms=15000)

    def _on_suggest_break(self, agent) -> None:
        if not self.settings.popup_card or not self.view.isVisible() or self._approval_on_screen():
            return
        minutes = max(1, round((datetime.now() - agent.turn_started).total_seconds() / 60)) if agent.turn_started else 2
        self.view.pomo.hello()
        self.toast.show_message(
            f"{agent.project} is on a long task",
            f"{agent.agent_label} has been working for {plural(minutes, 'minute')}. Good moment for your break? "
            f"This focus session counts as done, and Pomo will tell you when {agent.agent_label} finishes.",
            mode_color(self.theme, self.settings, "short"),
            [("Break now", self.engine.finish_now, True), ("Keep focusing", lambda: None, False)],
            self.view.anchor_rect_global(), timeout_ms=30000)

    def _on_suggest_focus(self, agent) -> None:
        if not self.settings.popup_card or not self.view.isVisible() or self._approval_on_screen():
            return
        self.toast.show_message(
            f"{agent.project} is on a long task",
            f"Use the wait for a focus session of your own? Pomo will hold {agent.agent_label}'s updates "
            "until your break.",
            mode_color(self.theme, self.settings, "focus"),
            [("Start focus", self._start_focus_now, True), ("Not now", lambda: None, False)],
            self.view.anchor_rect_global(), timeout_ms=30000)

    # ------------------------------------------------------- focus helpers
    def _rate_session(self, rating: int) -> None:
        self.storage.rate_last_focus(rating)
        if rating == 3:
            self.view.pomo.hello()
        self._check_quests()  # Check-in counts ratings; its card waits until this one closes
        s = self.settings
        if not s.suggest_length:
            return
        today = date.today()
        if s.length_tip_date and today - date.fromisoformat(s.length_tip_date) < timedelta(days=7):
            return
        tip = insights.length_tip(self.storage.sessions(since=today - timedelta(days=insights.LOOKBACK_DAYS)),
                                  s.focus_min, today)
        if tip is not None:
            s.length_tip_date = today.isoformat()
            self.save_settings()
            QTimer.singleShot(1800, lambda: self._show_length_tip(tip))

    def _show_length_tip(self, tip) -> None:
        if not self.view.isVisible() or self._approval_on_screen():
            return
        self.view.pomo.hello()
        self.toast.show_message(
            "Pomo has a tip", f"{tip.text} Try {tip.minutes}-minute focus sessions?",
            mode_color(self.theme, self.settings, "focus"),
            [(f"Use {tip.minutes} min", lambda: self.set_default_minutes(Mode.FOCUS, tip.minutes), True),
             ("Not now", lambda: None, False)],
            self.view.anchor_rect_global(), timeout_ms=30000)

    def _sync_presence(self) -> None:
        e = self.engine
        focusing = e.mode is Mode.FOCUS and e.state is State.RUNNING
        self.guard.set_active(focusing)
        self.applock.set_active(focusing)
        if not focusing and self.lock_card is not None:
            self.lock_card.hide()
        self.away.configure(focusing, self.settings.away_after_min * 60)

    def _on_app_blocked(self, hwnd: int, exe: str) -> None:
        """A locked app came to the front during focus: put it away and say why."""
        strict = self.settings.lock_strict
        (applock.close if strict else applock.minimize)(hwnd)
        if self.lock_card is None:
            self.lock_card = applock.LockCard(self)
        self.lock_card.show_for(hwnd, exe, closed=strict)
        if self.view.isVisible():
            self.view.pomo.side_eye()

    def _on_went_away(self, idle: float) -> None:
        e = self.engine
        if not (e.mode is Mode.FOCUS and e.state is State.RUNNING):
            return
        self._away_paused = True
        e.pause()
        e.discount(idle)  # the time since you left goes back on the clock

    def _on_came_back(self, away: float) -> None:
        if not self._away_paused:
            return
        self._away_paused = False
        if self.engine.state is not State.PAUSED:
            return  # you already carried on
        self.view.pomo.hello()
        if not self.settings.popup_card or not self.view.isVisible() or self._approval_on_screen():
            return
        minutes = max(1, round(away / 60))
        self.toast.show_message(
            "Welcome back!",
            f"You were away about {plural(minutes, 'minute')}, so Pomo paused your focus session "
            "when you left. That time doesn't count.",
            mode_color(self.theme, self.settings, "focus"),
            [("Resume", self.engine.start, True), ("It was focus time", lambda: self._count_away(away), False)],
            self.view.anchor_rect_global(), timeout_ms=60000)

    def _count_away(self, seconds: float) -> None:
        self.engine.credit(seconds)
        if self.engine.state is State.PAUSED:
            self.engine.start()

    def _on_nudge(self, label: str, word: str) -> None:
        view = self.view
        if view.isVisible():
            view.pomo.side_eye()
        if not self.settings.popup_card or not view.isVisible() or self._approval_on_screen():
            return
        task = self.engine.task
        self.toast.show_message(
            f"Psst... {label}?",
            f"You're in a focus session{f' on {task}' if task else ''}. Back to it?",
            mode_color(self.theme, self.settings, "short"),
            [("Back to it", lambda: None, True), ("It's for work", lambda: self._allow_distraction(word), False)],
            view.anchor_rect_global(), timeout_ms=12000)

    def _allow_distraction(self, word: str) -> None:
        self.settings.distracting = [w for w in self.settings.distracting if w != word]
        self.save_settings()

    def _follow_music(self, focusing: bool) -> None:
        """Music follows the timer: it plays while you focus and pauses otherwise."""
        s = self.settings
        if not (s.music_follows and s.music_controls) or self.media is None or self.track is None:
            return
        if focusing != self.track.playing:
            self.media_command("toggle")

    # --------------------------------------------------- hotkeys and windows
    def _sync_hotkeys(self) -> None:
        s = self.settings
        bindings = {action: getattr(s, attr) for action, (attr, _label) in HOTKEYS.items()} if s.hotkeys_on else {}
        self.hotkey_failures = self.hotkeys.register(bindings)

    def _on_hotkey(self, action: str) -> None:
        e = self.engine
        if action == "toggle":
            e.toggle()
        elif action == "skip":
            e.skip()
        elif action == "park":
            self.park_thought()
        elif action == "music":
            self.media_command("toggle")
        elif action == "show":
            if self.view.isVisible():
                self.hide_timer()
            else:
                self.show_timer()
        elif action == "talk":
            parsed = parse_hotkey(self.settings.hotkey_talk)
            self.start_talk(parsed[1] if parsed else None)  # listens while the key is held

    def park_thought(self) -> None:
        if self.park_card is None:
            from .thoughts import ParkCard

            self.park_card = ParkCard(self)
            self.park_card.parked.connect(lambda _thought: self._on_parked())
        self.park_card.popup(self.view.anchor_rect_global())

    def _on_parked(self) -> None:
        if self.view.isVisible():
            self.view.pomo.squish.kick(3.0)  # a little nod: noted
        self.thoughts_changed()
        self.quest_event("park")

    def thoughts_changed(self) -> None:
        if self.thoughts_window is not None and self.thoughts_window.isVisible():
            self.thoughts_window.refresh()

    def open_thoughts(self) -> None:
        if self.thoughts_window is None:
            from .thoughts import ThoughtsWindow

            self.thoughts_window = ThoughtsWindow(self)
        self.thoughts_window.show()
        self.thoughts_window.raise_()
        self.thoughts_window.activateWindow()

    def open_breathe(self) -> None:
        if self.breathe is None:
            from .breathe import BreatheWindow

            self.breathe = BreatheWindow(self)
        self.breathe.start()

    def ask_pomo(self, prompt: str) -> None:
        """Open Ask Pomo and send a question (when a key is set; otherwise the key panel shows,
        and the question waits in the box)."""
        self.open_chat()
        if self.chat is None:
            return
        if self.chat.worker is None and self.has_ai_key():
            self.chat.send(prompt)
        else:
            self.chat.input.setPlainText(prompt)

    def _on_agents_changed(self) -> None:
        self._update_tray()
        self.view.update()
        self._refresh_taskbar()

    def _on_quit(self) -> None:
        self.agents.shutdown()
        self.guard.set_active(False)  # saves what it counted
        self.hotkeys.unregister_all()
        self.tracker.set_active(False)
        self.sound_player.stop(fade_out_s=0.2, wait=True)  # let the sound thread let go of the device first
        if self.talk is not None:
            self.talk.shutdown()
        if self.speaker is not None:
            self.speaker.stop()
        if self.taskbar is not None:
            self.taskbar.stop()
        if self.media is not None:
            self.media.stop()
        if self.chat is not None:
            self.chat.shutdown()
        self.engine.shutdown()
        self.settings.cycle_index = self.engine.cycle_index
        self.settings.cycle_date = date.today().isoformat()
        self._save_timer.stop()
        self._save_now()
        if self.tray is not None:
            self.tray.hide()
        self.storage.close()
