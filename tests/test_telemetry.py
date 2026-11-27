import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer

from pomodoro import gamedetect, telemetry
from pomodoro.gamedetect import Foreground, in_library, is_game
from pomodoro.telemetry import (FrameRate, Reading, TelemetryWorker, cpu_percent, gpu_engines, parse_hwinfo,
                                parse_rtss, per_adapter, pick_temperature)


def fg(exe="game.exe", path="", fullscreen=False, gpu=None, pid=4242):
    return Foreground(hwnd=1, pid=pid, exe=exe, path=path, title="", fullscreen=fullscreen, gpu=gpu)


class IsGameTests(unittest.TestCase):
    def test_what_you_said_wins(self):
        self.assertTrue(is_game(fg("chrome.exe"), ["chrome"], []))  # even a browser, if you say so
        self.assertTrue(is_game(fg("eldenring.exe"), ["EldenRing.exe"], []))  # any case, with or without .exe
        steam = r"D:\SteamLibrary\steamapps\common\ELDEN RING\Game\eldenring.exe"
        self.assertFalse(is_game(fg("eldenring.exe", steam, True, 99.0), [], ["eldenring"]))

    def test_built_in_non_games(self):
        for exe in ("chrome.exe", "msedge.exe", "firefox.exe", "vlc.exe", "explorer.exe", "pomo.exe", "code.exe",
                    "winword.exe", "zoom.exe", "discord.exe", "steam.exe", "obs64.exe", "Bubbles.scr"):
            with self.subTest(exe=exe):
                self.assertFalse(is_game(fg(exe.lower(), fullscreen=True, gpu=95.0), [], []))

    def test_never_pomo_itself(self):
        self.assertFalse(is_game(fg("python.exe", pid=os.getpid(), fullscreen=True, gpu=90.0), ["python"], []))

    def test_game_libraries(self):
        for path in (r"C:\Program Files (x86)\Steam\steamapps\common\Hades\x64\Hades.exe",
                     r"E:\Games\SteamLibrary\steamapps\common\Celeste\Celeste.exe",
                     r"C:\Program Files\Epic Games\Fortnite\Binaries\Win64\FortniteClient-Win64-Shipping.exe",
                     r"C:\Riot Games\VALORANT\live\ShooterGame\Binaries\Win64\VALORANT-Win64-Shipping.exe",
                     r"C:\XboxGames\Starfield\Content\Starfield.exe",
                     r"C:\Program Files (x86)\GOG Galaxy\Games\Cyberpunk 2077\bin\x64\Cyberpunk2077.exe",
                     r"C:\Program Files\EA Games\Apex\r5apex.exe",
                     r"C:\Program Files (x86)\Ubisoft\Ubisoft Game Launcher\games\Far Cry 6\bin\FarCry6.exe"):
            with self.subTest(path=path):
                self.assertTrue(is_game(fg(os.path.basename(path).lower(), path), [], []))

    def test_library_helpers_and_launchers_are_not_games(self):
        crash = r"C:\Program Files (x86)\Steam\steamapps\common\Hades\UnityCrashHandler64.exe"
        self.assertFalse(is_game(fg("unitycrashhandler64.exe", crash), [], []))
        portal = r"C:\Program Files (x86)\Epic Games\Launcher\Portal\Binaries\Win64\EpicOnlineServicesHost.exe"
        self.assertFalse(is_game(fg("epiconlineserviceshost.exe", portal), [], []))
        riot = r"C:\Riot Games\Riot Client\RiotClientServices.exe"
        self.assertFalse(is_game(fg("riotclientservices.exe", riot), [], []))

    def test_your_own_library_folders(self):
        path = r"D:\MyGames\Hollow Knight\hollow_knight.exe"
        self.assertTrue(is_game(fg("hollow_knight.exe", path), [], [], [r"D:\MyGames"]))
        self.assertTrue(is_game(fg("hollow_knight.exe", path), [], [], ["d:/mygames/"]))
        self.assertFalse(is_game(fg("tool.exe", r"D:\MyGamesTools\tool.exe"), [], [], [r"D:\MyGames"]))
        self.assertFalse(is_game(fg("hollow_knight.exe", path), [], []))
        self.assertTrue(in_library(path, [r"D:\MyGames\\"]))

    def test_fullscreen_and_busy_gpu(self):
        self.assertTrue(is_game(fg("indie.exe", fullscreen=True, gpu=30.0), [], []))
        self.assertTrue(is_game(fg("indie.exe", fullscreen=True, gpu=25.0), [], []))
        self.assertFalse(is_game(fg("indie.exe", fullscreen=True, gpu=20.0), [], []))
        self.assertFalse(is_game(fg("indie.exe", fullscreen=False, gpu=90.0), [], []))
        self.assertFalse(is_game(fg("indie.exe", fullscreen=True, gpu=None), [], []))
        self.assertTrue(is_game(fg("", fullscreen=True, gpu=60.0), [], []))  # a protected process: no name

    def test_exe_key(self):
        self.assertEqual(gamedetect.exe_key(r"C:\Games\Valorant.EXE"), "valorant")
        self.assertEqual(gamedetect.exe_key("valorant"), "valorant")


