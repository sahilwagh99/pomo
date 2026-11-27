"""Gaming mode's readout: frame rate, CPU and GPU use and temperatures, memory.

Everything is read the way Task Manager and overlays read it, without admin rights:
  CPU use       GetSystemTimes, the change since the last reading
  memory        GlobalMemoryStatusEx
  GPU use       the "GPU Engine" performance counters (3D engines), busiest adapter; per process too,
                which is how gamedetect tells a game from a fullscreen video
  video memory  the "GPU Adapter Memory" counters (dedicated memory in use)
  GPU temp      D3DKMT adapter performance data (WDDM 2.4+ drivers fill it in; integrated GPUs often
                report 0, meaning unknown), else NVIDIA's NVML, else HWiNFO
  CPU temp      HWiNFO's shared memory when it runs with Shared Memory Support on, else the ACPI thermal
                zone counters (many PCs have none: then it's unknown)
  frame rate    RivaTuner Statistics Server's shared memory when it's running, else PresentMon (bundled),
                which needs either admin rights or membership of Performance Log Users

Every source fails soft: a value nobody can read is None, and the readout leaves it out. The worker
thread owns every query, handle and child process; the UI only flips two switches on it.
"""
from __future__ import annotations

import base64
import collections
import dataclasses
import os
import re
import struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QThread, Signal

from . import gamedetect

SAMPLE_S = 1.0          # a reading this often while gaming mode is on
FOREGROUND_S = 2.0      # what's in front, this often, always
PRIME_S = 0.5           # rate counters need two collections: this far apart when sampling starts
ENGINES_FRESH_S = 3.0   # an older GPU collection gets a fresh pair before it decides anything
RETRY_S = 60.0          # a source that couldn't open is tried again this much later
PM_DENIED_RETRY_S = 300.0
GB = 1024 ** 3
CREATE_NO_WINDOW = 0x08000000
PERFORMANCE_LOG_USERS = "S-1-5-32-559"
ADMINISTRATORS = "S-1-5-32-544"

ENGINE_COUNTER = r"\GPU Engine(*engtype_3D)\Utilization Percentage"
VRAM_COUNTER = r"\GPU Adapter Memory(*)\Dedicated Usage"
SHARED_COUNTER = r"\GPU Adapter Memory(*)\Shared Usage"  # an integrated GPU's only video memory
THERMAL_COUNTER = r"\Thermal Zone Information(*)\Temperature"


@dataclass(frozen=True)
class Reading:
    cpu: float | None = None           # total CPU use, %
    gpu: float | None = None           # 3D GPU engine use, %, the busiest adapter
    ram: float | None = None           # memory in use, %
    ram_used_gb: float | None = None
    ram_total_gb: float | None = None
    vram_used_gb: float | None = None  # dedicated GPU memory in use
    cpu_temp: float | None = None      # degrees C
    gpu_temp: float | None = None
    fps: float | None = None
    fps_source: str = ""               # "rtss", "presentmon" or ""


# ------------------------------------------------------------------ CPU and memory
def system_times() -> tuple[int, int, int] | None:
    """(idle, kernel, user) CPU time since boot in 100 ns units, all cores together. Kernel includes idle."""
    if sys.platform != "win32":
        return None
    import ctypes

    idle, kernel, user = ctypes.c_uint64(), ctypes.c_uint64(), ctypes.c_uint64()
    if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
        return None
    return idle.value, kernel.value, user.value


def cpu_percent(before: tuple[int, int, int] | None, after: tuple[int, int, int] | None) -> float | None:
    """CPU use between two system_times() readings, in %."""
    if before is None or after is None:
        return None
    idle = after[0] - before[0]
    total = (after[1] - before[1]) + (after[2] - before[2])
    if total <= 0 or idle < 0:
        return None
    return max(0.0, min(100.0, 100.0 * (total - idle) / total))


def memory() -> tuple[float, float, float] | None:
    """(% in use, GB in use, GB installed), as Task Manager counts them."""
    if sys.platform != "win32":
        return None
    import ctypes

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_uint32), ("dwMemoryLoad", ctypes.c_uint32)] + [
            (name, ctypes.c_uint64) for name in ("ullTotalPhys", "ullAvailPhys", "ullTotalPageFile",
                                                 "ullAvailPageFile", "ullTotalVirtual", "ullAvailVirtual",
                                                 "ullAvailExtendedVirtual")]

    status = MEMORYSTATUSEX()
    status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)) or not status.ullTotalPhys:
        return None
    used = status.ullTotalPhys - status.ullAvailPhys
    return 100.0 * used / status.ullTotalPhys, used / GB, status.ullTotalPhys / GB


# ------------------------------------------------------------------ performance counters (PDH)
_LUID = re.compile(r"luid_0x([0-9a-f]+)_0x([0-9a-f]+)", re.IGNORECASE)
_PID = re.compile(r"pid_(\d+)_", re.IGNORECASE)


def luid_of(instance: str) -> tuple[int, int] | None:
    """An adapter's LUID as (HighPart, LowPart) from a GPU counter instance name ('..._luid_0x0_0xD1B5_...')."""
    match = _LUID.search(instance)
    return (int(match.group(1), 16), int(match.group(2), 16)) if match else None


