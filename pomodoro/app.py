"""Application controller: owns the engine, storage and windows and wires them up."""
from __future__ import annotations

import time
from datetime import date
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import QApplication, QInputDialog, QMenu, QSystemTrayIcon

from . import APP_NAME
from .config import Settings
from .engine import Mode, State, TimerEngine
from .icons import app_icon, render_orb_icon
from .notifier import Notifier
from .orb import OrbWidget
from .stats import fmt_clock, plural
from .storage import Storage
from .theme import DARK, build_palette, build_qss, ensure_qss_assets, mode_color, theme_for
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


class FocusOrbApp(QObject):
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

        self.orb = OrbWidget(self)
        self.toast = ToastCard(self)
        self.dashboard = None
        self.settings_dialog = None

        self.tray: QSystemTrayIcon | None = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(self.icon, self)
            self._tray_menu = styled_menu(QMenu())
            self._tray_menu.aboutToShow.connect(lambda: self._populate_menu(self._tray_menu))
            self._populate_menu(self._tray_menu)
            self.tray.setContextMenu(self._tray_menu)
            self.tray.activated.connect(self._on_tray_activated)
            self.tray.messageClicked.connect(self.show_orb)
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
        self._idle_since: float | None = time.monotonic()
        self._idle_nudged = False
        self._tray_second = -1

        self.engine.session_logged.connect(self.storage.add)
        self.storage.changed.connect(self._on_history_changed)
        self.engine.finished.connect(self._on_finished)
        self.engine.changed.connect(self._on_engine_changed)
        self.engine.ticked.connect(self._on_tick)
        self.orb.dashboard_requested.connect(self.open_dashboard)
        self.orb.settings_requested.connect(self.open_settings)
        self.orb.menu_requested.connect(self._show_orb_menu)
        self.orb.moved.connect(self.save_position)
        self.orb.interacted.connect(self.toast.dismiss)
        qapp.aboutToQuit.connect(self._on_quit)
        qapp.screenRemoved.connect(lambda *_: QTimer.singleShot(400, self.orb.ensure_on_screen))
        qapp.primaryScreenChanged.connect(lambda *_: QTimer.singleShot(400, self.orb.ensure_on_screen))

        self.orb.place()
        self.orb.show()
        self._on_engine_changed()

    # ----------------------------------------------------------------- style
    def _apply_style(self) -> None:
        accent = mode_color(self.theme, self.settings, "focus")
        assets = ensure_qss_assets(self.theme, self.cache_dir)
        self.qapp.setPalette(build_palette(self.theme, accent))
        self.qapp.setStyleSheet(build_qss(self.theme, accent, assets))
        track = QColor(accent)
        track.setAlpha(70)
        self.icon = app_icon(accent, QColor(DARK.orb_face), track)
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
        pos = self.orb.pos()
        self.settings.orb_x, self.settings.orb_y = pos.x(), pos.y()
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
        self.orb.update()
        self._update_tray()
        if self.dashboard is not None:
            self.dashboard.refresh()

    def apply_settings(self, new: Settings) -> None:
        autostart_changed = new.start_with_windows != self.settings.start_with_windows
        self.settings.update_from(new)
        if autostart_changed and not winutil.set_start_with_windows(self.settings.start_with_windows):
            self.settings.start_with_windows = False
        self.theme = theme_for(self.settings.theme)
        self._apply_style()
        self.engine.apply_settings()
        self.orb.apply_settings()
        self.toast.update()
        self._update_tray()
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

    def open_settings(self) -> None:
        if self.settings_dialog is not None:
            self.settings_dialog.raise_()
            self.settings_dialog.activateWindow()
            return
        from .settings_dialog import SettingsDialog
        self.settings_dialog = SettingsDialog(self)
        self.settings_dialog.finished.connect(lambda _result: setattr(self, "settings_dialog", None))
        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()

    def show_orb(self) -> None:
        self.orb.show()
        self.orb.ensure_on_screen()
        self.orb.raise_()

    def hide_orb(self) -> None:
        if self.tray is not None:
            self.orb.hide()
            self.toast.dismiss()

    def edit_task(self) -> None:
        items = [self.engine.task] if self.engine.task else []
        items += [t for t in self.settings.recent_tasks if t not in items]
        text, ok = QInputDialog.getItem(None, "Current task", "What are you focusing on?", items or [""], 0, True)
        if ok:
            self.settings.remember_task(text)
            self.engine.task = self.settings.current_task
            self.save_settings()
            self.orb.update()

    def clear_task(self) -> None:
        self.settings.current_task = ""
        self.engine.task = ""
        self.save_settings()
        self.orb.update()

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

        goal = styled_menu(menu.addMenu(f"Daily goal: {self.settings.daily_goal}"))
        for g in GOAL_PRESETS:
            _action(goal, plural(g, "pomodoro"), lambda g=g: self.set_goal(g), checked=g == self.settings.daily_goal)
        goal.addSeparator()
        _action(goal, "Custom…", self._custom_goal)
        menu.addSeparator()

        _action(menu, "Dashboard\tD", self.open_dashboard)
        _action(menu, "Settings…", self.open_settings)
        menu.addSeparator()
        if self.orb.isVisible():
            _action(menu, "Hide timer", self.hide_orb, enabled=self.tray is not None)
        else:
            _action(menu, "Show timer", self.show_orb)
        _action(menu, f"Quit {APP_NAME}", self.quit)

    def _show_orb_menu(self, pos) -> None:
        menu = styled_menu(QMenu())
        self._populate_menu(menu)
        menu.exec(pos)
        menu.deleteLater()

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.Trigger:
            if self.orb.isVisible():
                self.orb.raise_()
                self.orb.flash(1)
            else:
                self.show_orb()
        elif reason == QSystemTrayIcon.DoubleClick:
            self.open_dashboard()

    # ---------------------------------------------------------------- engine
    def _on_tick(self) -> None:
        second = int(self.engine.remaining)
        if second != self._tray_second:
            self._tray_second = second
            self._update_tray()

    def _on_engine_changed(self) -> None:
        e = self.engine
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
        self.orb.update()
        self._update_tray()

    def _update_tray(self) -> None:
        if self.tray is None:
            return
        e = self.engine
        accent = mode_color(self.theme, self.settings, e.mode.value)
        track = QColor(accent)
        track.setAlpha(70)
        frac = e.fraction_left if e.state in (State.RUNNING, State.PAUSED) else 1.0
        self.tray.setIcon(QIcon(render_orb_icon(64, accent, frac, QColor(DARK.orb_face), track)))
        state = {State.RUNNING: "", State.PAUSED: " (paused)", State.IDLE: " (ready)", State.FINISHED: " (up next)"}[e.state]
        self.tray.setToolTip(
            f"{APP_NAME}\n{e.mode.label}{state} · {fmt_clock(e.remaining)}\n"
            f"{self.today_count}/{self.settings.daily_goal} pomodoros today"
        )

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
        if self.engine.task and upcoming is Mode.FOCUS:
            body += f"\nTask: {self.engine.task}"

        self.notifier.alert()
        self.notifier.toast(title, body)
        if auto_started:
            self.orb.flash()
        if s.popup_card and self.orb.isVisible():
            e = self.engine
            if auto_started:
                actions = [("Pause", e.pause, True), ("Skip", e.skip, False)]
            else:
                actions = [(f"Start {upcoming.label.lower()}", e.start, True), ("Skip", e.skip, False)]
            accent = mode_color(self.theme, s, upcoming.value)
            self.toast.show_message(title, body, accent, actions, self.orb.circle_rect_global())

    # ----------------------------------------------------------- housekeeping
    def _housekeeping(self) -> None:
        today = date.today()
        if today != self._today:
            self._today = today
            self.today_count = self.storage.completed_focus_count(today)
            if self.engine.state in (State.IDLE, State.FINISHED):
                self.engine.cycle_index = 0
                self.engine.changed.emit()
            self.orb.update()
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
            if self.settings.popup_card and self.orb.isVisible():
                accent = mode_color(self.theme, self.settings, "focus")
                actions = [("Start focus", self._start_focus_now, True), ("Later", lambda: None, False)]
                self.toast.show_message(title, body, accent, actions, self.orb.circle_rect_global())

    def _start_focus_now(self) -> None:
        if self.engine.mode is not Mode.FOCUS:
            self.engine.switch_mode(Mode.FOCUS)
        self.engine.start()

    def _on_quit(self) -> None:
        self.engine.shutdown()
        self.settings.cycle_index = self.engine.cycle_index
        self.settings.cycle_date = date.today().isoformat()
        self._save_timer.stop()
        self._save_now()
        if self.tray is not None:
            self.tray.hide()
        self.storage.close()
