"""The panel's System tab, and the quick-settings card Home shows: the PC's volume, sound output,
microphone, screen brightness and Bluetooth, plus buttons to lock the PC or take a screenshot.

Every call to Windows goes through system.Jobs, off the UI thread. A slider sends its level a few times
a second while you drag it, and the readings Pomo takes every couple of seconds (so the keyboard's volume
keys show up here too) never pull the handle out from under you.
"""
from __future__ import annotations

import time
from dataclasses import replace

from PySide6.QtCore import QObject, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QComboBox, QListWidget, QListWidgetItem, QSlider, QWidget

from . import system
from .panel import Card, IconButton, Page, label, row
from .panel_pages import chip
from .theme import mode_color

PAGE_KEY = "system"
SEND_MS = 150       # while you drag a slider, its level goes out this often
HOLD_S = 1.5        # after you change something, readings leave it alone this long
LEVELS_EVERY = 2    # seconds between readings while the controls show
DEVICES_EVERY = 6   # seconds between looks at the outputs (headphones come and go)
SNIP_AFTER_MS = 250  # the panel has to be off the screen before the snip freezes it

NO_BRIGHTNESS = "Pomo can only change a built-in screen's brightness. Monitors have their own buttons for it."


def _close_panel(widget: QWidget) -> None:
    """Get the panel out of the way of what's opening (or of the screenshot)."""
    window = widget.window()
    if hasattr(window, "close_panel"):
        window.close_panel()


def _dot(color: QColor, filled: bool = True, size: int = 10) -> QIcon:
    """A small round marker for list rows: filled for the one in use or connected, a ring otherwise."""
    pixmap = QPixmap(size * 2, size * 2)  # drawn at twice the size, so it stays round on high-DPI screens
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.Antialiasing)
    if filled:
        p.setPen(Qt.NoPen)
        p.setBrush(color)
    else:
        p.setPen(QPen(color, 1.4))
        p.setBrush(Qt.NoBrush)
    p.drawEllipse(QRectF(1, 1, size - 2, size - 2))
    p.end()
    return QIcon(pixmap)


def _list() -> QListWidget:
    lst = QListWidget()
    lst.setIconSize(QSize(10, 10))
    lst.setWordWrap(True)
    lst.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    lst.setFocusPolicy(Qt.NoFocus)
    return lst


def _volume_note(levels: system.Levels, known: bool) -> str:
    """What the volume shows instead of its number: "Muted", or "Off" with nothing to play through."""
    if levels.muted:
        return "Muted"
    return "Off" if known and levels.volume is None else ""


def _placeholder(lst: QListWidget, text: str) -> None:
    item = QListWidgetItem(text)
    item.setFlags(Qt.NoItemFlags)
    lst.addItem(item)