def gpu_engines(values: dict[str, float]) -> tuple[float | None, tuple[int, int] | None, dict[int, float]]:
    """(busiest adapter's 3D use %, its LUID, each process's 3D use %) from GPU Engine counter values.

    An adapter's use is the sum over the processes on it, as Task Manager shows it. Nothing on any
    3D engine (no instances at all) gives None, not 0: the counters say nothing then.
    """
    by_adapter: dict[tuple[int, int], float] = {}
    by_pid: dict[int, float] = {}
    for name, value in values.items():
        luid, pid = luid_of(name), _PID.search(name)
        if luid is None or pid is None or value != value:  # value != value: NaN
            continue
        by_adapter[luid] = by_adapter.get(luid, 0.0) + value
        by_pid[int(pid.group(1))] = by_pid.get(int(pid.group(1)), 0.0) + value
    by_pid = {pid: min(100.0, max(0.0, v)) for pid, v in by_pid.items()}
    if not by_adapter:
        return None, None, by_pid
    luid = max(by_adapter, key=by_adapter.__getitem__)
    return min(100.0, max(0.0, by_adapter[luid])), luid, by_pid


def per_adapter(values: dict[str, float], prefer: tuple[int, int] | None) -> float | None:
    """One adapter's value from per-adapter counter values: the preferred adapter's, else the largest."""
    by_luid: dict[tuple[int, int], float] = {}
    for name, value in values.items():
        luid = luid_of(name)
        if luid is not None:
            by_luid[luid] = by_luid.get(luid, 0.0) + value
    if not by_luid:
        return None
    return by_luid[prefer] if prefer in by_luid else max(by_luid.values())


class _Pdh:
    """One long-lived PDH query over a few English counter paths (works on any Windows language).

    Rate counters like GPU use compare two collections, so the query stays open between readings.
    """

    PDH_MORE_DATA = 0x800007D2
    FMT_DOUBLE_NOCAP = 0x00000200 | 0x00008000  # PDH_FMT_DOUBLE | PDH_FMT_NOCAP100

    def __init__(self, paths: list[str]):
        import ctypes

        self._c = ctypes
        pdh = ctypes.WinDLL("pdh")
        vp = ctypes.c_void_p
        pdh.PdhOpenQueryW.argtypes = [ctypes.c_wchar_p, ctypes.c_size_t, ctypes.POINTER(vp)]
        pdh.PdhAddEnglishCounterW.argtypes = [vp, ctypes.c_wchar_p, ctypes.c_size_t, ctypes.POINTER(vp)]
        pdh.PdhCollectQueryData.argtypes = [vp]
        pdh.PdhGetFormattedCounterArrayW.argtypes = [vp, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32),
                                                     ctypes.POINTER(ctypes.c_uint32), vp]
        pdh.PdhCloseQuery.argtypes = [vp]
        for fn in (pdh.PdhOpenQueryW, pdh.PdhAddEnglishCounterW, pdh.PdhCollectQueryData,
                   pdh.PdhGetFormattedCounterArrayW, pdh.PdhCloseQuery):
            fn.restype = ctypes.c_uint32

        class Value(ctypes.Structure):  # PDH_FMT_COUNTERVALUE, reading its union as a double
            _fields_ = [("CStatus", ctypes.c_uint32), ("doubleValue", ctypes.c_double)]

        class Item(ctypes.Structure):  # PDH_FMT_COUNTERVALUE_ITEM_W
            _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", Value)]

        self._pdh, self._item = pdh, Item
        self._query = vp()
        if pdh.PdhOpenQueryW(None, 0, ctypes.byref(self._query)) != 0:
            raise OSError("PdhOpenQuery failed")
        self._counters: dict[str, ctypes.c_void_p] = {}
        for path in paths:
            counter = vp()
            if pdh.PdhAddEnglishCounterW(self._query, path, 0, ctypes.byref(counter)) == 0:
                self._counters[path] = counter
        if not self._counters:
            self.close()
            raise OSError("none of the counters exist here")

    def has(self, path: str) -> bool:
        return path in self._counters

    def collect(self) -> bool:
        return self._query.value is not None and self._pdh.PdhCollectQueryData(self._query) == 0

    def values(self, path: str) -> dict[str, float] | None:
        """Each instance's value from the last collection; None if the counter isn't there or can't be read."""
        counter = self._counters.get(path)
        if counter is None:
            return None
        c = self._c
        size, count = c.c_uint32(0), c.c_uint32(0)
        status = self._pdh.PdhGetFormattedCounterArrayW(counter, self.FMT_DOUBLE_NOCAP, c.byref(size),
                                                        c.byref(count), None)
        for _attempt in range(3):
            if status != self.PDH_MORE_DATA or not size.value:
                return {} if status == 0 else None
            buf = (c.c_byte * size.value)()
            status = self._pdh.PdhGetFormattedCounterArrayW(counter, self.FMT_DOUBLE_NOCAP, c.byref(size),
                                                            c.byref(count), buf)
            if status == 0:
                items = c.cast(buf, c.POINTER(self._item))
                out = {}
                for i in range(count.value):
                    item = items[i]
                    if item.FmtValue.CStatus in (0, 1) and item.szName:  # PDH_CSTATUS_VALID_DATA, NEW_DATA
                        out[item.szName] = item.FmtValue.doubleValue
                return out
        return None

    def close(self) -> None:
        if self._query.value is not None:
            self._pdh.PdhCloseQuery(self._query)
            self._query = self._c.c_void_p()


