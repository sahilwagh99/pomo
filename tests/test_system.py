"""System controls: the backend fails soft, Jobs runs calls off the UI thread, and the System tab and Home's
controls card drive a fake backend. Nothing here changes this PC's real volume, microphone, screen or lock."""
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from PySide6.QtTest import QTest

from pomodoro import system
from pomodoro.config import Settings
from pomodoro.panel_system import NO_BRIGHTNESS, SNIP_AFTER_MS, ControlsCard, LevelRow, SystemPage
from pomodoro.system import AudioDevice, Levels, PairedDevice
from pomodoro.theme import theme_for


def setUpModule():
    """Let timers other test modules left behind fire now, not in the middle of these tests."""
    for _ in range(3):
        try:
            QTest.qWait(10)
        except RuntimeError:  # their widgets are gone already
            pass


def wait_for(cond, timeout: float = 3.0) -> bool:
    end = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > end:
            return False
        QTest.qWait(5)
    return True


class FakeSystem:
    """Stands in for pomodoro.system: answers readings and remembers what it was asked to change."""

    def __init__(self, levels=None, devices=None, paired="default"):
        self.levels = levels or Levels(40, False, False, 70)
        self.devices = devices if devices is not None else [
            AudioDevice("spk", "Speakers", True), AudioDevice("hp", "Headphones", False)]
        self.paired = [PairedDevice("bt1", "Buds", True), PairedDevice("bt2", "Mouse", False)] \
            if paired == "default" else paired
        self.calls: list[tuple] = []
        self._lock = threading.Lock()

    def _note(self, *call) -> bool:
        with self._lock:
            self.calls.append(call)
        return True

    def called(self, name: str) -> list:
        with self._lock:
            return [c[1] if len(c) > 1 else None for c in self.calls if c[0] == name]

    def read_levels(self):
        return self.levels

    def output_devices(self):
        return list(self.devices)

    def bluetooth_devices(self):
        return self.paired

    def set_volume(self, level):
        return self._note("set_volume", level)

    def set_mute(self, muted):
        return self._note("set_mute", muted)

    def set_mic_mute(self, muted):
        return self._note("set_mic_mute", muted)

    def set_brightness(self, level):
        return self._note("set_brightness", level)

    def set_default_output(self, device_id):
        self.devices = [AudioDevice(d.id, d.name, d.id == device_id) for d in self.devices]
        return self._note("set_default_output", device_id)

    def lock_pc(self):
        return self._note("lock_pc")

    def screenshot(self):
        return self._note("screenshot")

    def open_sound_settings(self):
        return self._note("open_sound_settings")

    def open_bluetooth_settings(self):
        return self._note("open_bluetooth_settings")

    def open_display_settings(self):
        return self._note("open_display_settings")


class Stickies:
    def __init__(self):
        self.made = 0

    def new_note(self):
        self.made += 1


class Ctrl:
    """Just enough of the app for the System tab and the Home card."""

    def __init__(self):
        self.theme = theme_for("light")
        self.settings = Settings()
        self.stickies = Stickies()
        self.menus = []

    def save_settings(self):
        pass

    def toggle_ultra(self):
        self.settings.ultra_on = not self.settings.ultra_on

    def open_sounds_menu(self, pos):
        self.menus.append(pos)


