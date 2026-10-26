"""Application controller: owns the engine, storage and windows and wires them up."""
from __future__ import annotations

import os
import sys
import time
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QImage
from PySide6.QtWidgets import QApplication, QInputDialog, QMenu, QSystemTrayIcon

from . import APP_NAME, claude_hooks, garden, insights, keystore, media, planner, providers
from .agents import AgentHub, Approval
from .config import HOTKEYS, OUTFITS, UNLOCKS, Settings
from .hotkeys import GlobalHotkeys
from .pacing import AgentPacer, finished_detail
from .presence import AwayWatcher, FocusGuard
from .pomo import SLEEPY_AFTER
from .engine import Mode, State, TimerEngine
from .icons import app_icon, render_tray_icon
from .island import IslandWidget
from .notifier import Notifier
from .orb import OrbWidget
from .stats import fmt_clock, plural
from .storage import Storage
from .theme import THEMES, build_palette, build_qss, ensure_qss_assets, mode_color, theme_for
from .toast import ToastCard
from . import winutil

GOAL_PRESETS = (4, 6, 8, 10, 12, 16)


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
        self.pacer = AgentPacer(self, parent=self)
        self.away = AwayWatcher(parent=self)
        self.guard = FocusGuard(self.storage, self.settings, parent=self)
        self.hotkeys = GlobalHotkeys(qapp, self)
        self.hotkey_failures: dict[str, str] = {}
        self.lifetime = self.storage.lifetime_pomodoros()
        self._prev_timer = (self.engine.mode, self.engine.state)
        self._away_paused = False

        self.tray: QSystemTrayIcon | None = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(self.icon, self)
            self._tray_menu = styled_menu(QMenu())
            self._tray_menu.aboutToShow.connect(lambda: self._populate_menu(self._tray_menu))
            self._populate_menu(self._tray_menu)
            self.tray.setContextMenu(self._tray_menu)
            self.tray.activated.connect(self._on_tray_activated)
            self.tray.messageClicked.connect(self.show_timer)
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
        self.agents.changed.connect(self._on_agents_changed)
        self.agents.changed.connect(self.island._update_targets)
        qapp.aboutToQuit.connect(self._on_quit)
        self.pacer.suggest_break.connect(self._on_suggest_break)
        self.pacer.suggest_focus.connect(self._on_suggest_focus)
        self.away.went_away.connect(self._on_went_away)
        self.away.came_back.connect(self._on_came_back)
        self.guard.nudge.connect(self._on_nudge)
        self.hotkeys.triggered.connect(self._on_hotkey)
        qapp.screenRemoved.connect(lambda *_: QTimer.singleShot(400, self.view.ensure_on_screen))
        qapp.primaryScreenChanged.connect(lambda *_: QTimer.singleShot(400, self.view.ensure_on_screen))

        self._apply_display_mode()
        self._on_engine_changed()
        self._sync_integrations()
        self._sync_media()
        self._sync_hotkeys()
        self._sync_presence()
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

    def set_theme(self, key: str) -> None:
        new = self.settings.copy()
        new.theme = key
        new.clamp()
        self.apply_settings(new)

    def set_outfit(self, key: str) -> None:
        if not garden.is_unlocked(key, self.lifetime):
            return
        self.settings.pomo_outfit = key
        self.settings.clamp()
        self.save_settings()
        self.view.pomo.outfit = key
        self.view.pomo.hop(1.8)  # a little twirl to show it off

    def current_outfit(self) -> str:
        """What Pomo wears right now: dressed for what you're doing, or your pick when nothing's on."""
        s = self.settings
        if s.outfit_follows:
            for outfit in self._activity_outfits():
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

    def open_game(self) -> None:
        if self.game is None:
            from .game import GameWindow
            self.game = GameWindow(self)
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
        return self.settings.music_controls and self.settings.pomo_listens and self.music_playing()

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

    def hide_timer(self) -> None:
        if self.tray is not None:
            self.view.hide()
            self.toast.dismiss()

    def edit_task(self) -> None:
        items = [self.engine.task] if self.engine.task else []
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
            if garden.is_unlocked(key, self.lifetime):
                _action(outfits, label, lambda k=key: self.set_outfit(k), checked=key == self.settings.pomo_outfit)
            else:
                _action(outfits, f"{label}  ({UNLOCKS[key]} pomodoros)", lambda: None, enabled=False)
        menu.addSeparator()

        _action(menu, "Ask Pomo…", self.open_chat)
        _action(menu, "Play Petal Catch…", self.open_game)
        _action(menu, "Breathe with Pomo…", self.open_breathe)
        if self.track is not None:
            t = self.track
            title = t.title if len(t.title) <= 30 else t.title[:29] + "…"
            music = styled_menu(menu.addMenu(f"Music: {title}"))
            _action(music, t.byline or t.app, lambda: None, enabled=False)
            music.addSeparator()
            _action(music, "Pause" if t.playing else "Play", lambda: self.media_command("toggle"), enabled=t.can_toggle)
            _action(music, "Next", lambda: self.media_command("next"), enabled=t.can_next)
            _action(music, "Previous", lambda: self.media_command("previous"), enabled=t.can_previous)
        sessions = self.agents.live_sessions()
        agents = styled_menu(menu.addMenu(f"Coding agents ({len(sessions)})" if sessions else "Coding agents"))
        for session in sessions[:8]:
            line = f"{session.project}: {session.status_text}"
            if session.status == "working" and session.activity:
                line += f" · {session.activity[:40]}"
            _action(agents, line, lambda: None, enabled=False)
        if not sessions:
            _action(agents, "No sessions right now", lambda: None, enabled=False)
        agents.addSeparator()
        _action(agents, "Agent settings…", lambda: self.open_settings("Agents"))
        _action(menu, "Dashboard\tD", self.open_dashboard)
        _action(menu, "Settings…", self.open_settings)
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

    def _on_session_logged(self, record) -> None:
        if record.mode == "focus":
            distracted = self.guard.take_session()
            if distracted:
                record = replace(record, distracted_sec=min(distracted, record.actual_sec))
        self.storage.add(record)

    def _on_engine_changed(self) -> None:
        e = self.engine
        (prev_mode, prev_state), self._prev_timer = self._prev_timer, (e.mode, e.state)
        focusing = e.mode is Mode.FOCUS and e.state is State.RUNNING
        was_focusing = prev_mode is Mode.FOCUS and prev_state is State.RUNNING
        if focusing != was_focusing:
            self._sync_presence()
            self._follow_music(focusing)
            if was_focusing:
                QTimer.singleShot(0, self._flush_held)  # a finished session shows them on its card instead
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
            if s.break_card and upcoming.is_break:
                extras.append(("Breathe", self.open_breathe))
                waiting = len(self.storage.thoughts())
                if waiting:
                    extras.append((f"Thoughts ({waiting})", self.open_thoughts))
                if len(extras) < 3:  # the card has room for three
                    extras.append(("Play", self.open_game))
        elif self.engine.task and upcoming is Mode.FOCUS:
            body += f"\nTask: {self.engine.task}"

        self.notifier.alert()
        self.notifier.toast(title, body)
        view = self.view
        if finished is Mode.FOCUS:
            view.celebrate(big=count == goal or any(e[0].startswith("Wear") for e in extras))
            if s.treats < 9:  # a dango for Pomo, fed from the badge beside him
                s.treats += 1
            self.save_settings()
        if auto_started:
            view.flash()
        if s.popup_card and view.isVisible() and not self._approval_on_screen():
            e = self.engine
            if auto_started:
                actions = [("Pause", e.pause, True), ("Skip", e.skip, False)]
            else:
                actions = [(f"Start {upcoming.label.lower()}", e.start, True), ("Skip", e.skip, False)]
            accent = mode_color(self.theme, s, upcoming.value)
            self.toast.show_message(title, body, accent, actions, view.anchor_rect_global(),
                                    timeout_ms=30000 if rate or extras else 20000, rate=rate, extras=extras)

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
        self._maybe_offer_review()

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
        project = approval.session.project
        if approval.kind != "reply":  # a reply is optional: the card itself is the "Claude finished" news
            self.view.flash(2)
            if self.settings.sound_enabled:
                self.notifier.play("Soft", self.settings.volume, 1)
            if approval.kind == "question":
                self.notifier.toast(f"{project}: Claude has a question", approval.detail)
            else:
                self.notifier.toast(f"{project}: Claude needs your OK", self._approval_text(approval))
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
        if self.pacer.should_hold():  # quiet focus: it'll be on your break card
            if s.agent_notify_finish and seconds >= s.agent_notify_after_s:
                self.pacer.hold("finished", session.project, finished_detail(seconds))
            return
        if self.view.isVisible() and self.view.pomo is not None:
            self.view.pomo.celebrate()
        if not s.agent_notify_finish or seconds < s.agent_notify_after_s:
            return
        title = f"{session.project}: Claude finished"
        if self.agents.awaiting_reply(session):  # its card already shows Claude's words, with a reply box
            self.notifier.toast(title, session.message[:200] or "Your turn when you're ready.")
            return
        body = f"Done after {fmt_clock(seconds)}. Your turn when you're ready."
        actions = [("OK", lambda: None, False)]
        if self.engine.mode.is_break:
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
        title = f"{session.project}: Claude is waiting for you"
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
            f"Claude has been working for {plural(minutes, 'minute')}. Good moment for your break? "
            "This focus session counts as done, and Pomo will tell you when Claude finishes.",
            mode_color(self.theme, self.settings, "short"),
            [("Break now", self.engine.finish_now, True), ("Keep focusing", lambda: None, False)],
            self.view.anchor_rect_global(), timeout_ms=30000)

    def _on_suggest_focus(self, agent) -> None:
        if not self.settings.popup_card or not self.view.isVisible() or self._approval_on_screen():
            return
        self.toast.show_message(
            f"{agent.project} is on a long task",
            "Use the wait for a focus session of your own? Pomo will hold Claude's updates until your break.",
            mode_color(self.theme, self.settings, "focus"),
            [("Start focus", self._start_focus_now, True), ("Not now", lambda: None, False)],
            self.view.anchor_rect_global(), timeout_ms=30000)

    # ------------------------------------------------------- focus helpers
    def _rate_session(self, rating: int) -> None:
        self.storage.rate_last_focus(rating)
        if rating == 3:
            self.view.pomo.hello()
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
        self.away.configure(focusing, self.settings.away_after_min * 60)

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
        """Open Ask Pomo and send a question (when a key is set; otherwise the key panel shows)."""
        self.open_chat()
        if self.chat is not None and self.chat.worker is None and self.has_ai_key():
            self.chat.send(prompt)

    def _on_agents_changed(self) -> None:
        self._update_tray()
        self.view.update()

    def _on_quit(self) -> None:
        self.agents.shutdown()
        self.guard.set_active(False)  # saves what it counted
        self.hotkeys.unregister_all()
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