# ------------------------------------------------------------------ GPU temperature
class _D3dkmt:
    """GPU temperatures from the display driver's adapter performance data (D3DKMT, no admin needed)."""

    PERFDATA = 62  # KMTQAITYPE_ADAPTERPERFDATA

    def __init__(self):
        import ctypes

        c = ctypes

        class LUID(c.Structure):
            _fields_ = [("LowPart", c.c_uint32), ("HighPart", c.c_int32)]

        class AdapterInfo(c.Structure):  # D3DKMT_ADAPTERINFO
            _fields_ = [("hAdapter", c.c_uint32), ("AdapterLuid", LUID), ("NumOfSources", c.c_uint32),
                        ("bPrecisePresentRegionsPreferred", c.c_int32)]

        class EnumAdapters2(c.Structure):  # D3DKMT_ENUMADAPTERS2
            _fields_ = [("NumAdapters", c.c_uint32), ("pAdapters", c.POINTER(AdapterInfo))]

        class QueryAdapterInfo(c.Structure):  # D3DKMT_QUERYADAPTERINFO
            _fields_ = [("hAdapter", c.c_uint32), ("Type", c.c_int), ("pPrivateDriverData", c.c_void_p),
                        ("PrivateDriverDataSize", c.c_uint32)]

        class PerfData(c.Structure):  # D3DKMT_ADAPTER_PERFDATA
            _fields_ = [("PhysicalAdapterIndex", c.c_uint32), ("MemoryFrequency", c.c_uint64),
                        ("MaxMemoryFrequency", c.c_uint64), ("MaxMemoryFrequencyOC", c.c_uint64),
                        ("MemoryBandwidth", c.c_uint64), ("PCIEBandwidth", c.c_uint64), ("FanRPM", c.c_uint32),
                        ("Power", c.c_uint32), ("Temperature", c.c_uint32), ("PowerStateOverride", c.c_ubyte)]

        class CloseAdapter(c.Structure):  # D3DKMT_CLOSEADAPTER
            _fields_ = [("hAdapter", c.c_uint32)]

        gdi32 = c.WinDLL("gdi32")
        gdi32.D3DKMTEnumAdapters2.argtypes = [c.POINTER(EnumAdapters2)]
        gdi32.D3DKMTQueryAdapterInfo.argtypes = [c.POINTER(QueryAdapterInfo)]
        gdi32.D3DKMTCloseAdapter.argtypes = [c.POINTER(CloseAdapter)]
        for fn in (gdi32.D3DKMTEnumAdapters2, gdi32.D3DKMTQueryAdapterInfo, gdi32.D3DKMTCloseAdapter):
            fn.restype = c.c_long  # NTSTATUS
        self._c, self._gdi32 = c, gdi32
        self._QueryAdapterInfo, self._PerfData, self._CloseAdapter = QueryAdapterInfo, PerfData, CloseAdapter
        query = EnumAdapters2(0, None)
        if gdi32.D3DKMTEnumAdapters2(c.byref(query)) != 0 or not query.NumAdapters:
            raise OSError("no adapters")
        array = (AdapterInfo * query.NumAdapters)()
        query.pAdapters = c.cast(array, c.POINTER(AdapterInfo))
        if gdi32.D3DKMTEnumAdapters2(c.byref(query)) != 0:
            raise OSError("D3DKMTEnumAdapters2 failed")
        self._adapters = [(array[i].hAdapter, (array[i].AdapterLuid.HighPart & 0xFFFFFFFF,
                                               array[i].AdapterLuid.LowPart)) for i in range(query.NumAdapters)]

    def temperatures(self) -> dict[tuple[int, int], float]:
        """Each adapter's temperature in degrees C by LUID; adapters that report 0 (unknown) are left out."""
        c = self._c
        out = {}
        for handle, luid in self._adapters:
            perf = self._PerfData()
            info = self._QueryAdapterInfo(handle, self.PERFDATA, c.cast(c.byref(perf), c.c_void_p),
                                          c.sizeof(perf))
            if self._gdi32.D3DKMTQueryAdapterInfo(c.byref(info)) == 0 and 0 < perf.Temperature < 1500:
                out[luid] = perf.Temperature / 10.0  # deci-Celsius
        return out

    def close(self) -> None:
        for handle, _luid in self._adapters:
            self._gdi32.D3DKMTCloseAdapter(self._c.byref(self._CloseAdapter(handle)))
        self._adapters = []


class _Nvml:
    """NVIDIA GPU temperatures through nvml.dll, which comes with NVIDIA's driver."""

    def __init__(self):
        import ctypes

        candidates = [os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "nvml.dll"),
                      os.path.join(os.environ.get("ProgramW6432", r"C:\Program Files"),
                                   "NVIDIA Corporation", "NVSMI", "nvml.dll")]
        path = next((p for p in candidates if os.path.isfile(p)), None)
        if path is None:
            raise OSError("no NVIDIA driver")
        lib = ctypes.CDLL(path)
        lib.nvmlDeviceGetHandleByIndex_v2.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)]
        lib.nvmlDeviceGetTemperature.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
        if lib.nvmlInit_v2() != 0:
            raise OSError("nvmlInit failed")
        self._c, self._lib = ctypes, lib
        count = ctypes.c_uint(0)
        self._devices = []
        if lib.nvmlDeviceGetCount_v2(ctypes.byref(count)) == 0:
            for i in range(count.value):
                device = ctypes.c_void_p()
                if lib.nvmlDeviceGetHandleByIndex_v2(i, ctypes.byref(device)) == 0:
                    self._devices.append(device)
        if not self._devices:
            lib.nvmlShutdown()
            raise OSError("no NVIDIA GPU")

    def temperature(self) -> float | None:
        temps = []
        for device in self._devices:
            value = self._c.c_uint(0)
            ok = self._lib.nvmlDeviceGetTemperature(device, 0, self._c.byref(value)) == 0  # NVML_TEMPERATURE_GPU
            if ok and value.value:
                temps.append(float(value.value))
        return max(temps) if temps else None

    def close(self) -> None:
        self._lib.nvmlShutdown()