# ------------------------------------------------------------------ backend
class BackendTests(unittest.TestCase):
    def test_everything_fails_soft_off_windows(self):
        with mock.patch.object(system, "WINDOWS", False):
            self.assertIsNone(system.get_volume())
            self.assertIsNone(system.get_mute())
            self.assertIsNone(system.get_mic_mute())
            self.assertIsNone(system.get_brightness())
            self.assertEqual(system.output_devices(), [])
            self.assertIsNone(system.bluetooth_devices())
            self.assertEqual(system.read_levels(), Levels())
            for setter, arg in ((system.set_volume, 50), (system.set_mute, True), (system.set_mic_mute, True),
                                (system.set_brightness, 50), (system.set_default_output, "id")):
                self.assertIs(setter(arg), False)
            for action in (system.lock_pc, system.screenshot, system.open_sound_settings,
                           system.open_bluetooth_settings, system.open_display_settings):
                self.assertIs(action(), False)

    @unittest.skipUnless(system.WINDOWS, "Windows only")
    def test_api_errors_become_defaults(self):
        def broken(*_args, **_kwargs):
            raise OSError("no such device")

        with mock.patch.object(system, "_endpoint_volume", broken):
            self.assertIsNone(system.get_volume())
            self.assertIsNone(system.get_mic_mute())
            self.assertIs(system.set_volume(30), False)
            self.assertIs(system.set_mute(True), False)
            self.assertIs(system.set_mic_mute(True), False)
        with mock.patch.object(system, "_create", broken):
            self.assertEqual(system.output_devices(), [])
            self.assertIs(system.set_default_output("{0.0.0.00000000}.{x}"), False)
        with mock.patch.object(system, "_wmi", broken):
            self.assertIsNone(system.get_brightness())
            self.assertIs(system.set_brightness(40), False)
        self.assertIs(system.set_default_output(""), False)

    def test_launchers_open_the_right_places(self):
        with mock.patch.object(system, "WINDOWS", True), \
                mock.patch.object(system.os, "startfile", create=True) as start:
            self.assertTrue(system.screenshot())
            self.assertTrue(system.open_sound_settings())
            self.assertTrue(system.open_bluetooth_settings())
            self.assertTrue(system.open_display_settings())
        self.assertEqual([c.args[0] for c in start.call_args_list],
                         ["ms-screenclip:", "ms-settings:sound", "ms-settings:bluetooth", "ms-settings:display"])
        with mock.patch.object(system, "WINDOWS", True), \
                mock.patch.object(system.os, "startfile", side_effect=OSError, create=True):
            self.assertFalse(system.screenshot())

    def test_lock_asks_windows_to_lock(self):
        lock = mock.Mock(return_value=1)
        fake = SimpleNamespace(user32=SimpleNamespace(LockWorkStation=lock))
        with mock.patch.object(system, "WINDOWS", True), mock.patch.object(system, "_dll", return_value=fake):
            self.assertTrue(system.lock_pc())
        lock.assert_called_once_with()

    def test_reads_variants(self):
        v = system.VARIANT()
        v.vt, v.v.u1 = system.VT_UI1, 87
        self.assertEqual(system._from_variant(v), 87)
        v = system.VARIANT()
        v.vt, v.v.i2 = system.VT_BOOL, -1
        self.assertIs(system._from_variant(v), True)
        v = system.VARIANT()
        v.vt, v.v.i4 = system.VT_UI4, -1
        self.assertEqual(system._from_variant(v), 0xFFFFFFFF)
        v = system.VARIANT()
        v.vt = system.VT_NULL
        self.assertIsNone(system._from_variant(v))

    def test_paired_devices_merge_and_sort(self):
        found = [
            ("c1", "OnePlus Buds", 0xA1, False),     # classic
            ("l1", "LE-OnePlus Buds", 0xB2, True),   # the same buds over LE, under another address
            ("c2", "Keyboard", 0xC3, False),
            ("l2", "Keyboard", 0xC3, False),         # the same address twice
            ("c3", "", 0, False),
        ]
        merged = system.merge_paired(found)
        self.assertEqual(merged, [
            PairedDevice("c1", "OnePlus Buds", True),
            PairedDevice("c3", "Bluetooth device", False),
            PairedDevice("c2", "Keyboard", False),
        ])