# ------------------------------------------------------------------ RTSS shared memory
RTSS_ENTRY_SIZE = 0x400  # larger than the fields Pomo reads, as real entries are


def rtss_buffer(entries, signature=0x52545353, version=0x0002000E, count=None, offset=0x100):
    """A synthetic RTSSSharedMemoryV2: header, then app entries (pid, name, time0, time1, frames, frame_us)."""
    buf = bytearray(offset + RTSS_ENTRY_SIZE * len(entries))
    struct.pack_into("<5I", buf, 0, signature, version, RTSS_ENTRY_SIZE, offset,
                     len(entries) if count is None else count)
    for i, (pid, name, t0, t1, frames, frame_us) in enumerate(entries):
        struct.pack_into("<I260sIIIII", buf, offset + i * RTSS_ENTRY_SIZE, pid, name.encode(), 0, t0, t1, frames,
                         frame_us)
    return bytes(buf)


class RtssTests(unittest.TestCase):
    def setUp(self):
        self.buf = rtss_buffer([(1111, "other.exe", 5000, 6000, 30, 33333),
                                (4242, "game.exe", 10_000, 11_000, 144, 6944)])

    def test_reads_the_targets_rate(self):
        self.assertAlmostEqual(parse_rtss(self.buf, 4242), 144.0)
        self.assertAlmostEqual(parse_rtss(self.buf, 1111), 30.0)
        self.assertIsNone(parse_rtss(self.buf, 9999))
        self.assertIsNone(parse_rtss(self.buf, 0))

    def test_a_stale_entry_is_unknown(self):
        self.assertAlmostEqual(parse_rtss(self.buf, 4242, now_ms=11_800), 144.0)
        self.assertIsNone(parse_rtss(self.buf, 4242, now_ms=11_000 + 6_000))
        wrapped = rtss_buffer([(7, "g.exe", 2 ** 32 - 1500, 2 ** 32 - 500, 60, 0)])
        self.assertAlmostEqual(parse_rtss(wrapped, 7, now_ms=200), 60.0)  # the tick count wrapped since

    def test_frame_time_when_there_is_no_period(self):
        buf = rtss_buffer([(4242, "game.exe", 0, 0, 0, 16_667)])
        self.assertAlmostEqual(parse_rtss(buf, 4242), 60.0, places=1)

    def test_not_rtss_or_old_layout(self):
        self.assertIsNone(parse_rtss(rtss_buffer([(4242, "g", 1, 2, 3, 4)], signature=0xDEAD), 4242))
        self.assertIsNone(parse_rtss(rtss_buffer([(4242, "g", 1, 2, 3, 4)], version=0x00010003), 4242))

    def test_a_count_larger_than_the_memory_stays_inside(self):
        buf = rtss_buffer([(4242, "game.exe", 1000, 2000, 90, 0)], count=256)
        self.assertAlmostEqual(parse_rtss(memoryview(buf), 4242), 90.0)
        self.assertIsNone(parse_rtss(memoryview(buf), 5))


# ------------------------------------------------------------------ HWiNFO shared memory
HWINFO_READING_SIZE = 460  # newer HWiNFO versions add fields after the ones Pomo reads