# ------------------------------------------------------------------ other apps' shared memory
def read_shared(name: str, parse):
    """parse(buffer) on a read-only view of another app's named shared memory; None if it isn't there.

    The buffer is a memoryview bounded to the mapped region (so a bad offset raises rather than reads
    past it), valid only during the call: parse must return plain values.
    """
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    k = _kernel32()
    handle = k.OpenFileMappingW(0x0004, False, name)  # FILE_MAP_READ
    if not handle:
        return None
    try:
        view = k.MapViewOfFile(handle, 0x0004, 0, 0, 0)
        if not view:
            return None
        try:
            class MBI(ctypes.Structure):  # MEMORY_BASIC_INFORMATION
                _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                            ("AllocationProtect", wintypes.DWORD), ("PartitionId", wintypes.WORD),
                            ("RegionSize", ctypes.c_size_t), ("State", wintypes.DWORD),
                            ("Protect", wintypes.DWORD), ("Type", wintypes.DWORD)]

            mbi = MBI()
            if not k.VirtualQuery(view, ctypes.byref(mbi), ctypes.sizeof(mbi)) or not mbi.RegionSize:
                return None
            region = (ctypes.c_ubyte * mbi.RegionSize).from_address(view)
            with memoryview(region) as buf:
                try:
                    return parse(buf)
                except (struct.error, ValueError, IndexError):
                    return None
        finally:
            k.UnmapViewOfFile(view)
    finally:
        k.CloseHandle(handle)


_k32 = None


def _kernel32():
    global _k32
    if _k32 is None:
        import ctypes
        from ctypes import wintypes

        k = ctypes.WinDLL("kernel32")
        k.OpenFileMappingW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        k.OpenFileMappingW.restype = wintypes.HANDLE
        k.MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
        k.MapViewOfFile.restype = ctypes.c_void_p
        k.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
        k.VirtualQuery.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
        k.VirtualQuery.restype = ctypes.c_size_t
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.GetTickCount.restype = wintypes.DWORD
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k.CreateJobObjectW.restype = wintypes.HANDLE
        k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        _k32 = k
    return _k32


# RivaTuner Statistics Server (RTSSSharedMemory.h, v2 layout)
RTSS_MAP = "RTSSSharedMemoryV2"
RTSS_SIGNATURE = 0x52545353  # 'RTSS' as a C multi-character constant
RTSS_STALE_MS = 5000         # RTSS refreshes each app's frame count once a second
_RTSS_HEADER = struct.Struct("<5I")         # signature, version, app entry size, app array offset, app array size
_RTSS_APP = struct.Struct("<I260sIIIII")    # pid, exe name, flags, time0, time1, frames, frame time (us)