# ------------------------------------------------------------------ Jobs
class JobsTests(unittest.TestCase):
    def setUp(self):
        self.jobs = system.Jobs()
        self.got = []

    def tearDown(self):
        wait_for(lambda: not self.jobs.busy())
        self.jobs.deleteLater()

    def test_runs_off_the_ui_thread_and_answers_on_it(self):
        ui = threading.get_ident()
        self.jobs.run("who", threading.get_ident, then=lambda worker: self.got.append((worker, threading.get_ident())))
        self.assertTrue(wait_for(lambda: self.got))
        worker, answered_on = self.got[0]
        self.assertNotEqual(worker, ui)
        self.assertEqual(answered_on, ui)

    def test_the_newest_call_with_a_key_wins(self):
        gate = threading.Event()
        ran = []
        self.jobs.run("gate", gate.wait, 3)
        for level in (10, 20, 30):
            self.jobs.run("volume", ran.append, level)
        self.jobs.run("other", ran.append, "other")
        gate.set()
        self.assertTrue(wait_for(lambda: not self.jobs.busy()))
        self.assertEqual(ran, [30, "other"])

    def test_a_failing_call_answers_none(self):
        def boom():
            raise ValueError("nope")

        self.jobs.run("boom", boom, then=self.got.append)
        self.assertTrue(wait_for(lambda: self.got))
        self.assertEqual(self.got, [None])


# ------------------------------------------------------------------ the slider row
class LevelRowTests(unittest.TestCase):
    def setUp(self):
        self.row = LevelRow(Ctrl(), "speaker", "Mute")
        self.sent = []
        self.row.changed.connect(self.sent.append)

    def tearDown(self):
        self.row.deleteLater()

    def test_sends_a_few_times_a_second_not_every_step(self):
        for value in (10, 20, 30):
            self.row.slider.setValue(value)
        self.assertEqual(self.sent, [])
        self.assertTrue(wait_for(lambda: self.sent, 1.0))
        QTest.qWait(200)
        self.assertEqual(self.sent, [30])
        self.assertEqual(self.row.value.text(), "30%")

    def test_letting_go_sends_at_once(self):
        self.row.slider.setSliderDown(True)
        self.row.slider.setValue(55)
        self.row.slider.setSliderDown(False)
        self.assertEqual(self.sent, [55])

    def test_readings_wait_while_you_change_it(self):
        self.row.set_level(40)
        self.assertEqual(self.row.slider.value(), 40)
        self.assertEqual(self.sent, [])  # a reading isn't a change
        self.row.slider.setValue(70)
        self.row.set_level(40)  # taken before the change landed
        self.assertEqual(self.row.slider.value(), 70)
        self.assertTrue(wait_for(lambda: self.sent))
        self.row._held_until = 0.0  # time passes
        self.row.set_level(65)
        self.assertEqual(self.row.slider.value(), 65)
        self.assertEqual(self.sent, [70])

    def test_note_replaces_the_number(self):
        self.row.set_level(25)
        self.row.set_note("Muted")
        self.assertEqual(self.row.value.text(), "Muted")
        self.row.set_note("")
        self.assertEqual(self.row.value.text(), "25%")