# ------------------------------------------------------------------ shared pieces
class SystemModel(QObject):
    """What the controls show, kept in step with Windows: one for the System tab, one for the Home card.

    Readings run in the background and arrive through the signals. A change shows at once, and a reading
    taken just before it can't undo it on screen.
    """

    levels_changed = Signal(object)     # system.Levels
    devices_changed = Signal(object)    # list[system.AudioDevice]
    bluetooth_changed = Signal(object)  # list[system.PairedDevice] | None

    def __init__(self, backend=None, parent: QObject | None = None):
        super().__init__(parent)
        self.sys = backend or system
        self.jobs = system.Jobs(self)
        self.slow = system.Jobs(self, "pomo-bluetooth")  # Bluetooth takes a moment: kept out of the sliders' way
        self.levels = system.Levels()
        self.devices: list[system.AudioDevice] = []
        self.bluetooth: list[system.PairedDevice] | None = None
        self.levels_read = self.devices_read = self.bluetooth_read = False  # until then, None means "not yet"
        self._held = {"muted": 0.0, "mic_muted": 0.0}

    # ------------------------------------------------------------- reading
    def read_levels(self) -> None:
        self.jobs.run("levels", self.sys.read_levels, then=self._got_levels)

    def read_devices(self) -> None:
        self.jobs.run("devices", self.sys.output_devices, then=self._got_devices)

    def read_bluetooth(self) -> None:
        self.slow.run("bluetooth", self.sys.bluetooth_devices, then=self._got_bluetooth)

    def _got_levels(self, levels) -> None:
        levels = levels if isinstance(levels, system.Levels) else system.Levels()
        now = time.monotonic()
        keep = {name: getattr(self.levels, name) for name, until in self._held.items() if now < until}
        self.levels = replace(levels, **keep)
        self.levels_read = True
        self.levels_changed.emit(self.levels)

    def _got_devices(self, devices) -> None:
        self.devices = list(devices or [])
        self.devices_read = True
        self.devices_changed.emit(self.devices)

    def _got_bluetooth(self, devices) -> None:
        self.bluetooth = devices
        self.bluetooth_read = True
        self.bluetooth_changed.emit(devices)

    # ------------------------------------------------------------- changing
    def _show(self, **changes) -> None:
        for name in changes:
            self._held[name] = time.monotonic() + HOLD_S
        self.levels = replace(self.levels, **changes)
        self.levels_changed.emit(self.levels)

    def set_volume(self, level: int) -> None:
        self.levels = replace(self.levels, volume=level)
        self.jobs.run("volume", self.sys.set_volume, level)
        if self.levels.muted:  # like Windows' own slider: moving it unmutes
            self.set_muted(False)

    def set_muted(self, muted: bool) -> None:
        self._show(muted=muted)
        self.jobs.run("mute", self.sys.set_mute, muted)

    def toggle_mute(self) -> None:
        if self.levels_read and self.levels.volume is None:
            return  # nothing to mute
        self.set_muted(not self.levels.muted)

    def toggle_mic(self) -> None:
        if self.levels.mic_muted is None:
            return
        muted = not self.levels.mic_muted
        self._show(mic_muted=muted)
        self.jobs.run("mic", self.sys.set_mic_mute, muted)

    def set_brightness(self, level: int) -> None:
        self.levels = replace(self.levels, brightness=level)
        self.jobs.run("brightness", self.sys.set_brightness, level)

    def set_output(self, device_id: str | None) -> None:
        if not device_id or any(d.id == device_id and d.is_default for d in self.devices):
            return
        self._got_devices([replace(d, is_default=d.id == device_id) for d in self.devices])
        self.jobs.run("output", self.sys.set_default_output, device_id, then=self._output_set)

    def _output_set(self, _ok) -> None:
        self.read_devices()
        self.read_levels()  # each output keeps its own volume


class LevelRow(QWidget):
    """A glyph button, a 0-100 slider and its value.

    `changed` fires while you drag, at most every SEND_MS, and once more as you let go. Levels read back
    from Windows (set_level) wait while you're on the slider and for HOLD_S after, so a reading taken a
    moment before your change can't yank the handle back.
    """

    changed = Signal(int)

    def __init__(self, ctrl, glyph: str, tip: str, parent=None):
        super().__init__(parent)
        self.button = IconButton(ctrl, glyph, tip, 28)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setFocusPolicy(Qt.NoFocus)
        self.value = label("", "muted")
        self.value.setFixedWidth(40)
        self.value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.setLayout(row(self.button, self.slider, self.value, stretch_at=1))
        self.note = ""  # shown instead of the number, like "Muted"
        self._filling = False
        self._held_until = 0.0
        self._sent: int | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(SEND_MS)
        self._timer.timeout.connect(self._send)
        self.slider.valueChanged.connect(self._moved)
        self.slider.sliderReleased.connect(self._send)
        self._show()

    def _moved(self, _value: int) -> None:
        self._show()
        if self._filling:
            return
        self._held_until = time.monotonic() + HOLD_S
        if not self._timer.isActive():
            self._timer.start()

    def _send(self) -> None:
        self._timer.stop()
        value = self.slider.value()
        if value == self._sent:
            return  # let go without moving it
        self._sent = value
        self._held_until = time.monotonic() + HOLD_S
        self.changed.emit(value)

    def held(self) -> bool:
        return self.slider.isSliderDown() or self._timer.isActive() or time.monotonic() < self._held_until

    def set_level(self, value: int | None) -> None:
        if value is None or self.held():
            return
        self._filling = True
        self.slider.setValue(value)
        self._filling = False
        self._sent = value
        self._show()

    def set_note(self, note: str) -> None:
        self.note = note
        self._show()

    def _show(self) -> None:
        self.value.setText(self.note or f"{self.slider.value()}%")