def parse_rtss(buf, pid: int, now_ms: int | None = None) -> float | None:
    """A process's frame rate from RTSS's shared memory, or None (RTSS isn't drawing on it).

    now_ms (GetTickCount) lets a game that stopped presenting read as unknown, not as its last rate.
    """
    signature, version, entry_size, offset, count = _RTSS_HEADER.unpack_from(buf, 0)
    if signature != RTSS_SIGNATURE or version < 0x00020000 or entry_size < _RTSS_APP.size:
        return None
    count = min(count, max(0, (len(buf) - offset) // entry_size))  # whatever the count means, stay inside
    for i in range(count):
        at = offset + i * entry_size
        if struct.unpack_from("<I", buf, at)[0] != pid or not pid:
            continue
        _pid, _name, _flags, time0, time1, frames, frame_us = _RTSS_APP.unpack_from(buf, at)
        if now_ms is not None and time1:
            age = ((now_ms - time1 + 2 ** 31) % 2 ** 32) - 2 ** 31  # both wrap at 2^32 ms
            if age > RTSS_STALE_MS:
                return None
        if time0 and time1 > time0:
            return 1000.0 * frames / (time1 - time0)
        return 1e6 / frame_us if frame_us else None
    return None


def rtss_fps(pid: int) -> float | None:
    return read_shared(RTSS_MAP, lambda buf: parse_rtss(buf, pid, _kernel32().GetTickCount()))


# HWiNFO (Shared Memory Support, "Global\HWiNFO_SENS_SM2"), packed structs
HWINFO_MAP = "Global\\HWiNFO_SENS_SM2"
HWINFO_SIGNATURE = b"HWiS"
HWINFO_TEMPERATURE = 1
_HWINFO_HEADER = struct.Struct("<4sIIqIIIIII")  # signature, version, revision, poll time, sensors (offset, size,
                                                # count), readings (offset, size, count)
_HWINFO_READING = struct.Struct("<III128s128s16sdddd")  # type, sensor index, id, label, user label, unit,
                                                       # value, min, max, avg
CPU_TEMP_LABELS = ("cpu package", "cpu (tctl/tdie)", "core max", "cpu tctl", "cpu die (average)")
GPU_TEMP_LABELS = ("gpu temperature",)


def parse_hwinfo(buf) -> dict[str, float]:
    """Every temperature reading in HWiNFO's shared memory: its original label (lower-case) -> degrees C.

    The original label, not the one you may have renamed it to, so "CPU Package" is found either way.
    A label that several sensors share keeps its highest value.
    """
    header = _HWINFO_HEADER.unpack_from(buf, 0)
    if header[0] != HWINFO_SIGNATURE:
        return {}
    offset, size, count = header[7:10]
    if size < _HWINFO_READING.size:
        return {}
    count = min(count, max(0, (len(buf) - offset) // size))
    out: dict[str, float] = {}
    for i in range(count):
        kind, _sensor, _id, label, user, unit, value, *_rest = _HWINFO_READING.unpack_from(buf, offset + i * size)
        if kind != HWINFO_TEMPERATURE or value != value:
            continue
        name = (_cstr(label) or _cstr(user)).lower()
        if _cstr(unit).endswith("F"):  # HWiNFO set to show Fahrenheit
            value = (value - 32.0) * 5.0 / 9.0
        if name and -40.0 < value < 150.0:
            out[name] = max(value, out.get(name, value))
    return out


def pick_temperature(temps: dict[str, float], labels: tuple[str, ...]) -> float | None:
    """The first of labels that HWiNFO has, in order of preference."""
    return next((temps[label] for label in labels if label in temps), None)


def _cstr(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("cp1252", errors="ignore").strip()


def hwinfo_temperatures() -> dict[str, float]:
    return read_shared(HWINFO_MAP, parse_hwinfo) or {}


# ------------------------------------------------------------------ PresentMon
PRESENTMON_SESSION = "PomoFPS"
PM_STALE_S = 4.0  # no frame for this long: the game isn't presenting (or PresentMon is stuck)
# The frame time column, newest naming first: v1 metrics, v2 metrics, then 2.4+ defaults' fallbacks
FRAME_TIME_COLUMNS = ("msbetweenpresents", "frametime", "msbetweenappstart", "msbetweendisplaychange")


class FrameRate:
    """Frames from PresentMon's CSV, fed one line at a time; fps over about the last second of them.

    Understands PresentMon 1.x columns (msBetweenPresents), 2.x (FrameTime) and 2.4+ defaults
    (MsBetweenPresents, MsBetweenAppStart). Columns are counted from the right so an Application name
    with a comma in it can't shift them.
    """

    def __init__(self, pid: int | None = None):
        self.pid = pid
        self.header: list[str] | None = None
        self._time_at: int | None = None   # index from the right (negative)
        self._pid_at: int | None = None
        self._frames: collections.deque[float] = collections.deque(maxlen=4000)
        self.last_frame_at: float | None = None

    def feed(self, line: str, now: float) -> None:
        cells = [cell.strip() for cell in line.strip().split(",")]
        low = [cell.lower() for cell in cells]
        column = next((name for name in FRAME_TIME_COLUMNS if name in low), None)
        if "application" in low and column is not None:  # a header (again, if PresentMon restarted)
            self.header = cells
            self._time_at = low.index(column) - len(cells)
            self._pid_at = low.index("processid") - len(cells) if "processid" in low else None
            self._frames.clear()
            return
        if self._time_at is None or len(cells) < len(self.header or ()):
            return
        if self.pid is not None and self._pid_at is not None and cells[self._pid_at] != str(self.pid):
            return
        try:
            ms = float(cells[self._time_at])
        except ValueError:  # "NA": a frame without that metric
            return
        if ms > 0:
            self._frames.append(ms)
            self.last_frame_at = now

    def fps(self, now: float) -> float | None:
        if self.last_frame_at is None or now - self.last_frame_at > PM_STALE_S:
            return None
        total, frames = 0.0, 0
        for ms in reversed(self._frames):
            total += ms
            frames += 1
            if total >= 1000.0:
                break
        return 1000.0 * frames / total if total > 0 else None


class _PresentMonRun:
    """One PresentMon reading one game's frames: started, read and ended by the worker thread.

    Two helper threads only drain its output pipes (a full pipe would stall it).
    """

    def __init__(self, exe: str, pid: int, job: int | None):
        self.exe, self.pid = exe, pid
        self.rate = FrameRate(pid)
        self.denied = False
        self._lock = threading.Lock()
        self.proc = subprocess.Popen(
            [exe, "--process_id", str(pid), "--output_stdout", "--no_console_stats", "--stop_existing_session",
             "--session_name", PRESENTMON_SESSION, "--terminate_on_proc_exit", "--no_track_input"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=CREATE_NO_WINDOW, text=True, encoding="utf-8", errors="replace", bufsize=1)
        if job:
            _join_job(job, self.proc.pid)
        threading.Thread(target=self._read_frames, name="pomo-presentmon-out", daemon=True).start()
        threading.Thread(target=self._read_errors, name="pomo-presentmon-err", daemon=True).start()

    def _read_frames(self) -> None:
        try:
            for line in self.proc.stdout:
                with self._lock:
                    self.rate.feed(line, time.monotonic())
        except (OSError, ValueError):
            pass

    def _read_errors(self) -> None:
        try:
            for line in self.proc.stderr:
                if "access denied" in line.lower():  # not elevated and not in Performance Log Users
                    self.denied = True
        except (OSError, ValueError):
            pass

    def alive(self) -> bool:
        return self.proc.poll() is None

    def fps(self) -> float | None:
        with self._lock:
            return self.rate.fps(time.monotonic())

    def stop(self) -> None:
        """End it, and its trace session: Windows keeps a killed tracer's session running otherwise."""
        if self.proc.poll() is None:
            try:
                subprocess.run([self.exe, "--terminate_existing_session", "--session_name", PRESENTMON_SESSION],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=CREATE_NO_WINDOW, timeout=5)
            except (OSError, subprocess.SubprocessError):
                pass
            try:
                self.proc.wait(1.5)  # its session ended under it, so it exits on its own
            except subprocess.TimeoutExpired:
                self.proc.kill()
                try:
                    self.proc.wait(2)
                except subprocess.TimeoutExpired:
                    pass


def _new_kill_job() -> int | None:
    """A job object that ends PresentMon if Pomo itself ends without stopping it (a crash, Task Manager)."""
    if sys.platform != "win32":
        return None
    import ctypes

    c = ctypes

    class Basic(c.Structure):  # JOBOBJECT_BASIC_LIMIT_INFORMATION
        _fields_ = [("PerProcessUserTimeLimit", c.c_int64), ("PerJobUserTimeLimit", c.c_int64),
                    ("LimitFlags", c.c_uint32), ("MinimumWorkingSetSize", c.c_size_t),
                    ("MaximumWorkingSetSize", c.c_size_t), ("ActiveProcessLimit", c.c_uint32),
                    ("Affinity", c.c_size_t), ("PriorityClass", c.c_uint32), ("SchedulingClass", c.c_uint32)]

    class Extended(c.Structure):  # JOBOBJECT_EXTENDED_LIMIT_INFORMATION
        _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", c.c_uint64 * 6), ("ProcessMemoryLimit", c.c_size_t),
                    ("JobMemoryLimit", c.c_size_t), ("PeakProcessMemoryUsed", c.c_size_t),
                    ("PeakJobMemoryUsed", c.c_size_t)]

    k = _kernel32()
    job = k.CreateJobObjectW(None, None)
    if not job:
        return None
    info = Extended()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k.SetInformationJobObject(job, 9, c.byref(info), c.sizeof(info)):  # JobObjectExtendedLimitInformation
        k.CloseHandle(job)
        return None
    return int(job)


def _join_job(job: int, pid: int) -> None:
    k = _kernel32()
    handle = k.OpenProcess(0x0100 | 0x0001, False, pid)  # PROCESS_SET_QUOTA | PROCESS_TERMINATE
    if handle:
        try:
            k.AssignProcessToJobObject(job, handle)
        finally:
            k.CloseHandle(handle)


def presentmon_path() -> str | None:
    """The bundled PresentMon.exe (tools/fetch_presentmon.py puts it in pomodoro/bin), if it's there."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS) / "pomodoro"
    else:
        base = Path(__file__).resolve().parent
    exe = base / "bin" / "PresentMon.exe"
    return str(exe) if exe.is_file() else None


# ------------------------------------------------------------------ the permission PresentMon needs
def fps_permission() -> bool:
    """True when PresentMon may trace frames: Pomo runs elevated, or you're in Performance Log Users.

    Checks this process's own token, so a group change counts only after signing out and in again.
    """
    if sys.platform != "win32":
        return False
    try:
        return _token_has(ADMINISTRATORS) or _token_has(PERFORMANCE_LOG_USERS)
    except OSError:
        return False


def _token_has(sid_text: str) -> bool:
    """Whether this process's token has a group enabled (Administrators counts only when elevated)."""
    import ctypes

    advapi32, kernel32 = ctypes.WinDLL("advapi32"), ctypes.WinDLL("kernel32")
    advapi32.ConvertStringSidToSidW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p)]
    advapi32.CheckTokenMembership.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    sid = ctypes.c_void_p()
    if not advapi32.ConvertStringSidToSidW(sid_text, ctypes.byref(sid)):
        return False
    try:
        member = ctypes.c_int(0)
        return bool(advapi32.CheckTokenMembership(None, sid, ctypes.byref(member))) and bool(member.value)
    finally:
        kernel32.LocalFree(sid)


def account_name() -> str:
    """This user as DOMAIN\\name ('DESKTOP-1\\sam'), or "" if Windows won't say."""
    if sys.platform != "win32":
        return ""
    import ctypes

    secur32 = ctypes.WinDLL("secur32")
    secur32.GetUserNameExW.argtypes = [ctypes.c_int, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)]
    size = ctypes.c_ulong(512)
    buf = ctypes.create_unicode_buffer(size.value)
    return buf.value if secur32.GetUserNameExW(2, buf, ctypes.byref(size)) else ""  # NameSamCompatible


def fps_permission_script(member: str) -> str:
    """The PowerShell that adds member to Performance Log Users, found by its SID so any Windows language works.

    Falls back to `net localgroup` where the LocalAccounts module is missing; already a member is fine.
    """
    quoted = "".join(ch * 2 if ch in "'\u2018\u2019\u201a\u201b" else ch for ch in member)  # PowerShell '...'
    return f"""$member = '{quoted}'
try {{
    Add-LocalGroupMember -SID '{PERFORMANCE_LOG_USERS}' -Member $member -ErrorAction Stop
}} catch {{
    if ($_.FullyQualifiedErrorId -like 'MemberExists*') {{ exit 0 }}
    $sid = New-Object System.Security.Principal.SecurityIdentifier('{PERFORMANCE_LOG_USERS}')
    $group = $sid.Translate([System.Security.Principal.NTAccount]).Value.Split('\\')[-1]
    net localgroup "$group" "$member" /add
    if ($LASTEXITCODE -ne 0) {{ exit 1 }}
}}
exit 0
"""


def request_fps_permission() -> bool:
    """Ask once (one UAC prompt) to add you to Performance Log Users, so PresentMon can read frame rates.

    The name comes from this unelevated process: the prompt may be answered with another admin's
    password, and it's you who should join. Blocks until the prompt is answered and the change is made
    (a second or two after). True if it ran; it takes effect after signing out and in again.
    """
    if sys.platform != "win32":
        return False
    member = account_name()
    if not member:
        return False
    import ctypes
    from ctypes import wintypes

    encoded = base64.b64encode(fps_permission_script(member).encode("utf-16-le")).decode("ascii")
    params = f"-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -EncodedCommand {encoded}"

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
                    ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                    ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE),
                    ("hProcess", wintypes.HANDLE)]

    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x00000040 | 0x00000100  # SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
    info.lpVerb, info.lpFile, info.lpParameters, info.nShow = "runas", "powershell.exe", params, 0  # SW_HIDE
    shell32, kernel32 = ctypes.WinDLL("shell32"), ctypes.WinDLL("kernel32")
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    try:
        if not shell32.ShellExecuteExW(ctypes.byref(info)):
            return False  # declined, or no admin could approve it
    except OSError:
        return False
    if not info.hProcess:
        return True
    try:
        if kernel32.WaitForSingleObject(info.hProcess, 30_000) != 0:  # still going: it did run
            return True
        code = wintypes.DWORD(1)
        kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return code.value == 0
    finally:
        kernel32.CloseHandle(info.hProcess)