# ------------------------------------------------------------------ the System tab
class SystemPageTests(unittest.TestCase):
    def make(self, fake: FakeSystem) -> SystemPage:
        self.fake, self.ctrl = fake, Ctrl()
        self.page = SystemPage(self.ctrl, fake)
        return self.page

    def tearDown(self):
        m = self.page.model
        wait_for(lambda: not m.jobs.busy() and not m.slow.busy())
        self.page.deleteLater()

    def shown(self, page: SystemPage) -> None:
        page.refresh()
        m = page.model
        self.assertTrue(wait_for(lambda: m.levels_read and m.devices_read and m.bluetooth_read))

    def test_cheap_to_build_and_waiting_shows(self):
        page = self.make(FakeSystem())
        self.assertFalse(page.model.jobs.busy())
        self.assertEqual(self.fake.calls, [])
        self.assertIn("Looking", page.bluetooth.item(0).text())
        self.assertTrue(page.brightness.isHidden())
        self.assertIn("Checking", page.display_note.text())

    def test_shows_what_windows_says(self):
        page = self.make(FakeSystem())
        self.shown(page)
        self.assertEqual(page.volume.slider.value(), 40)
        self.assertEqual(page.volume.value.text(), "40%")
        self.assertEqual(page.volume.button.glyph, "speaker")
        self.assertFalse(page.brightness.isHidden())
        self.assertTrue(page.display_note.isHidden())
        self.assertEqual(page.brightness.slider.value(), 70)
        self.assertEqual([page.outputs.item(i).text() for i in range(page.outputs.count())], ["Speakers", "Headphones"])
        self.assertEqual(page.bluetooth.item(0).text(), "Buds  ·  Connected")
        self.assertEqual(page.bluetooth.item(1).text(), "Mouse  ·  Not connected")
        self.assertEqual(page.mic.text(), "Mute mic")

    def test_mute_shows_at_once_and_a_stale_reading_cant_undo_it(self):
        page = self.make(FakeSystem())
        self.shown(page)
        page.volume.button.click()
        self.assertEqual(page.volume.button.glyph, "speaker_off")
        self.assertEqual(page.volume.value.text(), "Muted")
        self.assertTrue(wait_for(lambda: self.fake.called("set_mute") == [True]))
        page.model.read_levels()  # Windows hasn't caught up: the fake still says unmuted
        self.assertTrue(wait_for(lambda: not page.model.jobs.busy()))
        QTest.qWait(20)
        self.assertEqual(page.volume.button.glyph, "speaker_off")

    def test_moving_the_volume_while_muted_unmutes(self):
        page = self.make(FakeSystem(Levels(40, True, False, 70)))
        self.shown(page)
        self.assertEqual(page.volume.value.text(), "Muted")
        page.volume.slider.setValue(60)
        self.assertTrue(wait_for(lambda: self.fake.called("set_volume") == [60]))
        self.assertTrue(wait_for(lambda: self.fake.called("set_mute") == [False]))
        self.assertEqual(page.volume.value.text(), "60%")

    def test_picking_an_output_makes_it_the_default(self):
        page = self.make(FakeSystem())
        self.shown(page)
        page.outputs.itemClicked.emit(page.outputs.item(1))
        self.assertTrue(wait_for(lambda: self.fake.called("set_default_output") == ["hp"]))
        self.assertTrue(wait_for(lambda: [d.id for d in page.model.devices if d.is_default] == ["hp"]))
        page.outputs.itemClicked.emit(page.outputs.item(1))  # already the one in use
        QTest.qWait(50)
        self.assertEqual(self.fake.called("set_default_output"), ["hp"])

    def test_brightness_slider_sends_it(self):
        page = self.make(FakeSystem())
        self.shown(page)
        page.brightness.slider.setValue(30)
        self.assertTrue(wait_for(lambda: self.fake.called("set_brightness") == [30]))

    def test_no_brightness_and_no_mic(self):
        page = self.make(FakeSystem(Levels(40, False, None, None)))
        self.shown(page)
        self.assertTrue(page.brightness.isHidden())
        self.assertFalse(page.display_note.isHidden())
        self.assertEqual(page.display_note.text(), NO_BRIGHTNESS)
        self.assertFalse(page.mic.isEnabled())
        self.assertEqual(page.mic.text(), "No microphone")

    def test_no_speakers(self):
        page = self.make(FakeSystem(Levels(None, None, False, 50), devices=[]))
        self.shown(page)
        self.assertFalse(page.volume.isEnabled())
        self.assertEqual(page.volume.value.text(), "Off")
        self.assertIn("No speakers", page.outputs.item(0).text())
        page.volume.button.click()
        QTest.qWait(30)
        self.assertEqual(self.fake.called("set_mute"), [])

    def test_bluetooth_missing_or_empty(self):
        page = self.make(FakeSystem(paired=None))
        self.shown(page)
        self.assertIn("isn't available", page.bluetooth.item(0).text())
        self.fake.paired = []
        page.model.read_bluetooth()
        self.assertTrue(wait_for(lambda: "No paired" in page.bluetooth.item(0).text()))

    def test_quick_actions(self):
        page = self.make(FakeSystem())
        self.shown(page)
        page.lock.click()
        page.sound_settings.click()
        page.mic.click()
        self.assertEqual(page.mic.text(), "Mic muted")
        self.assertTrue(page.mic.isChecked())
        page.snip.click()
        self.assertEqual(self.fake.called("screenshot"), [])  # waits for the panel to get out of the way
        self.assertTrue(wait_for(lambda: self.fake.called("screenshot"), SNIP_AFTER_MS / 1000 + 2))
        self.assertTrue(wait_for(lambda: self.fake.called("set_mic_mute") == [True]))
        self.assertEqual(len(self.fake.called("lock_pc")), 1)
        self.assertEqual(len(self.fake.called("open_sound_settings")), 1)

    def test_retheme_keeps_the_lists(self):
        page = self.make(FakeSystem())
        self.shown(page)
        self.ctrl.theme = theme_for("dark")
        page.retheme()
        self.assertEqual(page.outputs.count(), 2)
        self.assertEqual(page.bluetooth.count(), 2)