# ------------------------------------------------------------------ the System tab
class SystemPage(Page):
    """Sound, the screen, Bluetooth, and a few things you reach for: lock, screenshot, mic."""

    def __init__(self, ctrl, backend=None):
        super().__init__(ctrl)
        self.model = m = SystemModel(backend, self)
        self._ticks = 0

        sound = Card(ctrl, "Sound")
        sound.action("gear", "Sound settings", lambda: self._open(m.sys.open_sound_settings))
        self.volume = LevelRow(ctrl, "speaker", "Mute")
        self.volume.button.clicked.connect(m.toggle_mute)
        self.volume.changed.connect(m.set_volume)
        self.outputs = _list()
        self.outputs.itemClicked.connect(lambda item: m.set_output(item.data(Qt.UserRole)))
        sound.body.addWidget(self.volume)
        sound.body.addWidget(label("Play sound through", "muted"))
        sound.body.addWidget(self.outputs, 1)
        self.add(sound, 1)

        display = Card(ctrl, "Display", 250)
        display.action("gear", "Display settings", lambda: self._open(m.sys.open_display_settings))
        self.brightness = LevelRow(ctrl, "sun", "Brightness")
        self.brightness.button.setAttribute(Qt.WA_TransparentForMouseEvents)  # just an icon here
        self.brightness.changed.connect(m.set_brightness)
        self.brightness.hide()
        self.display_note = label("Checking your screen…", "secondary", wrap=True)
        display.body.addWidget(self.brightness)
        display.body.addWidget(self.display_note)
        display.body.addStretch(1)
        self.add(display)

        bluetooth = Card(ctrl, "Bluetooth")
        bluetooth.action("refresh", "Look again", m.read_bluetooth)
        bluetooth.action("gear", "Bluetooth settings", lambda: self._open(m.sys.open_bluetooth_settings))
        self.bluetooth = _list()
        self.bluetooth.itemClicked.connect(lambda _item: self._open(m.sys.open_bluetooth_settings))
        bluetooth.body.addWidget(self.bluetooth, 1)
        self.add(bluetooth, 1)

        quick = Card(ctrl, "Quick actions", 210)
        self.lock = chip("Lock the PC", lambda: self._open(m.sys.lock_pc))
        self.snip = chip("Screenshot", self.screenshot)
        self.mic = chip("Mute mic", self._toggle_mic, checkable=True)
        self.sound_settings = chip("Sound settings", lambda: self._open(m.sys.open_sound_settings))
        for button in (self.lock, self.snip, self.mic, self.sound_settings):
            quick.body.addWidget(button)
        quick.body.addStretch(1)
        self.add(quick)

        m.levels_changed.connect(self._show_levels)
        m.devices_changed.connect(self._show_devices)
        m.bluetooth_changed.connect(self._show_bluetooth)
        self._show_levels(m.levels)
        self._show_devices(m.devices)
        self._show_bluetooth(m.bluetooth)

    # ------------------------------------------------------------- actions
    def _open(self, fn) -> None:
        _close_panel(self)
        fn()

    def screenshot(self) -> None:
        _close_panel(self)
        QTimer.singleShot(SNIP_AFTER_MS, self.model.sys.screenshot)

    def _toggle_mic(self) -> None:
        self.model.toggle_mic()
        self._show_levels(self.model.levels)  # the chip flipped itself; put it right if nothing changed

    # ------------------------------------------------------------- showing
    def _show_levels(self, levels: system.Levels) -> None:
        known = self.model.levels_read
        self.volume.setEnabled(levels.volume is not None or not known)
        self.volume.set_level(levels.volume)
        self.volume.set_note(_volume_note(levels, known))
        self.volume.button.glyph = "speaker_off" if levels.muted else "speaker"
        self.volume.button.setToolTip("Unmute" if levels.muted else "Mute")
        self.volume.button.update()
        if known:
            self.brightness.setVisible(levels.brightness is not None)
            self.display_note.setVisible(levels.brightness is None)
            self.display_note.setText(NO_BRIGHTNESS)
            self.brightness.set_level(levels.brightness)
        no_mic = known and levels.mic_muted is None
        self.mic.setEnabled(not no_mic)
        self.mic.setChecked(bool(levels.mic_muted))
        self.mic.setText("No microphone" if no_mic else "Mic muted" if levels.mic_muted else "Mute mic")
        self.mic.setToolTip("" if no_mic else "Unmute the microphone" if levels.mic_muted else "Mute the microphone")

    def _show_devices(self, devices: list[system.AudioDevice]) -> None:
        t = self.ctrl.theme
        accent = mode_color(t, self.ctrl.settings, "focus")
        self.outputs.clear()
        for device in devices:
            item = QListWidgetItem(_dot(accent if device.is_default else QColor(t.muted), device.is_default),
                                   device.name)
            item.setData(Qt.UserRole, device.id)
            item.setToolTip("Playing through this one" if device.is_default else "Click to play through this one")
            self.outputs.addItem(item)
        if not devices:
            _placeholder(self.outputs, "No speakers or headphones found." if self.model.devices_read
                         else "Looking for speakers…")

    def _show_bluetooth(self, devices: list[system.PairedDevice] | None) -> None:
        t = self.ctrl.theme
        accent = mode_color(t, self.ctrl.settings, "focus")
        self.bluetooth.clear()
        if not self.model.bluetooth_read:
            _placeholder(self.bluetooth, "Looking for your devices…")
            return
        if devices is None:
            _placeholder(self.bluetooth, "Bluetooth isn't available on this PC.")
            return
        for device in devices:
            state = "Connected" if device.connected else "Not connected"
            item = QListWidgetItem(_dot(accent if device.connected else QColor(t.muted), device.connected),
                                   f"{device.name}  ·  {state}")
            item.setToolTip("Connect or disconnect it in Bluetooth settings")
            self.bluetooth.addItem(item)
        if not devices:
            _placeholder(self.bluetooth, "No paired devices yet. Pair one in Bluetooth settings.")

    # ------------------------------------------------------------- page
    def refresh(self) -> None:
        self._ticks = 0
        self.model.read_levels()
        self.model.read_devices()
        self.model.read_bluetooth()

    def tick(self) -> None:
        self._ticks += 1
        if self._ticks % LEVELS_EVERY == 0:
            self.model.read_levels()
        if self._ticks % DEVICES_EVERY == 0:
            self.model.read_devices()

    def retheme(self) -> None:
        self._show_devices(self.model.devices)
        self._show_bluetooth(self.model.bluetooth)


