"""Settings window."""
from __future__ import annotations

from pathlib import Path
from datetime import date

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import APP_NAME, APP_VERSION, claude_hooks, keystore, media
from .ai import KEYS_URL, KeyCheckWorker
from .config import AI_MODELS, ORB_SIZES, OUTFITS, SOUND_NAMES, Settings
from .stats import plural
from .theme import THEMES, theme_for
from .winutil import set_dark_title_bar


class ColorButton(QPushButton):
    def __init__(self, default: QColor):
        super().__init__()
        self.value = ""
        self.default = QColor(default)
        self.setCursor(Qt.PointingHandCursor)
        self.clicked.connect(self._pick)

    def color(self) -> QColor:
        return QColor(self.value) if self.value else QColor(self.default)

    def set_value(self, value: str) -> None:
        self.value = value if value and QColor.isValidColorName(value) else ""
        pm = QPixmap(16, 16)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(self.color())
        p.drawRoundedRect(1, 1, 14, 14, 4, 4)
        p.end()
        self.setIcon(QIcon(pm))
        self.setText(self.value.upper() if self.value else "Default")

    def _pick(self) -> None:
        color = QColorDialog.getColor(self.color(), self.window(), "Choose a color")
        if color.isValid():
            self.set_value(color.name())


def _spin(lo: int, hi: int, suffix: str = "") -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(lo, hi)
    spin.setSuffix(suffix)
    spin.setMinimumWidth(150)
    return spin


def _slider(lo: int, hi: int) -> tuple[QWidget, QSlider]:
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    slider = QSlider(Qt.Horizontal)
    slider.setRange(lo, hi)
    value = QLabel()
    value.setMinimumWidth(40)
    value.setProperty("role", "secondary")
    slider.valueChanged.connect(lambda v: value.setText(f"{v}%"))
    lay.addWidget(slider, 1)
    lay.addWidget(value)
    return box, slider


def _hint(text: str) -> QLabel:
    # explicit line breaks: word-wrapped labels get clipped inside QFormLayout
    label = QLabel(text)
    label.setProperty("role", "muted")
    return label


def _form() -> tuple[QWidget, QFormLayout]:
    page = QWidget()
    form = QFormLayout(page)
    form.setContentsMargins(20, 18, 20, 18)
    form.setHorizontalSpacing(18)
    form.setVerticalSpacing(12)
    form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
    return page, form