# ------------------------------------------------------------------ the Home card
class ControlsCardTests(unittest.TestCase):
    def make(self, fake: FakeSystem) -> ControlsCard:
        self.fake, self.ctrl = fake, Ctrl()
        self.card = ControlsCard(self.ctrl, backend=fake)
        return self.card

    def tearDown(self):
        wait_for(lambda: not self.card.model.jobs.busy())
        self.card.deleteLater()

    def shown(self, card: ControlsCard) -> None:
        card.refresh()
        self.assertTrue(wait_for(lambda: card.model.levels_read and card.model.devices_read))

    def test_shows_levels_and_outputs(self):
        card = self.make(FakeSystem())
        self.assertEqual(card.width(), 290)
        self.assertTrue(card.brightness.isHidden())  # until Windows says there is one
        self.shown(card)
        self.assertEqual(card.volume.slider.value(), 40)
        self.assertFalse(card.brightness.isHidden())
        self.assertEqual(card.brightness.value.text(), "70%")
        self.assertEqual([card.output.itemText(i) for i in range(card.output.count())], ["Speakers", "Headphones"])
        self.assertEqual(card.output.currentText(), "Speakers")
        self.assertFalse(card.mic.lit)

    def test_brightness_hidden_when_unsupported(self):
        card = self.make(FakeSystem(Levels(40, False, False, None)))
        self.shown(card)
        self.assertTrue(card.brightness.isHidden())

    def test_choosing_an_output(self):
        card = self.make(FakeSystem())
        self.shown(card)
        card.output.activated.emit(1)
        self.assertTrue(wait_for(lambda: self.fake.called("set_default_output") == ["hp"]))
        self.assertTrue(wait_for(lambda: card.output.currentText() == "Headphones"))

    def test_mic_lights_up_when_muted(self):
        card = self.make(FakeSystem())
        self.shown(card)
        card.mic.click()
        self.assertTrue(card.mic.lit)
        self.assertEqual(card.mic.glyph, "mic_off")
        self.assertTrue(wait_for(lambda: self.fake.called("set_mic_mute") == [True]))

    def test_buttons_reach_the_app(self):
        card = self.make(FakeSystem())
        self.shown(card)
        card.ultra.click()
        self.assertTrue(self.ctrl.settings.ultra_on)
        self.assertTrue(card.ultra.lit)
        self.ctrl.settings.ultra_on = False  # turned off somewhere else
        card.tick()
        self.assertFalse(card.ultra.lit)
        card.sticky.click()
        self.assertEqual(self.ctrl.stickies.made, 1)
        card.sounds.click()
        self.assertEqual(len(self.ctrl.menus), 1)
        card.lock.click()
        self.assertEqual(len(self.fake.called("lock_pc")), 1)
        card.snip.click()
        self.assertTrue(wait_for(lambda: self.fake.called("screenshot"), SNIP_AFTER_MS / 1000 + 2))

    def test_volume_and_mute(self):
        card = self.make(FakeSystem())
        self.shown(card)
        card.volume.button.click()
        self.assertEqual(card.volume.value.text(), "Muted")
        self.assertTrue(wait_for(lambda: self.fake.called("set_mute") == [True]))
        card.volume.slider.setValue(15)
        self.assertTrue(wait_for(lambda: self.fake.called("set_volume") == [15]))
        self.assertTrue(wait_for(lambda: self.fake.called("set_mute") == [True, False]))


if __name__ == "__main__":
    unittest.main()