# ------------------------------------------------------------------ the Home card
class ControlsCard(Card):
    """Home's quick settings, like a phone's: volume, brightness, where the sound goes, and a row of toggles."""

    def __init__(self, ctrl, width: int | None = 290, backend=None):
        super().__init__(ctrl, "Controls", width)
        self.model = m = SystemModel(backend, self)
        self._ticks = 0
        from .panel import PAGES
        if any(spec.key == PAGE_KEY for spec in PAGES):
            self.action("sliders", "More controls", self._more)

        self.volume = LevelRow(ctrl, "speaker", "Mute")
        self.volume.button.clicked.connect(m.toggle_mute)
        self.volume.changed.connect(m.set_volume)
        self.brightness = LevelRow(ctrl, "sun", "Brightness")
        self.brightness.button.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.brightness.changed.connect(m.set_brightness)
        self.brightness.hide()  # until Windows says this screen has one
        self.output = QComboBox()
        self.output.setFocusPolicy(Qt.NoFocus)
        self.output.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.output.setMinimumContentsLength(8)
        self.output.setToolTip("Play sound through")
        self.output.activated.connect(lambda i: m.set_output(self.output.itemData(i)))
        self.lock = chip("Lock", lambda: (_close_panel(self), m.sys.lock_pc()))
        self.lock.setToolTip("Lock the PC")

        self.ultra = IconButton(ctrl, "bolt", "Ultra focus", 32)
        self.ultra.clicked.connect(self._ultra)
        self.mic = IconButton(ctrl, "mic", "Mute the microphone", 32)
        self.mic.clicked.connect(m.toggle_mic)
        self.snip = IconButton(ctrl, "crop", "Screenshot", 32)
        self.snip.clicked.connect(self.screenshot)
        self.sticky = IconButton(ctrl, "sticky", "New sticky note", 32)
        self.sticky.clicked.connect(lambda: ctrl.stickies.new_note())
        self.sounds = IconButton(ctrl, "headphones", "Focus sounds", 32)
        self.sounds.clicked.connect(lambda: ctrl.open_sounds_menu(QCursor.pos()))

        self.body.addWidget(self.volume)
        self.body.addWidget(self.brightness)
        self.body.addLayout(row(self.output, self.lock, stretch_at=0))
        self.body.addStretch(1)
        self.body.addLayout(row(self.ultra, None, self.mic, None, self.snip, None, self.sticky, None, self.sounds))

        m.levels_changed.connect(self._show_levels)
        m.devices_changed.connect(self._show_devices)
        self._show_levels(m.levels)
        self._show_devices(m.devices)
        self._sync_ultra()

    # ------------------------------------------------------------- actions
    def _more(self) -> None:
        window = self.window()
        if hasattr(window, "show_page"):
            window.show_page(PAGE_KEY)

    def _ultra(self) -> None:
        self.ctrl.toggle_ultra()
        self._sync_ultra()

    def _sync_ultra(self) -> None:
        on = bool(getattr(self.ctrl.settings, "ultra_on", False))
        self.ultra.lit = on
        self.ultra.setToolTip("Leave Ultra focus" if on else "Ultra focus")
        self.ultra.update()

    def screenshot(self) -> None:
        _close_panel(self)
        QTimer.singleShot(SNIP_AFTER_MS, self.model.sys.screenshot)

    # ------------------------------------------------------------- showing
    def _show_levels(self, levels: system.Levels) -> None:
        known = self.model.levels_read
        self.volume.setEnabled(levels.volume is not None or not known)
        self.volume.set_level(levels.volume)
        self.volume.set_note(_volume_note(levels, known))
        self.volume.button.glyph = "speaker_off" if levels.muted else "speaker"
        self.volume.button.setToolTip("Unmute" if levels.muted else "Mute")
        self.volume.button.update()
        if known:
            self.brightness.setVisible(levels.brightness is not None)
            self.brightness.set_level(levels.brightness)
        self.mic.setEnabled(levels.mic_muted is not None or not known)
        self.mic.glyph = "mic_off" if levels.mic_muted else "mic"
        self.mic.lit = bool(levels.mic_muted)
        self.mic.setToolTip("No microphone found" if known and levels.mic_muted is None else
                            "Unmute the microphone" if levels.mic_muted else "Mute the microphone")
        self.mic.update()

    def _show_devices(self, devices: list[system.AudioDevice]) -> None:
        self.output.blockSignals(True)
        self.output.clear()
        for device in devices:
            self.output.addItem(device.name, device.id)
            self.output.setItemData(self.output.count() - 1, device.name, Qt.ToolTipRole)
            if device.is_default:
                self.output.setCurrentIndex(self.output.count() - 1)
        if not devices:
            self.output.addItem("No speakers found" if self.model.devices_read else "Looking…")
        self.output.setEnabled(bool(devices))
        self.output.blockSignals(False)

    # ------------------------------------------------------------- card
    def refresh(self) -> None:
        self._ticks = 0
        self.model.read_levels()
        self.model.read_devices()
        self._sync_ultra()

    def tick(self) -> None:
        self._ticks += 1
        if self._ticks % LEVELS_EVERY == 0:
            self.model.read_levels()
        if self._ticks % DEVICES_EVERY == 0:
            self.model.read_devices()
        self._sync_ultra()


def register_pages() -> None:
    from .panel import register
    register(PAGE_KEY, "System", "sliders", SystemPage)