def hwinfo_buffer(readings, signature=b"HWiS"):
    """A synthetic HWiNFO_SENS_SM2: header, one sensor, then readings (type, label, user label, unit, value)."""
    sensors_at, sensor_size = 44, 264
    readings_at = sensors_at + sensor_size
    buf = bytearray(readings_at + HWINFO_READING_SIZE * len(readings))
    struct.pack_into("<4sIIqIIIIII", buf, 0, signature, 2, 1, 1_700_000_000, sensors_at, sensor_size, 1,
                     readings_at, HWINFO_READING_SIZE, len(readings))
    for i, (kind, label, user, unit, value) in enumerate(readings):
        struct.pack_into("<III128s128s16sdddd", buf, readings_at + i * HWINFO_READING_SIZE, kind, 0, i,
                         label.encode("cp1252"), user.encode("cp1252"), unit.encode("cp1252"), value, value, value,
                         value)
    return bytes(buf)


class HwinfoTests(unittest.TestCase):
    def test_cpu_package_first(self):
        buf = hwinfo_buffer([(2, "CPU Package", "", "V", 1.2),          # a voltage, not a temperature
                             (1, "Core Max", "", "°C", 71.0),
                             (1, "CPU Package", "My CPU", "°C", 66.5),  # renamed: the original label counts
                             (1, "GPU Temperature", "", "°C", 58.0)])
        temps = parse_hwinfo(buf)
        self.assertEqual(temps["cpu package"], 66.5)
        self.assertEqual(pick_temperature(temps, telemetry.CPU_TEMP_LABELS), 66.5)
        self.assertEqual(pick_temperature(temps, telemetry.GPU_TEMP_LABELS), 58.0)

    def test_amd_and_core_max(self):
        amd = parse_hwinfo(hwinfo_buffer([(1, "CPU (Tctl/Tdie)", "", "°C", 75.25)]))
        self.assertEqual(pick_temperature(amd, telemetry.CPU_TEMP_LABELS), 75.25)
        intel = parse_hwinfo(hwinfo_buffer([(1, "Core Max", "", "°C", 80.0)]))
        self.assertEqual(pick_temperature(intel, telemetry.CPU_TEMP_LABELS), 80.0)

    def test_fahrenheit_and_duplicates(self):
        temps = parse_hwinfo(hwinfo_buffer([(1, "CPU Package", "", "°F", 140.0), (1, "CPU Package", "", "°C", 55.0)]))
        self.assertAlmostEqual(temps["cpu package"], 60.0)  # 140 F, the higher of the two

    def test_not_running(self):
        self.assertEqual(parse_hwinfo(hwinfo_buffer([(1, "CPU Package", "", "°C", 60.0)], signature=b"DEAD")), {})
        self.assertIsNone(pick_temperature({}, telemetry.CPU_TEMP_LABELS))


# ------------------------------------------------------------------ PresentMon CSV
V1_HEADER = ("Application,ProcessID,SwapChainAddress,Runtime,SyncInterval,PresentFlags,AllowsTearing,PresentMode,"
             "Dropped,TimeInSeconds,msBetweenPresents,msBetweenDisplayChange,msInPresentAPI,msUntilRenderComplete,"
             "msUntilDisplayed")
V2_HEADER = ("Application,ProcessID,SwapChainAddress,PresentRuntime,SyncInterval,PresentFlags,AllowsTearing,"
             "PresentMode,CPUStartTime,FrameTime,CPUBusy,CPUWait,GPULatency,GPUTime,GPUBusy,GPUWait,DisplayLatency,"
             "DisplayedTime,ClickToPhotonLatency")
V26_HEADER = ("Application,ProcessID,SwapChainAddress,PresentRuntime,SyncInterval,PresentFlags,AllowsTearing,"
              "PresentMode,FrameType,CPUStartTime,MsBetweenSimulationStart,MsBetweenPresents,MsBetweenDisplayChange,"
              "MsInPresentAPI,MsRenderPresentLatency,MsUntilDisplayed,MsPCLatency,MsBetweenAppStart,MsCPUBusy,"
              "MsCPUWait,MsGPULatency,MsGPUTime,MsGPUBusy,MsGPUWait,MsAnimationError,AnimationTime,"
              "MsAllInputToPhotonLatency,MsClickToPhotonLatency")


def row(header: str, values: dict[str, str], app="game.exe", pid=4242) -> str:
    cells = []
    for name in header.split(","):
        if name == "Application":
            cells.append(app)
        elif name == "ProcessID":
            cells.append(str(pid))
        else:
            cells.append(values.get(name, "0.5"))
    return ",".join(cells)


