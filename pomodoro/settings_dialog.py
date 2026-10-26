"""Settings window."""
from __future__ import annotations

from pathlib import Path
from datetime import date

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QKeySequence, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import APP_NAME, APP_VERSION, agent_hooks, claude_hooks, garden, keystore, media
from .ai import KeyCheckWorker
from .config import FOCUS_SOUNDS, HOTKEYS, ORB_SIZES, OUTFITS, SOUND_NAMES, Settings
from .providers import PROVIDERS, default_model, model_for, model_label, provider, set_model
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


def _scrolled(page: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    area.setWidget(page)
    area.setMinimumWidth(page.sizeHint().width() + 4)
    area.setMinimumHeight(min(page.sizeHint().height() + 4, 480))  # taller tabs scroll the rest
    return area


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
        self._wanted_voice = ""
        # Windows' voices are looked up on a worker thread; the app (which outlives this window) passes them on
        ctrl.voices_found.connect(self._fill_voices)
        self.setWindowTitle(f"{APP_NAME} · Settings")
        self.setWindowIcon(ctrl.icon)
        self.setMinimumWidth(600)
        self.setAttribute(Qt.WA_DeleteOnClose)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)
        title = QLabel("Settings")
        title.setProperty("role", "h1")
        root.addWidget(title)
        self.tabs = tabs = QTabWidget()
        for build, name in ((self._timer_tab, "Timer"), (self._focus_tab, "Focus"), (self._alerts_tab, "Alerts"),
                            (self._appearance_tab, "Appearance"), (self._agents_tab, "Agents"), (self._ai_tab, "AI"),
                            (self._keys_tab, "Shortcuts"), (self._general_tab, "General")):
            tabs.addTab(_scrolled(build()), name)
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
        screen = self.screen().availableGeometry() if self.screen() else None
        if screen is not None:  # tall tabs scroll, so the window fits small screens
            self.resize(self.sizeHint().width(), min(self.sizeHint().height(), screen.height() - 60))

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

    def _focus_tab(self) -> QWidget:
        page, form = _form()
        self.rate_sessions = QCheckBox("Ask how each focus session went (one tap)")
        self.suggest_length = QCheckBox("Suggest a better focus length from my answers")
        self.rate_sessions.toggled.connect(self.suggest_length.setEnabled)
        self.away_after = _spin(0, 60, " min")
        self.away_after.setSpecialValueText("Off")
        self.focus_guard = QCheckBox("Notice distracting apps and sites during focus")
        self.distracting = QLineEdit()
        self.distracting.setPlaceholderText("youtube, reddit, netflix")
        self.focus_guard.toggled.connect(self.distracting.setEnabled)
        self.quiet_focus = QCheckBox("Hold agent updates until my break")
        self.break_card = QCheckBox("Offer breathing, parked thoughts and a game")
        self.break_recap = QCheckBox("Show where I was: my task, window and agent news")
        self.auto_task = QComboBox()
        for key, label in (("ask", "Ask me first"), ("auto", "Automatically"), ("off", "Off")):
            self.auto_task.addItem(label, key)
        self.quests_on = QCheckBox("Three small quests a day, with badges and outfits")
        self.cheer_on = QCheckBox("Cheer me on: a few words at halfway and near the end")
        form.addRow("After a session", self.rate_sessions)
        form.addRow("", self.suggest_length)
        form.addRow("During focus", self.cheer_on)
        form.addRow("Away from the PC", self.away_after)
        form.addRow("", _hint("Pauses the focus session when the keyboard and mouse have\n"
                              "been still this long, and gives that time back."))
        form.addRow("Distractions", self.focus_guard)
        form.addRow("Sites and apps", self.distracting)
        form.addRow("", _hint("Pomo only looks at which app or site is in front, never what's\n"
                              "in it, and keeps the names and minutes on this PC."))
        form.addRow("Quiet focus", self.quiet_focus)
        form.addRow("", _hint("Windows doesn't let apps turn on its own Focus or Do not disturb,\n"
                              "so Pomo keeps itself quiet. Permission requests still come through."))
        form.addRow("When a break starts", self.break_card)
        form.addRow("When a break ends", self.break_recap)
        form.addRow("", _hint("\"Where was I?\" brings back the window you were working in\n"
                              "with one click. Window titles stay in memory, never on disk."))
        form.addRow("Name sessions after", self.auto_task)
        form.addRow("", _hint("the project and git branch open in your editor, so your\n"
                              "dashboard shows focus per project with no typing."))
        form.addRow("Daily quests", self.quests_on)
        return page

    def _keys_tab(self) -> QWidget:
        page, form = _form()
        self.hotkeys_on = QCheckBox("Use global shortcuts (they work from any app)")
        form.addRow("", self.hotkeys_on)
        self.hotkey_edits: dict[str, QKeySequenceEdit] = {}
        for _action, (attr, label) in HOTKEYS.items():
            edit = QKeySequenceEdit()
            edit.setMaximumSequenceLength(1)
            edit.setClearButtonEnabled(True)
            self.hotkey_edits[attr] = edit
            form.addRow(label, edit)
        self.hotkeys_on.toggled.connect(lambda on: [e.setEnabled(on) for e in self.hotkey_edits.values()])
        problems = [f"{HOTKEYS[action][1]}: {why}" for action, why in self.ctrl.hotkey_failures.items()]
        if problems:
            warn = _hint("Not working right now:\n" + "\n".join(problems))
            warn.setStyleSheet(f"color: {self.ctrl.theme.danger};")
            form.addRow("", warn)
        form.addRow("", _hint("Shortcuts need Ctrl, Alt or Win. Ctrl+Alt+Shift keeps them\n"
                              "clear of the shortcuts other apps use."))
        if not self.ctrl.hotkeys.available:
            self.hotkeys_on.setEnabled(False)
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
            if self.ctrl.outfit_unlocked(key):
                self.outfit_box.addItem(label, key)
            else:  # still growing in the garden, or a quest reward
                self.outfit_box.addItem(f"{label}  (at {garden.unlock_hint(key)})", key)
                self.outfit_box.model().item(self.outfit_box.count() - 1).setEnabled(False)
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
        self.outfit_follows = QCheckBox("Pomo dresses for what you're doing")
        form.addRow("Pomo's outfit", self.outfit_box)
        form.addRow("", self.outfit_follows)
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
        self.more_agents: dict[str, tuple[QLabel, QPushButton]] = {}
        for key, info in agent_hooks.AGENTS.items():
            status = QLabel()
            status.setWordWrap(True)
            status.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            button = QPushButton()
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _=False, k=key: self._toggle_agent(k))
            self.more_agents[key] = (status, button)
            form.addRow(info.label, status)
            form.addRow("", button)
        form.addRow("", _hint("Codex: Pomo's hooks go in ~/.codex/hooks.json; run /hooks in Codex\n"
                              "once to trust them. Gemini: they go in ~/.gemini/settings.json.\n"
                              "Questions with options are answered in Pomo for Claude Code only."))
        self.agent_reviews = QCheckBox("When an agent changes files, suggest reviewing them as a focus task")
        form.addRow("", self.agent_reviews)
        form.addRow("", self.approvals)
        form.addRow("Wait for my answer", self.approval_wait)
        form.addRow("", _hint("If you don't answer in time, or choose \"In terminal\",\n"
                              "Claude Code asks in the terminal as usual."))
        form.addRow("", self.notify_finish)
        form.addRow("When longer than", self.notify_after)
        self.agent_questions = QCheckBox("Answer Claude's multiple-choice questions in Pomo")
        self.agent_reply = QCheckBox("Let me reply to Claude in Pomo when it finishes")
        self.agent_reply_wait = _spin(10, 300, " s")
        self.agent_reply.toggled.connect(self.agent_reply_wait.setEnabled)
        form.addRow("", self.agent_questions)
        form.addRow("", self.agent_reply)
        form.addRow("Claude waits", self.agent_reply_wait)
        form.addRow("", _hint("Only while you're away from your terminal or editor; there, Claude\n"
                              "Code asks and stops as usual. Pomo shows Claude's own words."))
        self.agent_break_hint = QCheckBox("Suggest a break while an agent works on a long task")
        self.agent_break_after = _spin(30, 1800, " s")
        self.agent_break_hint.toggled.connect(self.agent_break_after.setEnabled)
        form.addRow("", self.agent_break_hint)
        form.addRow("Long task after", self.agent_break_after)
        form.addRow("", _hint("Late in a focus session, Pomo offers your break then. With the\n"
                              "timer idle, it offers a focus session while you wait."))
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
        self._agent_states: dict[str, str] = {}
        for key, (label, button) in getattr(self, "more_agents", {}).items():
            info = agent_hooks.AGENTS[key]
            agent_state = agent_hooks.status(key)
            self._agent_states[key] = agent_state
            found = agent_hooks.detected(key)
            label.setText({
                "connected": f"Connected. {info.trust_hint}",
                "outdated": "Connected to an older Pomo location. Reconnect to update.",
                "off": "Not connected." + ("" if found else f" ({info.label} isn't installed on this PC yet.)"),
            }[agent_state])
            button.setText({"connected": f"Disconnect {info.short}", "outdated": f"Reconnect {info.short}",
                            "off": f"Connect {info.short}"}[agent_state])

    def _toggle_agent(self, key: str) -> None:
        info = agent_hooks.AGENTS[key]
        path = agent_hooks.settings_path(key)
        try:
            if self._agent_states.get(key) == "connected":
                agent_hooks.uninstall(key, path)
            else:
                answer = QMessageBox.question(
                    self, f"Connect {info.label}",
                    f"Pomo will add its hooks to\n{path}\n\n{info.label} will then send Pomo a short summary of "
                    "what each session does (prompts, tools, file names), and ask Pomo before showing a "
                    f"permission prompt. {info.trust_hint}",
                    QMessageBox.Ok | QMessageBox.Cancel, QMessageBox.Ok)
                if answer != QMessageBox.Ok:
                    return
                agent_hooks.install(key, path)
        except (claude_hooks.HookConfigError, OSError) as exc:
            QMessageBox.warning(self, f"{info.label} settings", str(exc))
        self._refresh_hooks_status()

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
        self.provider_box = QComboBox()
        for prov in PROVIDERS.values():
            self.provider_box.addItem(prov.label, prov.key)
        self.model_box = QComboBox()
        self.model_box.setEditable(True)
        self.model_box.setInsertPolicy(QComboBox.NoInsert)
        self.load_models = QPushButton("Load models")
        self.load_models.setToolTip("Fetch the models your key can use")
        self.load_models.clicked.connect(self._check_key)
        model_row = QWidget()
        lay = QHBoxLayout(model_row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.model_box, 1)
        lay.addWidget(self.load_models)
        self.key_status = QLabel()
        self.key_status.setWordWrap(True)
        # without this the form squeezes the label to its narrowest wrap and clips the second line
        self.key_status.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.key_input = QLineEdit()
        self.key_input.setEchoMode(QLineEdit.Password)
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
        get_key.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(provider(self._provider()).keys_url)))
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
        open_chat = QPushButton("Open Ask Pomo")
        open_chat.clicked.connect(self.ctrl.open_chat)
        form.addRow("Provider", self.provider_box)
        form.addRow("Model", model_row)
        form.addRow("", _hint("Pick a suggestion or type any model ID. Load models fetches\n"
                              "the models your key can use."))
        form.addRow("Your key", self.key_status)
        form.addRow("", key_row)
        form.addRow("", key_actions)
        form.addRow("", _hint("Bring your own key: you only pay the provider for what you use.\n"
                              "Keys are stored encrypted for your Windows account and are only\n"
                              "ever sent to their own provider."))
        self.review_hour = _spin(0, 23, ":00")
        self.review_hour.setSpecialValueText("Off")
        form.addRow("Day review from", self.review_hour)
        form.addRow("", _hint("From this hour, Pomo offers a short review of your day.\n"
                              "It only asks the AI if you say yes."))
        form.addRow("", _hint("Ask Pomo sends your question and a short summary of your focus\n"
                              "stats, task names and agent activity to the provider you picked.\n"
                              "Nothing is sent until you ask something."))
        form.addRow("", open_chat)
        self.voice_input = QComboBox()
        for key, label in (("auto", "Automatic"), ("windows", "This PC (Windows speech recognition)"),
                           ("openai", "OpenAI, with your key"), ("gemini", "Gemini, with your key")):
            self.voice_input.addItem(label, key)
        self.voice_replies = QCheckBox("Read Pomo's replies aloud")
        self.voice_box = QComboBox()
        self.voice_box.addItem("A soft voice (Pomo picks)", "")
        try_voice = QPushButton("Try it")
        try_voice.clicked.connect(self._try_voice)
        voice_row = QWidget()
        lay = QHBoxLayout(voice_row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.voice_box, 1)
        lay.addWidget(try_voice)
        form.addRow("Talk to Pomo: hear me with", self.voice_input)
        form.addRow("", _hint("Hold the talk shortcut (Shortcuts tab) or the mic in Ask Pomo.\n"
                              "Automatic uses OpenAI or Gemini when Ask Pomo does, and this PC\n"
                              "otherwise. On this PC, your voice never leaves it."))
        form.addRow("", self.voice_replies)
        form.addRow("Voice", voice_row)
        self._load_voices()
        self._key_check: KeyCheckWorker | None = None
        self._pending_models = {k: model_for(self.ctrl.settings, k) for k in PROVIDERS}
        self._loaded_models: dict[str, list[str]] = {}
        self._shown_provider = self.ctrl.settings.ai_provider
        self.provider_box.currentIndexChanged.connect(self._provider_changed)
        return page

    def _refresh_update_status(self) -> None:
        self.update_status.setText(self.ctrl.update_status_text())
        self.update_now.setVisible(self.ctrl.update_ready())

    def _load_voices(self) -> None:
        """Fill the voice list with Windows' voices (asked once, in the background)."""
        voices = self.ctrl.voices
        if voices is not None:
            self._fill_voices(voices)
            return
        import threading

        from .voice import list_voices

        def look(ctrl=self.ctrl):
            ctrl.voices = list_voices()
            ctrl.voices_found.emit(ctrl.voices)

        threading.Thread(target=look, daemon=True).start()

    def _fill_voices(self, voices: list) -> None:
        current = self.voice_box.currentData() or self._wanted_voice
        for name, detail in voices:
            if self.voice_box.findData(name) < 0:
                self.voice_box.addItem(f"{name}  ·  {detail}", name)
        self._set_voice(current)

    def _set_voice(self, name: str) -> None:
        self._wanted_voice = name
        index = self.voice_box.findData(name)
        self.voice_box.setCurrentIndex(index if index >= 0 else 0)

    def _try_voice(self) -> None:
        self.ctrl._ensure_voice()
        self.ctrl.speaker.speak("Hi! I'm Pomo. This is how I'll read my replies to you.",
                                self.voice_box.currentData() or "")

    def _provider(self) -> str:
        return self.provider_box.currentData() or "anthropic"

    def _provider_changed(self) -> None:
        if self._shown_provider:  # keep what was typed for the provider we're leaving
            self._pending_models[self._shown_provider] = self.model_box.currentText().strip()
        self._shown_provider = self._provider()
        self._fill_models()
        self._refresh_key_status()

    def _fill_models(self) -> None:
        key = self._provider()
        prov = provider(key)
        current = self._pending_models.get(key) or default_model(key)
        self.model_box.blockSignals(True)
        self.model_box.clear()
        ids = [m for m, _label in prov.models] + [m for m in self._loaded_models.get(key, []) if m not in dict(prov.models)]
        if current not in ids:
            ids.insert(0, current)
        for model in ids:
            self.model_box.addItem(model)
            self.model_box.setItemData(self.model_box.count() - 1, model_label(key, model), Qt.ToolTipRole)
        self.model_box.setCurrentText(current)
        self.model_box.blockSignals(False)
        self.key_input.setPlaceholderText(f"Paste your {prov.short} key ({prov.key_hint})")

    def _refresh_key_status(self, text: str = "", tone: str | None = None) -> None:
        key_name = self._provider()
        prov = provider(key_name)
        key = self.ctrl.ai_key(key_name)
        if not text:
            if key:
                text = f"Saved ({keystore.mask(key)})."
            elif self.ctrl.has_ai_key(key_name):
                text = f"Using the key from your environment ({' or '.join(prov.env)})."
            else:
                text = f"No {prov.short} key yet. Paste yours below."
        color = {"good": self.ctrl.theme.success, "bad": self.ctrl.theme.danger}.get(tone)
        self.key_status.setStyleSheet(f"color: {color};" if color else "")
        self.key_status.setText(text)
        self.remove_key.setVisible(bool(key))
        busy = self._key_check is not None
        self.check_key.setEnabled(self.ctrl.has_ai_key(key_name) and not busy)
        self.load_models.setEnabled(self.ctrl.has_ai_key(key_name) and not busy)
        if getattr(self.ctrl, "chat", None) is not None:
            self.ctrl.chat.refresh_key()

    def _save_key(self) -> bool:
        key = self.key_input.text().strip()
        if not key:
            return False
        try:
            keystore.save_key(self.ctrl.key_path_for(self._provider()), key)
        except OSError as exc:
            QMessageBox.warning(self, "API key", str(exc))
            return False
        self.key_input.clear()
        self._check_key()
        return True

    def _check_key(self) -> None:
        key_name = self._provider()
        if self._key_check is not None or not self.ctrl.has_ai_key(key_name):
            return
        api_key = self.ctrl.ai_key(key_name) or self.ctrl.env_ai_key(key_name)
        # parented to the app, not this dialog, which is deleted when it closes
        self._key_check = worker = KeyCheckWorker(key_name, api_key, self.model_box.currentText().strip(), self.ctrl)
        worker.result.connect(self._on_key_checked)
        worker.models.connect(lambda models, k=key_name: self._on_models(k, models))
        worker.finished.connect(worker.deleteLater)
        worker.start()
        self._refresh_key_status(f"Checking your key with {provider(key_name).short}…")

    def _on_models(self, key_name: str, models: list) -> None:
        self._loaded_models[key_name] = list(models)
        if key_name == self._provider():
            self._pending_models[key_name] = self.model_box.currentText().strip()
            self._fill_models()

    def _on_key_checked(self, status: str, message: str) -> None:
        self._key_check = None
        key_name = self._provider()
        if status == "bad" and self.ctrl.ai_key(key_name):
            keystore.delete_key(self.ctrl.key_path_for(key_name))  # don't keep a key the provider rejected
        self._refresh_key_status(message, "good" if status in ("ok", "warn") else "bad")

    def _remove_key(self) -> None:
        keystore.delete_key(self.ctrl.key_path_for(self._provider()))
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
        self.music_follows = QCheckBox("Play music while I focus, pause it on breaks")
        self.music_controls.toggled.connect(self.pomo_listens.setEnabled)
        self.music_controls.toggled.connect(self.music_follows.setEnabled)
        self.taskbar = QCheckBox("Show Pomo on the taskbar")
        self.sound_box = QComboBox()
        for key, label in FOCUS_SOUNDS.items():
            self.sound_box.addItem(label, key)
        sound_volume_box, self.sound_volume = _slider(0, 100)
        self.sound_follows = QCheckBox("Play them only while I focus")
        form.addRow("Startup", self.autostart)
        form.addRow("", _hint(f"Starts {APP_NAME} when you sign in to Windows."))
        self.auto_update = QCheckBox("Get new versions ready and offer a one-click update")
        self.update_status = QLabel()
        self.update_status.setWordWrap(True)
        self.update_status.setProperty("role", "secondary")
        self.update_status.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        check_now = QPushButton("Check now")
        check_now.clicked.connect(lambda: self.ctrl.check_for_updates(manual=True))
        self.update_now = QPushButton("Update now")
        self.update_now.setObjectName("primary")
        self.update_now.clicked.connect(self.ctrl.install_update)
        update_row = QWidget()
        lay = QHBoxLayout(update_row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(check_now)
        lay.addWidget(self.update_now)
        lay.addStretch(1)
        form.addRow("Updates", self.auto_update)
        form.addRow("", self.update_status)
        form.addRow("", update_row)
        form.addRow("", _hint("Pomo asks GitHub for a new version every few hours. Updating\n"
                              "takes one click and keeps your history and settings."))
        self.ctrl.update_status_changed.connect(self._refresh_update_status)
        self._refresh_update_status()
        form.addRow("Taskbar", self.taskbar)
        form.addRow("", _hint("The countdown shows as progress on Pomo's taskbar button,\n"
                              "with reset, play/pause and skip when you hover it."))
        form.addRow("Focus sounds", self.sound_box)
        form.addRow("Sound volume", sound_volume_box)
        form.addRow("", self.sound_follows)
        form.addRow("", _hint("A sound you pick plays on a loop until you turn it off. Rain and\n"
                              "café are recordings that come with Pomo; brown noise and the\n"
                              "lo-fi loop are made on this PC. Nothing is downloaded."))
        form.addRow("Music", self.music_controls)
        form.addRow("", self.pomo_listens)
        form.addRow("", self.music_follows)
        if media.available():
            form.addRow("", _hint("Works with Spotify, YouTube in your browser and any app in\n"
                                  "Windows' media controls. Nothing leaves your PC."))
        else:
            self.music_controls.setEnabled(False)
            self.pomo_listens.setEnabled(False)
            self.music_follows.setEnabled(False)
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
        self.outfit_follows.setChecked(s.outfit_follows)
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
        self.auto_update.setChecked(s.auto_update)
        self.music_follows.setChecked(s.music_follows)
        self.music_follows.setEnabled(s.music_controls and media.available())
        self.rate_sessions.setChecked(s.rate_sessions)
        self.suggest_length.setChecked(s.suggest_length)
        self.suggest_length.setEnabled(s.rate_sessions)
        self.away_after.setValue(s.away_after_min)
        self.focus_guard.setChecked(s.focus_guard)
        self.distracting.setText(", ".join(s.distracting))
        self.distracting.setCursorPosition(0)
        self.distracting.setEnabled(s.focus_guard)
        self.quiet_focus.setChecked(s.quiet_focus)
        self.break_card.setChecked(s.break_card)
        self.break_recap.setChecked(s.break_recap)
        self.cheer_on.setChecked(s.cheer_on)
        self.auto_task.setCurrentIndex(max(0, self.auto_task.findData(s.auto_task)))
        self.quests_on.setChecked(s.quests_on)
        self.agent_reviews.setChecked(s.agent_reviews)
        self.taskbar.setChecked(s.taskbar_button)
        self.sound_box.setCurrentIndex(max(0, self.sound_box.findData(s.focus_sound)))
        self.sound_volume.setValue(s.focus_sound_volume)
        self.sound_follows.setChecked(s.focus_sound_only_focus)
        self.voice_input.setCurrentIndex(max(0, self.voice_input.findData(s.voice_input)))
        self.voice_replies.setChecked(s.voice_replies)
        self._set_voice(s.voice_name)
        self.hotkeys_on.setChecked(s.hotkeys_on)
        for attr, edit in self.hotkey_edits.items():
            edit.setKeySequence(QKeySequence.fromString(getattr(s, attr), QKeySequence.PortableText))
            edit.setEnabled(s.hotkeys_on)
        self.agent_questions.setChecked(s.agent_questions)
        self.agent_reply.setChecked(s.agent_reply)
        self.agent_reply_wait.setValue(s.agent_reply_wait_s)
        self.agent_reply_wait.setEnabled(s.agent_reply)
        self.agent_break_hint.setChecked(s.agent_break_hint)
        self.agent_break_after.setValue(s.agent_break_after_s)
        self.agent_break_after.setEnabled(s.agent_break_hint)
        self.review_hour.setValue(s.review_hour)
        self.music_controls.setChecked(s.music_controls)
        self.pomo_listens.setChecked(s.pomo_listens)
        self.pomo_listens.setEnabled(s.music_controls and media.available())
        self.approvals.setChecked(s.agent_approvals)
        self.approval_wait.setValue(s.approval_wait_s)
        self.notify_finish.setChecked(s.agent_notify_finish)
        self.notify_after.setValue(s.agent_notify_after_s)
        self._pending_models = {k: model_for(s, k) for k in PROVIDERS}
        self._shown_provider = ""
        self.provider_box.setCurrentIndex(max(0, self.provider_box.findData(s.ai_provider)))
        self._shown_provider = self._provider()
        self._fill_models()
        self._refresh_key_status()

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
        s.outfit_follows = self.outfit_follows.isChecked()
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
        s.auto_update = self.auto_update.isChecked()
        s.music_follows = self.music_follows.isChecked()
        s.rate_sessions = self.rate_sessions.isChecked()
        s.suggest_length = self.suggest_length.isChecked()
        s.away_after_min = self.away_after.value()
        s.focus_guard = self.focus_guard.isChecked()
        s.distracting = [w.strip().lower() for w in self.distracting.text().split(",") if w.strip()]
        s.quiet_focus = self.quiet_focus.isChecked()
        s.break_card = self.break_card.isChecked()
        s.break_recap = self.break_recap.isChecked()
        s.cheer_on = self.cheer_on.isChecked()
        s.auto_task = self.auto_task.currentData()
        s.quests_on = self.quests_on.isChecked()
        s.agent_reviews = self.agent_reviews.isChecked()
        s.taskbar_button = self.taskbar.isChecked()
        s.focus_sound = self.sound_box.currentData()
        s.focus_sound_volume = self.sound_volume.value()
        s.focus_sound_only_focus = self.sound_follows.isChecked()
        s.voice_input = self.voice_input.currentData()
        s.voice_replies = self.voice_replies.isChecked()
        s.voice_name = self.voice_box.currentData() or ""
        s.hotkeys_on = self.hotkeys_on.isChecked()
        for attr, edit in self.hotkey_edits.items():
            setattr(s, attr, edit.keySequence().toString(QKeySequence.PortableText))
        s.agent_questions = self.agent_questions.isChecked()
        s.agent_reply = self.agent_reply.isChecked()
        s.agent_reply_wait_s = self.agent_reply_wait.value()
        s.agent_break_hint = self.agent_break_hint.isChecked()
        s.agent_break_after_s = self.agent_break_after.value()
        s.review_hour = self.review_hour.value()
        s.music_controls = self.music_controls.isChecked()
        s.pomo_listens = self.pomo_listens.isChecked()
        s.agent_approvals = self.approvals.isChecked()
        s.approval_wait_s = self.approval_wait.value()
        s.agent_notify_finish = self.notify_finish.isChecked()
        s.agent_notify_after_s = self.notify_after.value()
        self._pending_models[self._provider()] = self.model_box.currentText().strip()
        s.ai_provider = self._provider()
        for key_name, model in self._pending_models.items():
            set_model(s, key_name, model)
        s.clamp()
        return s

    def _save(self) -> None:
        if self.key_input.text().strip():  # Enter in the key field lands here too
            try:
                keystore.save_key(self.ctrl.key_path_for(self._provider()), self.key_input.text().strip())
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