# ------------------------------------------------------------------ the sampler (worker thread only)
class _Source:
    """A handle opened on first use; one that can't open is tried again a minute later, not every second."""

    def __init__(self, factory):
        self._factory = factory
        self.obj = None
        self._retry_at = 0.0

    def get(self):
        if self.obj is None and time.monotonic() >= self._retry_at:
            try:
                self.obj = self._factory()
            except Exception:
                self.obj = None
                self._retry_at = time.monotonic() + RETRY_S
        return self.obj

    def drop(self) -> None:
        """Close it (it stopped working); it opens again later."""
        obj, self.obj = self.obj, None
        self._retry_at = time.monotonic() + RETRY_S
        if obj is not None:
            try:
                obj.close()
            except Exception:
                pass


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


class Sampler:
    """Everything the worker reads and every handle it holds. Created, used and closed on one thread."""

    def __init__(self):
        self._cpu_before = None
        self._engines = _Source(lambda: _Pdh([ENGINE_COUNTER]))
        self._extras = _Source(lambda: _Pdh([VRAM_COUNTER, SHARED_COUNTER, THERMAL_COUNTER]))
        self._d3dkmt = _Source(_D3dkmt)
        self._nvml = _Source(_Nvml)
        self._engines_at = -1e9
        self.gpu_total: float | None = None
        self.busiest: tuple[int, int] | None = None
        self.gpu_by_pid: dict[int, float] | None = None
        self._pm: _PresentMonRun | None = None
        self._pm_exe: str | None | bool = False  # False: not looked for yet
        self._pm_allowed: bool | None = None
        self._pm_skip: set[int] = set()
        self._pm_blocked_until = 0.0
        self._job: int | None | bool = False

    # -------------------------------------------------- GPU use
    def collect_engines(self) -> None:
        query = self._engines.get()
        values = query.values(ENGINE_COUNTER) if query is not None and query.collect() else None
        if values is None:
            self.gpu_total, self.busiest, self.gpu_by_pid = None, None, None
            return
        self.gpu_total, self.busiest, self.gpu_by_pid = gpu_engines(values)
        self._engines_at = time.monotonic()

    def prime(self) -> None:
        """First collections, so the first reading after a pause has rates to compare against."""
        self._cpu_before = _safe(system_times)
        _safe(self.collect_engines)
        _safe(lambda: (self._extras.get() or _Nothing).collect())

    def idle(self) -> None:
        """Gaming mode is off: no frame tracing."""
        self._stop_presentmon()

    # -------------------------------------------------- one reading
    def sample(self, target: int | None) -> Reading:
        after = _safe(system_times)
        cpu = cpu_percent(self._cpu_before, after)
        self._cpu_before = after
        mem = _safe(memory)
        _safe(self.collect_engines)
        vram, zone_temp = _safe(self._extras_values, (None, None))
        hwinfo = _safe(hwinfo_temperatures, {})
        cpu_temp = pick_temperature(hwinfo, CPU_TEMP_LABELS)
        if cpu_temp is None:
            cpu_temp = zone_temp
        gpu_temp = _safe(self._gpu_temperature)
        if gpu_temp is None:
            gpu_temp = pick_temperature(hwinfo, GPU_TEMP_LABELS)
        fps, source = _safe(lambda: self._fps(target), (None, ""))
        return Reading(cpu=_round(cpu), gpu=_round(self.gpu_total),
                       ram=_round(mem[0]) if mem else None, ram_used_gb=_round(mem[1], 2) if mem else None,
                       ram_total_gb=_round(mem[2], 2) if mem else None, vram_used_gb=_round(vram, 2),
                       cpu_temp=_round(cpu_temp), gpu_temp=_round(gpu_temp), fps=_round(fps),
                       fps_source=source if fps is not None else "")

    def _extras_values(self) -> tuple[float | None, float | None]:
        """(video memory in use in GB, the hottest ACPI thermal zone in degrees C).

        Video memory is the GPU's dedicated memory; an integrated GPU has none (0) and keeps its
        textures in shared system memory, so for it that's the number that means something.
        """
        query = self._extras.get()
        if query is None or not query.collect():
            return None, None
        used = per_adapter(query.values(VRAM_COUNTER) or {}, self.busiest)
        if not used:
            used = per_adapter(query.values(SHARED_COUNTER) or {}, self.busiest) or used
        zones = [k - 273.15 for k in (query.values(THERMAL_COUNTER) or {}).values() if 0 < k - 273.15 < 125]
        return (used / GB if used is not None else None), (max(zones) if zones else None)

    def _gpu_temperature(self) -> float | None:
        d3d = self._d3dkmt.get()
        if d3d is not None:
            temps = d3d.temperatures()
            if temps:
                return temps.get(self.busiest) or max(temps.values())
        nvml = self._nvml.get()
        return nvml.temperature() if nvml is not None else None

    # -------------------------------------------------- frame rate
    def _fps(self, target: int | None) -> tuple[float | None, str]:
        if not target:
            self._stop_presentmon()
            return None, ""
        fps = _safe(lambda: rtss_fps(target))
        if fps is not None:
            return fps, "rtss"
        run = self._presentmon(target)
        fps = run.fps() if run is not None else None
        return (fps, "presentmon") if fps is not None else (None, "")

    def _presentmon(self, target: int) -> _PresentMonRun | None:
        if self._pm is not None and self._pm.pid != target:
            self._stop_presentmon()
        if self._pm is not None:
            if self._pm.alive():
                return self._pm
            # it ended: the game quit, or tracing wasn't allowed
            if self._pm.denied:
                self._pm_blocked_until = time.monotonic() + PM_DENIED_RETRY_S
            self._pm_skip.add(self._pm.pid)
            self._pm = None
            return None
        if target in self._pm_skip or time.monotonic() < self._pm_blocked_until:
            return None
        if self._pm_exe is False:
            self._pm_exe = presentmon_path()
        if self._pm_allowed is None:
            self._pm_allowed = fps_permission()
        if not self._pm_exe or not self._pm_allowed:
            return None
        if self._job is False:
            self._job = _safe(_new_kill_job)
        try:
            self._pm = _PresentMonRun(self._pm_exe, target, self._job or None)
        except (OSError, ValueError, subprocess.SubprocessError):
            self._pm_skip.add(target)
            return None
        return self._pm

    def _stop_presentmon(self) -> None:
        run, self._pm = self._pm, None
        if run is not None:
            _safe(run.stop)

    # -------------------------------------------------- what's in front
    def foreground(self, sampling: bool, sleep) -> gamedetect.Foreground | None:
        """The window in front, with its process's GPU use.

        While gaming mode samples anyway, the latest collection serves. Otherwise the GPU counters are
        read only for a fullscreen app Pomo can't already rule out, since that's the only time the
        decision needs them (gpu is None the rest of the time).
        """
        fg = gamedetect.foreground(None)
        if fg is None:
            return None
        if not sampling:
            if not fg.fullscreen or gamedetect.exe_key(fg.exe) in gamedetect.NOT_GAMES:
                return fg
            if time.monotonic() - self._engines_at > ENGINES_FRESH_S:
                _safe(self.collect_engines)  # stale: start a fresh pair so the rate is about now
                sleep(PRIME_S)
            _safe(self.collect_engines)
        if self.gpu_by_pid is None:
            return fg
        return dataclasses.replace(fg, gpu=round(float(self.gpu_by_pid.get(fg.pid, 0.0)), 1))

    def close(self) -> None:
        self._stop_presentmon()
        for source in (self._engines, self._extras, self._d3dkmt, self._nvml):
            source.drop()
        if self._job:
            _safe(lambda: _kernel32().CloseHandle(self._job))
            self._job = None