class PresentMonCsvTests(unittest.TestCase):
    def feed(self, rate: FrameRate, header: str, column: str, ms: float, frames: int, now=100.0, **kw):
        for _ in range(frames):
            rate.feed(row(header, {column: f"{ms:.4f}"}, **kw), now)

    def test_v1_columns(self):
        rate = FrameRate(4242)
        rate.feed(V1_HEADER, 100.0)
        self.feed(rate, V1_HEADER, "msBetweenPresents", 10.0, 150)
        self.assertAlmostEqual(rate.fps(100.5), 100.0)

    def test_v2_columns(self):
        rate = FrameRate(4242)
        rate.feed(V2_HEADER + "\n", 100.0)
        self.feed(rate, V2_HEADER, "FrameTime", 16.6667, 90)
        self.assertAlmostEqual(rate.fps(100.5), 60.0, places=1)

    def test_v26_defaults_prefer_present_to_present(self):
        rate = FrameRate(4242)
        rate.feed(V26_HEADER, 100.0)
        for _ in range(200):
            rate.feed(row(V26_HEADER, {"MsBetweenPresents": "8.0", "MsBetweenAppStart": "20.0"}), 100.0)
        self.assertAlmostEqual(rate.fps(100.0), 125.0)

    def test_only_the_last_second_counts(self):
        rate = FrameRate(4242)
        rate.feed(V26_HEADER, 100.0)
        self.feed(rate, V26_HEADER, "MsBetweenPresents", 50.0, 100)  # 20 fps a while ago
        self.feed(rate, V26_HEADER, "MsBetweenPresents", 5.0, 400)   # 200 fps now
        self.assertAlmostEqual(rate.fps(100.0), 200.0)

    def test_odd_rows(self):
        rate = FrameRate(4242)
        self.assertIsNone(rate.fps(100.0))
        rate.feed(row(V26_HEADER, {"MsBetweenPresents": "10"}), 100.0)  # before any header: ignored
        self.assertIsNone(rate.fps(100.0))
        rate.feed(V26_HEADER, 100.0)
        rate.feed(row(V26_HEADER, {"MsBetweenPresents": "NA"}), 100.0)
        rate.feed(row(V26_HEADER, {"MsBetweenPresents": "10"}, pid=1), 100.0)  # someone else's frame
        rate.feed("garbage", 100.0)
        self.assertIsNone(rate.fps(100.0))
        self.feed(rate, V26_HEADER, "MsBetweenPresents", 20.0, 60, app="Cool, Game.exe")  # a comma in the name
        self.assertAlmostEqual(rate.fps(100.0), 50.0)
        self.assertIsNone(rate.fps(100.0 + telemetry.PM_STALE_S + 1))  # stopped presenting


# ------------------------------------------------------------------ counters and math
class CpuAndGpuMathTests(unittest.TestCase):
    def test_cpu_percent(self):
        self.assertAlmostEqual(cpu_percent((100, 1000, 500), (200, 1400, 700)), 500 / 6)  # kernel includes idle
        self.assertEqual(cpu_percent((0, 0, 0), (100, 100, 0)), 0.0)
        self.assertEqual(cpu_percent((0, 0, 0), (0, 50, 50)), 100.0)
        self.assertIsNone(cpu_percent(None, (1, 2, 3)))
        self.assertIsNone(cpu_percent((1, 2, 3), (1, 2, 3)))

    def test_gpu_engines(self):
        values = {
            "pid_100_luid_0x00000000_0x0000D1B5_phys_0_eng_0_engtype_3D": 40.0,
            "pid_200_luid_0x00000000_0x0000D1B5_phys_0_eng_0_engtype_3D": 25.0,
            "pid_100_luid_0x00000000_0x00010421_phys_0_eng_0_engtype_3D": 10.0,
            "pid_300_luid_0x00000000_0x00010421_phys_0_eng_0_engtype_3D": 5.0,
            "nonsense": 99.0,
        }
        total, luid, by_pid = gpu_engines(values)
        self.assertEqual((total, luid), (65.0, (0, 0xD1B5)))
        self.assertEqual(by_pid, {100: 50.0, 200: 25.0, 300: 5.0})
        self.assertEqual(gpu_engines({}), (None, None, {}))
        self.assertEqual(gpu_engines({"pid_1_luid_0x0_0x1_phys_0_eng_0_engtype_3D": 180.0})[0], 100.0)

    def test_per_adapter(self):
        values = {"luid_0x00000000_0x0000D1B5_phys_0": 3e9, "luid_0x00000000_0x00010421_phys_0": 1e8}
        self.assertEqual(per_adapter(values, (0, 0x10421)), 1e8)
        self.assertEqual(per_adapter(values, None), 3e9)
        self.assertIsNone(per_adapter({}, None))


