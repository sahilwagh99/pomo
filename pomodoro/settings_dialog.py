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
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import APP_NAME, APP_VERSION
from .config import ORB_SIZES, SOUND_NAMES, Settings
from .stats import plural
from .theme import theme_for
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
        tabs = QTabWidget()
        tabs.addTab(self._timer_tab(), "Timer")
        tabs.addTab(self._alerts_tab(), "Alerts")
        tabs.addTab(self._appearance_tab(), "Appearance")
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
        self.theme_box.addItems(["Dark", "Light"])
        self.size_box = QComboBox()
        self.size_box.addItems(list(ORB_SIZES))
        opacity_box, self.opacity = _slider(30, 100)
        self.fade = QCheckBox("Fade the timer while focusing, until you hover over it")
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
        form.addRow("Timer size", self.size_box)
        form.addRow("Opacity", opacity_box)
        form.addRow("", self.fade)
        form.addRow("", self.on_top)
        form.addRow("", self.show_task)
        form.addRow("Focus color", self.colors["focus"])
        form.addRow("Short break color", self.colors["short"])
        form.addRow("Long break color", self.colors["long"])
        form.addRow("", reset_colors)
        return page

    def _general_tab(self) -> QWidget:
        page, form = _form()
        self.autostart = QCheckBox(f"Start {APP_NAME} when Windows starts")
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
        form.addRow("", self.autostart)
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
        self.theme_box.setCurrentIndex(0 if s.theme == "dark" else 1)
        size_name = min(ORB_SIZES, key=lambda name: abs(ORB_SIZES[name] - s.orb_size))
        self.size_box.setCurrentText(size_name)
        self.opacity.setValue(s.opacity)
        self.fade.setChecked(s.fade_when_away)
        self.on_top.setChecked(s.always_on_top)
        self.show_task.setChecked(s.show_task)
        for mode, button in self.colors.items():
            button.set_value(getattr(s, f"{mode}_color"))
        self.autostart.setChecked(s.start_with_windows)

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
        s.theme = "dark" if self.theme_box.currentIndex() == 0 else "light"
        s.orb_size = ORB_SIZES[self.size_box.currentText()]
        s.opacity = self.opacity.value()
        s.fade_when_away = self.fade.isChecked()
        s.always_on_top = self.on_top.isChecked()
        s.show_task = self.show_task.isChecked()
        for mode, button in self.colors.items():
            setattr(s, f"{mode}_color", button.value)
        s.start_with_windows = self.autostart.isChecked()
        s.clamp()
        return s

    def _save(self) -> None:
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
        default = str(Path.home() / f"focusorb-history-{date.today().isoformat()}.csv")
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