class SettingsDialog(QDialog):
    def __init__(self, ctrl):
        super().__init__(None)
        self.ctrl = ctrl
        self.setWindowTitle(f"{APP_NAME} · Settings")
        self.setWindowIcon(ctrl.icon)
        self.setMinimumWidth(540)
        self.setAttribute(Qt.WA_DeleteOnClose)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)
        title = QLabel("Settings")
        title.setProperty("role", "h1")
        root.addWidget(title)
        self.tabs = tabs = QTabWidget()
        tabs.addTab(self._timer_tab(), "Timer")
        tabs.addTab(self._alerts_tab(), "Alerts")
        tabs.addTab(self._appearance_tab(), "Appearance")
        tabs.addTab(self._agents_tab(), "Agents")
        tabs.addTab(self._ai_tab(), "AI")
        tabs.addTab(self._general_tab(), "General")
        root.addWidget(tabs, 1)

        buttons = QHBoxLayout()
        defaults = QPushButton("Restore defaults")
        defaults.setObjectName("ghost")
        defaults.clicked.connect(self._restore_defaults)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save")
        save.setObjectName("primary")
        save.setDefault(True)
        save.clicked.connect(self._save)
        buttons.addWidget(defaults)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        root.addLayout(buttons)

        self._load(ctrl.settings)

    # ------------------------------------------------------------------ tabs
    def _timer_tab(self) -> QWidget:
        page, form = _form()
        self.focus_min = _spin(1, 180, " min")
        self.short_min = _spin(1, 60, " min")
        self.long_min = _spin(1, 120, " min")
        self.cycle = _spin(1, 12, " focus sessions")
        self.goal = _spin(1, 48, " pomodoros")
        self.auto_breaks = QCheckBox("Start breaks automatically")
        self.auto_focus = QCheckBox("Start the next focus session automatically")
        form.addRow("Focus length", self.focus_min)
        form.addRow("Short break", self.short_min)
        form.addRow("Long break", self.long_min)
        form.addRow("Long break after", self.cycle)
        form.addRow("Daily goal", self.goal)
        form.addRow("", _hint("The daily goal is how many pomodoro turns you plan to finish\n"
                              "today. The timer and dashboard track your progress against it."))
        form.addRow("", self.auto_breaks)
        form.addRow("", self.auto_focus)
        form.addRow("", _hint("Tip: double-click the time on the timer to type a new one,\n"
                              "or scroll over it to add or remove a minute."))
        return page

    def _alerts_tab(self) -> QWidget:
        page, form = _form()
        self.toasts = QCheckBox("Show Windows notifications")
        self.popup = QCheckBox("Show a pop-up card next to the timer")
        self.sound = QCheckBox("Play a sound")
        self.sound_name = QComboBox()
        self.sound_name.addItems(SOUND_NAMES)
        preview = QPushButton("Preview")
        preview.setCursor(Qt.PointingHandCursor)
        preview.clicked.connect(
            lambda: self.ctrl.notifier.play(self.sound_name.currentText(), self.volume.value(), 1))
        sound_row = QWidget()
        lay = QHBoxLayout(sound_row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.sound_name, 1)
        lay.addWidget(preview)
        volume_box, self.volume = _slider(0, 100)
        self.repeat = _spin(1, 5, " times")
        self.idle = _spin(0, 240, " min")
        self.idle.setSpecialValueText("Off")
        form.addRow("", self.toasts)
        form.addRow("", self.popup)
        form.addRow("", self.sound)
        form.addRow("Sound", sound_row)
        form.addRow("Volume", volume_box)
        form.addRow("Repeat sound", self.repeat)
        form.addRow("Idle reminder", self.idle)
        form.addRow("", _hint("The idle reminder nudges you when no timer has run for this long."))
        return page

    def _appearance_tab(self) -> QWidget:
        page, form = _form()
        self.theme_box = QComboBox()
        for theme in THEMES.values():
            self.theme_box.addItem(theme.label, theme.key)
        self.outfit_box = QComboBox()
        for key, label in OUTFITS.items():
            self.outfit_box.addItem(label, key)
        self.style_box = QComboBox()
        self.style_box.addItems(["Floating orb", "Top island"])
        self.buddy = QCheckBox("Show Pomo, your focus buddy")
        self.island_autohide = QCheckBox("Tuck the island away when idle")
        self.size_box = QComboBox()
        self.size_box.addItems(list(ORB_SIZES))
        opacity_box, self.opacity = _slider(30, 100)
        self.fade = QCheckBox("Fade while focusing, until hovered")
        self.on_top = QCheckBox("Keep the timer above other windows")
        self.show_task = QCheckBox("Show the current task on the timer")
        theme = theme_for(self.ctrl.settings.theme)
        self.colors = {
            "focus": ColorButton(QColor(theme.focus)),
            "short": ColorButton(QColor(theme.short)),
            "long": ColorButton(QColor(theme.long)),
        }
        reset_colors = QPushButton("Reset colors")
        reset_colors.setObjectName("ghost")
        reset_colors.clicked.connect(lambda: [b.set_value("") for b in self.colors.values()])
        form.addRow("Theme", self.theme_box)
        form.addRow("Timer style", self.style_box)
        form.addRow("", self.buddy)
        form.addRow("Pomo's outfit", self.outfit_box)
        form.addRow("", self.island_autohide)
        form.addRow("Orb size", self.size_box)
        form.addRow("Opacity", opacity_box)
        form.addRow("", self.fade)
        form.addRow("", self.on_top)
        form.addRow("", self.show_task)
        form.addRow("Focus color", self.colors["focus"])
        form.addRow("Short break color", self.colors["short"])
        form.addRow("Long break color", self.colors["long"])
        form.addRow("", reset_colors)
        return page

    def show_tab(self, name: str) -> None:
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i) == name:
                self.tabs.setCurrentIndex(i)

    def _agents_tab(self) -> QWidget:
        page, form = _form()
        self.hooks_status = QLabel()
        self.hooks_status.setWordWrap(True)
        self.hooks_button = QPushButton()
        self.hooks_button.setCursor(Qt.PointingHandCursor)
        self.hooks_button.clicked.connect(self._toggle_hooks)
        self.approvals = QCheckBox("Answer permission requests in Pomo")
        self.approval_wait = _spin(15, 300, " s")
        self.notify_finish = QCheckBox("Tell me when an agent finishes a long task")
        self.notify_after = _spin(0, 3600, " s")
        form.addRow("Claude Code", self.hooks_status)
        form.addRow("", self.hooks_button)
        form.addRow("", _hint("Connecting adds Pomo hooks to\n"
                              f"{claude_hooks.default_settings_path()}\n"
                              "Your other settings are kept, and a backup is\n"
                              "saved next to the file the first time."))
        form.addRow("", self.approvals)
        form.addRow("Wait for my answer", self.approval_wait)
        form.addRow("", _hint("If you don't answer in time, or choose \"In terminal\",\n"
                              "Claude Code asks in the terminal as usual."))
        form.addRow("", self.notify_finish)
        form.addRow("When longer than", self.notify_after)
        self._refresh_hooks_status()
        return page

    def _refresh_hooks_status(self) -> None:
        state = claude_hooks.status()
        listening = self.ctrl.agents.listening
        text = {
            "connected": "Connected. Pomo can see your Claude Code sessions.",
            "outdated": "Connected to an older Pomo location. Reconnect to update.",
            "off": "Not connected.",
        }[state]
        if not listening:
            text += "\nAnother Pomo is already receiving agent events, so this one can't."
        self.hooks_status.setText(text)
        self.hooks_button.setText({"connected": "Disconnect Claude Code", "outdated": "Reconnect Claude Code",
                                   "off": "Connect Claude Code"}[state])
        self._hooks_state = state

    def _toggle_hooks(self) -> None:
        path = claude_hooks.default_settings_path()
        try:
            if self._hooks_state == "connected":
                claude_hooks.uninstall(path)
            else:
                answer = QMessageBox.question(
                    self, "Connect Claude Code",
                    f"Pomo will add its hooks to\n{path}\n\nClaude Code will then send Pomo a short "
                    "summary of what each session does (prompts, tools, file names), and ask Pomo before "
                    "showing a permission prompt. New Claude Code sessions pick this up.",
                    QMessageBox.Ok | QMessageBox.Cancel, QMessageBox.Ok)
                if answer != QMessageBox.Ok:
                    return
                claude_hooks.install(path, approval_timeout=330)
        except (claude_hooks.HookConfigError, OSError) as exc:
            QMessageBox.warning(self, "Claude Code settings", str(exc))
        self._refresh_hooks_status()

    def _ai_tab(self) -> QWidget:
        page, form = _form()
        self.key_status = QLabel()
        self.key_status.setWordWrap(True)
        # without this the form squeezes the label to its narrowest wrap and clips the second line
        self.key_status.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.key_input = QLineEdit()
        self.key_input.setEchoMode(QLineEdit.Password)
        self.key_input.setPlaceholderText("Paste your key (sk-ant-…)")
        save_key = QPushButton("Save key")
        save_key.clicked.connect(self._save_key)
        key_row = QWidget()
        lay = QHBoxLayout(key_row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.key_input, 1)
        lay.addWidget(save_key)
        self.check_key = QPushButton("Check key")
        self.check_key.clicked.connect(self._check_key)
        get_key = QPushButton("Get a key")
        get_key.setObjectName("ghost")
        get_key.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(KEYS_URL)))
        self.remove_key = QPushButton("Remove")
        self.remove_key.setObjectName("ghost")
        self.remove_key.clicked.connect(self._remove_key)
        key_actions = QWidget()
        lay = QHBoxLayout(key_actions)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.check_key)
        lay.addWidget(get_key)
        lay.addWidget(self.remove_key)
        lay.addStretch(1)
        self.model_box = QComboBox()
        for model_id, label in AI_MODELS.items():
            self.model_box.addItem(label, model_id)
        open_chat = QPushButton("Open Ask Pomo")
        open_chat.clicked.connect(self.ctrl.open_chat)
        form.addRow("Your key", self.key_status)
        form.addRow("", key_row)
        form.addRow("", key_actions)
        form.addRow("", _hint("Bring your own key: Ask Pomo uses your Anthropic API key, so you\n"
                              "only pay Anthropic for what you use. The key is stored encrypted for\n"
                              "your Windows account and is only ever sent to Anthropic."))
        form.addRow("Model", self.model_box)
        form.addRow("", _hint("Ask Pomo sends your question and a short summary of your focus\n"
                              "stats, task names and agent activity to Anthropic. Nothing is sent\n"
                              "until you ask something."))
        form.addRow("", open_chat)
        self._key_check: KeyCheckWorker | None = None
        self._refresh_key_status()
        return page

    def _refresh_key_status(self, text: str = "", tone: str | None = None) -> None:
        key = self.ctrl.ai_key()
        if not text:
            if key:
                text = f"Saved ({keystore.mask(key)})."
            elif self.ctrl.has_ai_key():
                text = "Using the key from your environment (ANTHROPIC_API_KEY)."
            else:
                text = "No key yet. Paste yours below to chat with Pomo."
        color = {"good": self.ctrl.theme.success, "bad": self.ctrl.theme.danger}.get(tone)
        self.key_status.setStyleSheet(f"color: {color};" if color else "")
        self.key_status.setText(text)
        self.remove_key.setVisible(bool(key))
        self.check_key.setEnabled(self.ctrl.has_ai_key() and self._key_check is None)
        if getattr(self.ctrl, "chat", None) is not None:
            self.ctrl.chat.refresh_key()

    def _save_key(self) -> bool:
        key = self.key_input.text().strip()
        if not key:
            return False
        try:
            keystore.save_key(self.ctrl.ai_key_path, key)
        except OSError as exc:
            QMessageBox.warning(self, "API key", str(exc))
            return False
        self.key_input.clear()
        self._check_key()
        return True

    def _check_key(self) -> None:
        if self._key_check is not None or not self.ctrl.has_ai_key():
            return
        # parented to the app, not this dialog, which is deleted when it closes
        self._key_check = worker = KeyCheckWorker(self.ctrl.ai_key(), self.model_box.currentData(), self.ctrl)
        worker.result.connect(self._on_key_checked)
        worker.finished.connect(worker.deleteLater)
        worker.start()
        self._refresh_key_status("Checking your key with Anthropic…")

    def _on_key_checked(self, status: str, message: str) -> None:
        self._key_check = None
        if status == "bad" and self.ctrl.ai_key():
            keystore.delete_key(self.ctrl.ai_key_path)  # don't keep a key Anthropic rejected
        self._refresh_key_status(message, "good" if status in ("ok", "warn") else "bad")

    def _remove_key(self) -> None:
        keystore.delete_key(self.ctrl.ai_key_path)
        self._refresh_key_status()

    def done(self, result: int) -> None:
        if self._key_check is not None:
            self._key_check.result.disconnect(self._on_key_checked)  # it may finish after this dialog is gone
            self._key_check = None
        super().done(result)

    def _general_tab(self) -> QWidget:
        page, form = _form()
        self.autostart = QCheckBox(f"Run {APP_NAME} on startup")
        corner = QPushButton("Move timer to the bottom-right corner")
        corner.clicked.connect(self._move_to_corner)
        folder = QPushButton("Open data folder")
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.ctrl.data_dir))))
        export = QPushButton("Export history as CSV")
        export.clicked.connect(self._export)
        clear = QPushButton("Clear all history")
        clear.setObjectName("danger")
        clear.clicked.connect(self._clear_history)
        for button in (corner, folder, export, clear):
            button.setCursor(Qt.PointingHandCursor)
        self.music_controls = QCheckBox("Show what's playing, with music controls")
        self.pomo_listens = QCheckBox("Pomo listens along (headphones and a little sway)")
        self.music_controls.toggled.connect(self.pomo_listens.setEnabled)
        form.addRow("Startup", self.autostart)
        form.addRow("", _hint(f"Starts {APP_NAME} when you sign in to Windows."))
        form.addRow("Music", self.music_controls)
        form.addRow("", self.pomo_listens)
        if media.available():
            form.addRow("", _hint("Works with Spotify, YouTube in your browser and any app in\n"
                                  "Windows' media controls. Nothing leaves your PC."))
        else:
            self.music_controls.setEnabled(False)
            self.pomo_listens.setEnabled(False)
            form.addRow("", _hint("Music controls need Windows 10 or later."))
        form.addRow("Position", corner)
        form.addRow("Data", folder)
        form.addRow("", export)
        form.addRow("", clear)
        sessions = self.ctrl.storage.count()
        form.addRow("", _hint(f"{APP_NAME} {APP_VERSION}  ·  {plural(sessions, 'session')} recorded\n"
                              f"Data is stored in {self.ctrl.data_dir}"))
        return page

    # ---------------------------------------------------------- load / save
    def _load(self, s: Settings) -> None:
        self.focus_min.setValue(s.focus_min)
        self.short_min.setValue(s.short_min)
        self.long_min.setValue(s.long_min)
        self.cycle.setValue(s.sessions_before_long)
        self.goal.setValue(s.daily_goal)
        self.auto_breaks.setChecked(s.auto_start_breaks)
        self.auto_focus.setChecked(s.auto_start_focus)
        self.toasts.setChecked(s.desktop_notifications)
        self.popup.setChecked(s.popup_card)
        self.sound.setChecked(s.sound_enabled)
        self.sound_name.setCurrentText(s.sound_name)
        self.volume.setValue(s.volume)
        self.repeat.setValue(s.sound_repeat)
        self.idle.setValue(s.idle_reminder_min)
        self.theme_box.setCurrentIndex(max(0, self.theme_box.findData(s.theme)))
        self.outfit_box.setCurrentIndex(max(0, self.outfit_box.findData(s.pomo_outfit)))
        self.style_box.setCurrentIndex(1 if s.display_mode == "island" else 0)
        self.buddy.setChecked(s.show_buddy)
        self.island_autohide.setChecked(s.island_autohide)
        size_name = min(ORB_SIZES, key=lambda name: abs(ORB_SIZES[name] - s.orb_size))
        self.size_box.setCurrentText(size_name)
        self.opacity.setValue(s.opacity)
        self.fade.setChecked(s.fade_when_away)
        self.on_top.setChecked(s.always_on_top)
        self.show_task.setChecked(s.show_task)
        for mode, button in self.colors.items():
            button.set_value(getattr(s, f"{mode}_color"))
        self.autostart.setChecked(s.start_with_windows)
        self.music_controls.setChecked(s.music_controls)
        self.pomo_listens.setChecked(s.pomo_listens)
        self.pomo_listens.setEnabled(s.music_controls and media.available())
        self.approvals.setChecked(s.agent_approvals)
        self.approval_wait.setValue(s.approval_wait_s)
        self.notify_finish.setChecked(s.agent_notify_finish)
        self.notify_after.setValue(s.agent_notify_after_s)
        self.model_box.setCurrentIndex(max(0, self.model_box.findData(s.ai_model)))

    def _collect(self) -> Settings:
        # start from the live settings so runtime state (position, task, cycle) is kept
        s = self.ctrl.settings.copy()
        s.focus_min = self.focus_min.value()
        s.short_min = self.short_min.value()
        s.long_min = self.long_min.value()
        s.sessions_before_long = self.cycle.value()
        s.daily_goal = self.goal.value()
        s.auto_start_breaks = self.auto_breaks.isChecked()
        s.auto_start_focus = self.auto_focus.isChecked()
        s.desktop_notifications = self.toasts.isChecked()
        s.popup_card = self.popup.isChecked()
        s.sound_enabled = self.sound.isChecked()
        s.sound_name = self.sound_name.currentText()
        s.volume = self.volume.value()
        s.sound_repeat = self.repeat.value()
        s.idle_reminder_min = self.idle.value()
        s.theme = self.theme_box.currentData()
        s.pomo_outfit = self.outfit_box.currentData()
        s.display_mode = "island" if self.style_box.currentIndex() == 1 else "orb"
        s.show_buddy = self.buddy.isChecked()
        s.island_autohide = self.island_autohide.isChecked()
        s.orb_size = ORB_SIZES[self.size_box.currentText()]
        s.opacity = self.opacity.value()
        s.fade_when_away = self.fade.isChecked()
        s.always_on_top = self.on_top.isChecked()
        s.show_task = self.show_task.isChecked()
        for mode, button in self.colors.items():
            setattr(s, f"{mode}_color", button.value)
        s.start_with_windows = self.autostart.isChecked()
        s.music_controls = self.music_controls.isChecked()
        s.pomo_listens = self.pomo_listens.isChecked()
        s.agent_approvals = self.approvals.isChecked()
        s.approval_wait_s = self.approval_wait.value()
        s.agent_notify_finish = self.notify_finish.isChecked()
        s.agent_notify_after_s = self.notify_after.value()
        s.ai_model = self.model_box.currentData()
        s.clamp()
        return s

    def _save(self) -> None:
        if self.key_input.text().strip():  # Enter in the key field lands here too
            try:
                keystore.save_key(self.ctrl.ai_key_path, self.key_input.text().strip())
            except OSError as exc:
                QMessageBox.warning(self, "API key", str(exc))
        self.ctrl.apply_settings(self._collect())
        self.accept()

    def _restore_defaults(self) -> None:
        self._load(Settings())

    # ------------------------------------------------------------- actions
    def showEvent(self, event) -> None:
        super().showEvent(event)
        set_dark_title_bar(self, self.ctrl.theme.dark)

    def _move_to_corner(self) -> None:
        self.ctrl.orb.move_to_corner()
        self.ctrl.save_position()

    def _export(self) -> None:
        default = str(Path.home() / f"pomo-history-{date.today().isoformat()}.csv")
        path, _ = QFileDialog.getSaveFileName(self, "Export history", default, "CSV files (*.csv)")
        if path:
            n = self.ctrl.storage.export_csv(Path(path))
            QMessageBox.information(self, "Export complete", f"Saved {plural(n, 'session')} to\n{path}")

    def _clear_history(self) -> None:
        n = self.ctrl.storage.count()
        answer = QMessageBox.question(
            self, "Clear all history",
            f"Delete all {plural(n, 'recorded session')}? Your dashboard will start from zero. This cannot be undone.",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
        )
        if answer == QMessageBox.Yes:
            self.ctrl.storage.clear()