# ------------------------------------------------------------------ nothing available
class NothingAvailableTests(unittest.TestCase):
    def test_an_empty_reading(self):
        r = Reading()
        self.assertEqual(r.fps_source, "")
        self.assertTrue(all(getattr(r, f) is None for f in ("cpu", "gpu", "ram", "ram_used_gb", "ram_total_gb",
                                                           "vram_used_gb", "cpu_temp", "gpu_temp", "fps")))

    def test_every_source_failing_gives_an_empty_reading(self):
        def broken(*_args, **_kw):
            raise OSError("not here")

        with mock.patch.object(telemetry, "system_times", broken), mock.patch.object(telemetry, "memory", broken), \
                mock.patch.object(telemetry, "_Pdh", broken), mock.patch.object(telemetry, "_D3dkmt", broken), \
                mock.patch.object(telemetry, "_Nvml", broken), \
                mock.patch.object(telemetry, "hwinfo_temperatures", broken), \
                mock.patch.object(telemetry, "rtss_fps", broken), \
                mock.patch.object(telemetry, "presentmon_path", lambda: None), \
                mock.patch.object(telemetry, "fps_permission", lambda: False), \
                mock.patch.object(gamedetect, "foreground", lambda *_a: None):
            sampler = telemetry.Sampler()
            sampler.prime()
            self.assertEqual(sampler.sample(4242), Reading())
            self.assertEqual(sampler.sample(None), Reading())
            self.assertIsNone(sampler.foreground(False, lambda _s: False))
            sampler.close()

    def test_no_presentmon_without_permission(self):
        with mock.patch.object(telemetry, "rtss_fps", lambda _pid: None), \
                mock.patch.object(telemetry, "fps_permission", lambda: False), \
                mock.patch.object(telemetry, "_PresentMonRun", side_effect=AssertionError("started")):
            sampler = telemetry.Sampler()
            self.assertEqual(sampler._fps(4242), (None, ""))

    def test_permission_script_quotes_the_name(self):
        script = telemetry.fps_permission_script("PC\\O'Brien")
        self.assertIn("$member = 'PC\\O''Brien'", script)
        self.assertIn("-SID 'S-1-5-32-559'", script)

    def test_presentmon_path_when_frozen(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "pomodoro" / "bin" / "PresentMon.exe"
            with mock.patch.object(sys, "frozen", True, create=True), \
                    mock.patch.object(sys, "_MEIPASS", tmp, create=True):
                self.assertIsNone(telemetry.presentmon_path())
                exe.parent.mkdir(parents=True)
                exe.write_bytes(b"MZ")
                self.assertEqual(telemetry.presentmon_path(), str(exe))


class FakeSampler:
    def __init__(self):
        self.closed = False
        self._pm = None

    def prime(self):
        pass

    def idle(self):
        pass

    def sample(self, target):
        return Reading(cpu=12.5, fps=60.0 if target else None, fps_source="rtss" if target else "")

    def foreground(self, sampling, sleep):
        return None

    def close(self):
        self.closed = True


class WorkerTests(unittest.TestCase):
    def test_readings_reach_the_ui_thread_and_stop_ends_it(self):
        fake = FakeSampler()
        readings, fronts = [], []
        with mock.patch.object(telemetry, "Sampler", lambda: fake):
            worker = TelemetryWorker()
            worker.reading.connect(readings.append)
            worker.foreground.connect(fronts.append)
            worker.set_target(4242)
            worker.start()
            worker.set_sampling(True)
            loop = QEventLoop()
            worker.reading.connect(lambda _r: loop.quit())
            QTimer.singleShot(5000, loop.quit)
            loop.exec()
            worker.stop()
            QCoreApplication.processEvents()  # deliver what it queued before it stopped
        self.assertFalse(worker.isRunning())
        self.assertTrue(fake.closed)
        self.assertEqual(readings[0], Reading(cpu=12.5, fps=60.0, fps_source="rtss"))
        self.assertEqual(fronts[:1], [None])

    def test_stop_before_start(self):
        worker = TelemetryWorker()
        worker.stop()
        self.assertFalse(worker.isRunning())


if __name__ == "__main__":
    unittest.main()