class _Nothing:
    @staticmethod
    def collect() -> bool:
        return False


def _round(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(float(value), digits)


# ------------------------------------------------------------------ the worker
class TelemetryWorker(QThread):
    """Watches what's in front (always) and samples telemetry (in gaming mode) on its own thread.

    Its signals reach the UI thread queued. The UI only sets two switches (set_target, set_sampling);
    every query, handle and PresentMon process belongs to this thread and closes with it. stop() is
    also connected to the app quitting, so a forgotten stop can't leave PresentMon running.
    """

    reading = Signal(object)     # a Reading about once a second while sampling is on
    foreground = Signal(object)  # a gamedetect.Foreground (with .gpu filled in) or None, about every 2 s

    def __init__(self, parent=None):
        super().__init__(parent)
        self._target: int | None = None
        self._sampling = False
        self._stopping = False
        self._wake = threading.Event()
        self._ident: int | None = None
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.stop)

    # ------------------------------------------------------------ UI thread
    def set_target(self, pid: int | None) -> None:
        """The game to read the frame rate of; None for none."""
        self._target = int(pid) if pid else None

    def set_sampling(self, on: bool) -> None:
        """On: also sample telemetry (gaming mode). Off: only watch what's in front."""
        self._sampling = bool(on)
        self._wake.set()

    def stop(self) -> None:
        """End the thread and any PresentMon it started, and wait for both."""
        self._stopping = True
        self._wake.set()
        if self.isRunning() and threading.get_ident() != self._ident:
            self.wait(15_000)

    # ------------------------------------------------------------ worker thread
    def _sleep(self, seconds: float) -> bool:
        """Wait, waking early for stop(). True if stopping."""
        if seconds > 0 and not self._stopping:
            self._wake.wait(seconds)
        return self._stopping

    def run(self) -> None:
        self._ident = threading.get_ident()
        sampler = None
        try:
            sampler = Sampler()
            was_sampling = False
            next_foreground = 0.0
            while not self._stopping:
                self._wake.clear()
                started = time.monotonic()
                sampling = self._sampling
                try:
                    if sampling:
                        if not was_sampling:
                            sampler.prime()
                            if self._sleep(PRIME_S):
                                break
                            started = time.monotonic()
                        self.reading.emit(sampler.sample(self._target))
                    else:
                        sampler.idle()
                    was_sampling = sampling
                    if time.monotonic() >= next_foreground:
                        next_foreground = time.monotonic() + FOREGROUND_S - 0.05
                        self.foreground.emit(sampler.foreground(sampling, self._sleep))
                except Exception:  # one bad round; the next may well work
                    pass
                interval = SAMPLE_S if sampling else FOREGROUND_S
                self._sleep(started + interval - time.monotonic())
        except Exception:  # the readout goes quiet; Pomo carries on
            pass
        finally:
            if sampler is not None:
                _safe(sampler.close)
