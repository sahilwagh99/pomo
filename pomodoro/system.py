"""System controls: the PC's volume, sound output, microphone, screen brightness and Bluetooth, from Pomo.

Everything here talks straight to Windows as the signed-in user (no admin rights):
- Sound: Core Audio (IMMDeviceEnumerator and IAudioEndpointVolume) for the volume and mute of the default
  speakers and microphone, and IPolicyConfig to switch the default output. IPolicyConfig is undocumented,
  but it's what Windows' own sound settings use, and it has stayed the same since Windows 7.
- Brightness: WMI's WmiMonitorBrightness. Only built-in (laptop) screens support it; desktop PCs and
  external monitors read as None, since those have their own buttons.
- Bluetooth: WinRT's BluetoothDevice and BluetoothLEDevice, for the paired devices and whether each is
  connected. Connecting itself happens in Windows' Bluetooth settings.

The COM calls go through plain ctypes, so there's no extra package to bundle and importing this sets
nothing up. Each call works on whatever thread it runs on, but WMI and Bluetooth can take a moment, so the
UI runs them through Jobs, a background thread that hands results back on the UI thread.

Nothing here raises: when an API isn't there (another OS, no speakers, no Bluetooth radio), calls return
None, [] or False, and Pomo leaves that control out.
"""
from __future__ import annotations

import asyncio
import ctypes
import functools
import os
import re
import sys
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from ctypes import POINTER, byref, c_float, c_int, c_long, c_uint, c_ulong, c_ushort, c_void_p, c_wchar_p
from dataclasses import dataclass

from PySide6.QtCore import QObject, Qt, Signal

WINDOWS = sys.platform == "win32"
IDLE_S = 20.0            # a Jobs thread ends after this long with nothing to do
WMI_WAIT_MS = 5000       # how long one WMI result may take
BLUETOOTH_WAIT_S = 10.0  # how long listing the paired devices may take

# Core Audio
CLSID_MMDEVICE_ENUMERATOR = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
IID_IMMDEVICE_ENUMERATOR = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"
IID_IAUDIO_ENDPOINT_VOLUME = "{5CDF2C82-841E-4546-9722-0CF74078229A}"
CLSID_POLICY_CONFIG = "{870AF99C-171D-4F9E-AF0D-E63DF40C2BC9}"
IID_IPOLICY_CONFIG = "{F8679F50-850A-41CF-9C72-430F290290C8}"
RENDER, CAPTURE = 0, 1                       # EDataFlow
CONSOLE, MULTIMEDIA, COMMUNICATIONS = 0, 1, 2  # ERole
DEVICE_STATE_ACTIVE = 1
STGM_READ = 0
CLSCTX_ALL = 23
COINIT_MULTITHREADED = 0

# WMI
CLSID_WBEM_LOCATOR = "{4590F811-1D3A-11D0-891F-00AA004B2E24}"
IID_IWBEM_LOCATOR = "{DC12A687-737F-11CF-884D-00AA004B2E24}"
WBEM_FORWARD_ONLY_IMMEDIATELY = 0x20 | 0x10
RPC_C_AUTHN_WINNT, RPC_C_AUTHZ_NONE, RPC_C_AUTHN_LEVEL_CALL, RPC_C_IMP_LEVEL_IMPERSONATE = 10, 0, 3, 3

# VARIANT and PROPVARIANT types read or written here
VT_EMPTY, VT_NULL, VT_I2, VT_I4, VT_BSTR, VT_BOOL, VT_UI1, VT_UI2, VT_UI4, VT_LPWSTR = 0, 1, 2, 3, 8, 11, 17, 18, 19, 31

# Vtable slots: each method's place in its interface, after IUnknown's three
ENUM_ENDPOINTS, ENUM_DEFAULT = 3, 4                    # IMMDeviceEnumerator
DEVICE_ACTIVATE, DEVICE_PROPERTIES, DEVICE_ID = 3, 4, 5  # IMMDevice
COLLECTION_COUNT, COLLECTION_ITEM = 3, 4                # IMMDeviceCollection
STORE_GET_VALUE = 5                                     # IPropertyStore
VOLUME_SET, VOLUME_GET, MUTE_SET, MUTE_GET = 7, 9, 14, 15  # IAudioEndpointVolume (the "Scalar" ones)
POLICY_SET_DEFAULT = 13                                 # IPolicyConfig.SetDefaultEndpoint
LOCATOR_CONNECT = 3                                     # IWbemLocator.ConnectServer
SERVICES_GET_OBJECT, SERVICES_QUERY, SERVICES_EXEC_METHOD = 6, 20, 24  # IWbemServices
ENUM_NEXT = 4                                           # IEnumWbemClassObject
OBJECT_GET, OBJECT_PUT, OBJECT_SPAWN, OBJECT_GET_METHOD = 4, 5, 15, 19  # IWbemClassObject


@dataclass(frozen=True)
class AudioDevice:
    id: str
    name: str
    is_default: bool = False


@dataclass(frozen=True)
class PairedDevice:
    id: str
    name: str
    connected: bool = False


@dataclass(frozen=True)
class Levels:
    """What the sliders and toggles show. None means there's nothing to control."""

    volume: int | None = None      # 0-100; None: no speakers or headphones
    muted: bool | None = None
    mic_muted: bool | None = None  # None: no microphone
    brightness: int | None = None  # 0-100; None: Windows can't set this screen's brightness


# ------------------------------------------------------------------ COM through ctypes
class GUID(ctypes.Structure):
    _fields_ = [("data1", c_ulong), ("data2", c_ushort), ("data3", c_ushort), ("data4", ctypes.c_ubyte * 8)]


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", c_ulong)]


class _Value(ctypes.Union):
    _fields_ = [("i8", ctypes.c_longlong), ("i4", c_long), ("u1", ctypes.c_ubyte), ("i2", ctypes.c_short),
                ("p", c_void_p), ("pair", c_void_p * 2)]


class VARIANT(ctypes.Structure):
    """VARIANT, and PROPVARIANT too: they share this layout for the simple types used here."""

    _fields_ = [("vt", c_ushort), ("r1", c_ushort), ("r2", c_ushort), ("r3", c_ushort), ("v", _Value)]


def _guid(text: str) -> GUID:
    return GUID.from_buffer_copy(uuid.UUID(text).bytes_le)


PKEY_FRIENDLY_NAME = PROPERTYKEY(_guid("{A45C254E-DF1C-4EFD-8020-67D146A850E0}"), 14)


@functools.cache
def _dll():
    """Our own handles on the COM DLLs, so the argtypes set here can't clash with anyone else's."""
    from types import SimpleNamespace

    ole32, oleaut32, user32 = ctypes.WinDLL("ole32"), ctypes.WinDLL("oleaut32"), ctypes.WinDLL("user32")
    hr = ctypes.HRESULT
    for fn, args, res in (
        (ole32.CoInitializeEx, (c_void_p, c_ulong), c_long),  # not HRESULT: a thread set up otherwise is fine
        (ole32.CoUninitialize, (), None),
        (ole32.CoCreateInstance, (POINTER(GUID), c_void_p, c_ulong, POINTER(GUID), POINTER(c_void_p)), hr),
        (ole32.CoSetProxyBlanket, (c_void_p, c_ulong, c_ulong, c_void_p, c_ulong, c_ulong, c_void_p, c_ulong), hr),
        (ole32.CoTaskMemFree, (c_void_p,), None),
        (ole32.PropVariantClear, (POINTER(VARIANT),), hr),
        (oleaut32.SysAllocString, (c_wchar_p,), c_void_p),
        (oleaut32.SysFreeString, (c_void_p,), None),
        (oleaut32.VariantClear, (POINTER(VARIANT),), hr),
        (user32.LockWorkStation, (), ctypes.c_bool),
    ):
        fn.argtypes, fn.restype = args, res
    return SimpleNamespace(ole32=ole32, oleaut32=oleaut32, user32=user32)


@functools.cache
def _proto(argtypes: tuple):
    return ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, *argtypes)


@functools.cache
def _release_proto():
    return ctypes.WINFUNCTYPE(c_ulong, c_void_p)


class _Com:
    """A COM interface pointer, released when its with block ends."""

    def __init__(self):
        self.ptr = c_void_p()

    @property
    def out(self):
        return byref(self.ptr)

    def call(self, slot: int, argtypes: tuple = (), *args) -> int:
        """Calls the method in vtable slot `slot`. A failed HRESULT raises OSError."""
        if not self.ptr:
            raise OSError("no COM interface")
        vtable = ctypes.cast(self.ptr, POINTER(POINTER(c_void_p))).contents
        return _proto(argtypes)(vtable[slot])(self.ptr, *args)

    def release(self) -> None:
        if self.ptr:
            vtable = ctypes.cast(self.ptr, POINTER(POINTER(c_void_p))).contents
            _release_proto()(vtable[2])(self.ptr)
            self.ptr = c_void_p()

    def __enter__(self) -> _Com:
        return self

    def __exit__(self, *_exc) -> None:
        self.release()


@contextmanager
def _com_thread() -> Iterator[None]:
    """COM ready on this thread for as long as the block runs.

    Worker threads start without COM, so they join the multithreaded apartment. The UI thread already has
    COM (Qt sets it up single-threaded); that's fine too, and stays as it is.
    """
    if not WINDOWS:
        yield
        return
    ole32 = _dll().ole32
    result = ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
    try:
        yield
    finally:
        if result in (0, 1):  # S_OK or S_FALSE: ours to balance
            ole32.CoUninitialize()


def _create(clsid: str, iid: str, com: _Com) -> None:
    _dll().ole32.CoCreateInstance(byref(_guid(clsid)), None, CLSCTX_ALL, byref(_guid(iid)), com.out)


def _soft(default):
    """Runs a call with COM ready, and returns `default` instead of raising: no control is worth a crash."""
    def wrap(fn):
        @functools.wraps(fn)
        def run(*args, **kwargs):
            if not WINDOWS:
                return default
            try:
                with _com_thread():
                    return fn(*args, **kwargs)
            except Exception:  # no device, no driver support, an API that isn't there
                return default
        return run
    return wrap


# ------------------------------------------------------------------ sound
@contextmanager
def _endpoint_volume(flow: int, role: int = CONSOLE) -> Iterator[_Com]:
    """The default speakers' (RENDER) or microphone's (CAPTURE) volume control."""
    with _Com() as enum, _Com() as device, _Com() as volume:
        _create(CLSID_MMDEVICE_ENUMERATOR, IID_IMMDEVICE_ENUMERATOR, enum)
        enum.call(ENUM_DEFAULT, (c_int, c_int, POINTER(c_void_p)), flow, role, device.out)
        device.call(DEVICE_ACTIVATE, (POINTER(GUID), c_ulong, c_void_p, POINTER(c_void_p)),
                    byref(_guid(IID_IAUDIO_ENDPOINT_VOLUME)), CLSCTX_ALL, None, volume.out)
        yield volume


def _read_mute(volume: _Com) -> bool:
    muted = c_int()
    volume.call(MUTE_GET, (POINTER(c_int),), byref(muted))
    return bool(muted.value)


def _write_mute(volume: _Com, muted: bool) -> None:
    volume.call(MUTE_SET, (c_int, c_void_p), int(bool(muted)), None)


@_soft(None)
def get_volume() -> int | None:
    """The default output's volume, 0-100, as Windows' own volume flyout shows it."""
    level = c_float()
    with _endpoint_volume(RENDER) as volume:
        volume.call(VOLUME_GET, (POINTER(c_float),), byref(level))
    return max(0, min(100, round(level.value * 100)))


@_soft(False)
def set_volume(level: int) -> bool:
    with _endpoint_volume(RENDER) as volume:
        volume.call(VOLUME_SET, (c_float, c_void_p), max(0, min(100, int(level))) / 100, None)
    return True


@_soft(None)
def get_mute() -> bool | None:
    with _endpoint_volume(RENDER) as volume:
        return _read_mute(volume)


@_soft(False)
def set_mute(muted: bool) -> bool:
    with _endpoint_volume(RENDER) as volume:
        _write_mute(volume, muted)
    return True


@_soft(None)
def get_mic_mute() -> bool | None:
    """Whether the default microphone is muted; None when there's no microphone."""
    with _endpoint_volume(CAPTURE) as volume:
        return _read_mute(volume)


@_soft(False)
def set_mic_mute(muted: bool) -> bool:
    """Mutes the default microphone, and the default one for calls if that's a different one."""
    done = False
    for role in (CONSOLE, COMMUNICATIONS):
        try:
            with _endpoint_volume(CAPTURE, role) as volume:
                _write_mute(volume, muted)
            done = True
        except OSError:
            if role == CONSOLE:
                raise
    return done


def _device_id(device: _Com) -> str:
    raw = c_void_p()
    device.call(DEVICE_ID, (POINTER(c_void_p),), byref(raw))
    try:
        return ctypes.wstring_at(raw.value) if raw.value else ""
    finally:
        _dll().ole32.CoTaskMemFree(raw)


def _friendly_name(device: _Com) -> str:
    with _Com() as store:
        device.call(DEVICE_PROPERTIES, (c_ulong, POINTER(c_void_p)), STGM_READ, store.out)
        value = VARIANT()
        store.call(STORE_GET_VALUE, (POINTER(PROPERTYKEY), POINTER(VARIANT)), byref(PKEY_FRIENDLY_NAME), byref(value))
        try:
            return ctypes.wstring_at(value.v.p) if value.vt == VT_LPWSTR and value.v.p else ""
        finally:
            _dll().ole32.PropVariantClear(byref(value))


@_soft([])
def output_devices() -> list[AudioDevice]:
    """The speakers and headphones Windows can play to right now, in its own order."""
    devices = []
    with _Com() as enum, _Com() as default, _Com() as collection:
        _create(CLSID_MMDEVICE_ENUMERATOR, IID_IMMDEVICE_ENUMERATOR, enum)
        try:
            enum.call(ENUM_DEFAULT, (c_int, c_int, POINTER(c_void_p)), RENDER, CONSOLE, default.out)
            default_id = _device_id(default)
        except OSError:
            default_id = ""
        enum.call(ENUM_ENDPOINTS, (c_int, c_ulong, POINTER(c_void_p)), RENDER, DEVICE_STATE_ACTIVE, collection.out)
        count = c_uint()
        collection.call(COLLECTION_COUNT, (POINTER(c_uint),), byref(count))
        for i in range(count.value):
            with _Com() as device:
                collection.call(COLLECTION_ITEM, (c_uint, POINTER(c_void_p)), i, device.out)
                device_id = _device_id(device)
                try:
                    name = _friendly_name(device)
                except OSError:
                    name = ""
                devices.append(AudioDevice(device_id, name or "Speakers", device_id == default_id))
    return devices


@_soft(False)
def set_default_output(device_id: str) -> bool:
    """Makes a device the default output for everything: music, games and calls alike."""
    if not device_id:
        return False
    with _Com() as policy:
        _create(CLSID_POLICY_CONFIG, IID_IPOLICY_CONFIG, policy)
        for role in (CONSOLE, MULTIMEDIA, COMMUNICATIONS):
            policy.call(POLICY_SET_DEFAULT, (c_wchar_p, c_int), device_id, role)
    return True


# ------------------------------------------------------------------ brightness (WMI)
@contextmanager
def _bstr(text: str) -> Iterator[int]:
    ptr = _dll().oleaut32.SysAllocString(text)
    try:
        yield ptr
    finally:
        _dll().oleaut32.SysFreeString(ptr)


def _blanket(com: _Com) -> None:
    """Lets WMI act as us for this proxy (Pomo doesn't set COM security for the whole process)."""
    try:
        _dll().ole32.CoSetProxyBlanket(com.ptr, RPC_C_AUTHN_WINNT, RPC_C_AUTHZ_NONE, None, RPC_C_AUTHN_LEVEL_CALL,
                                       RPC_C_IMP_LEVEL_IMPERSONATE, None, 0)
    except OSError:  # not a proxy: nothing to set
        pass


@contextmanager
def _wmi(namespace: str = r"root\wmi") -> Iterator[_Com]:
    with _Com() as locator, _Com() as services:
        _create(CLSID_WBEM_LOCATOR, IID_IWBEM_LOCATOR, locator)
        with _bstr(namespace) as ns:
            locator.call(LOCATOR_CONNECT, (c_void_p,) * 4 + (c_long, c_void_p, c_void_p, POINTER(c_void_p)),
                         ns, None, None, None, 0, None, None, services.out)
        _blanket(services)
        yield services


def _each(services: _Com, wql: str) -> Iterator[_Com]:
    """Each object a WQL query finds, released after its turn."""
    with _Com() as found, _bstr("WQL") as language, _bstr(wql) as query:
        services.call(SERVICES_QUERY, (c_void_p, c_void_p, c_long, c_void_p, POINTER(c_void_p)),
                      language, query, WBEM_FORWARD_ONLY_IMMEDIATELY, None, found.out)
        _blanket(found)
        while True:
            with _Com() as obj:
                got = c_ulong()
                found.call(ENUM_NEXT, (c_long, c_ulong, POINTER(c_void_p), POINTER(c_ulong)),
                           WMI_WAIT_MS, 1, obj.out, byref(got))
                if not got.value:
                    return
                yield obj


def _from_variant(value: VARIANT):
    vt = value.vt
    if vt == VT_BSTR:
        return ctypes.wstring_at(value.v.p) if value.v.p else ""
    if vt == VT_BOOL:
        return value.v.i2 != 0
    if vt == VT_UI1:
        return value.v.u1
    if vt == VT_I2:
        return value.v.i2
    if vt in (VT_UI2, VT_UI4):
        return value.v.i4 & (0xFFFF if vt == VT_UI2 else 0xFFFFFFFF)
    if vt == VT_I4:
        return value.v.i4
    return None


def _get(obj: _Com, name: str):
    value = VARIANT()
    obj.call(OBJECT_GET, (c_wchar_p, c_long, POINTER(VARIANT), c_void_p, c_void_p), name, 0, byref(value), None, None)
    try:
        return _from_variant(value)
    finally:
        _dll().oleaut32.VariantClear(byref(value))


def _put(obj: _Com, name: str, vt: int, number: int) -> None:
    """Sets a number property; WMI wants VT_UI1 for a uint8 and VT_I4 for a uint32."""
    value = VARIANT()
    value.vt = vt
    if vt == VT_UI1:
        value.v.u1 = number
    else:
        value.v.i4 = number
    obj.call(OBJECT_PUT, (c_wchar_p, c_long, POINTER(VARIANT), c_long), name, 0, byref(value), 0)


@_soft(None)
def get_brightness() -> int | None:
    """The built-in screen's brightness, 0-100; None on PCs and monitors where Windows can't set it."""
    with _wmi() as wmi:
        for obj in _each(wmi, "SELECT CurrentBrightness FROM WmiMonitorBrightness WHERE Active = TRUE"):
            level = _get(obj, "CurrentBrightness")
            if isinstance(level, int):
                return max(0, min(100, level))
    return None


@_soft(False)
def set_brightness(level: int) -> bool:
    level = max(0, min(100, int(level)))
    done = False
    with _wmi() as wmi, _Com() as methods, _Com() as signature, _Com() as params:
        with _bstr("WmiMonitorBrightnessMethods") as name:
            wmi.call(SERVICES_GET_OBJECT, (c_void_p, c_long, c_void_p, POINTER(c_void_p), c_void_p),
                     name, 0, None, methods.out, None)
        methods.call(OBJECT_GET_METHOD, (c_wchar_p, c_long, POINTER(c_void_p), c_void_p),
                     "WmiSetBrightness", 0, signature.out, None)
        signature.call(OBJECT_SPAWN, (c_long, POINTER(c_void_p)), 0, params.out)
        _put(params, "Timeout", VT_I4, 1)  # seconds; the same as everyone's PowerShell one-liner
        _put(params, "Brightness", VT_UI1, level)
        for screen in _each(wmi, "SELECT * FROM WmiMonitorBrightnessMethods WHERE Active = TRUE"):
            path = _get(screen, "__RELPATH")
            if not path:
                continue
            with _bstr(path) as target, _bstr("WmiSetBrightness") as method:
                wmi.call(SERVICES_EXEC_METHOD, (c_void_p, c_void_p, c_long, c_void_p, c_void_p, c_void_p, c_void_p),
                         target, method, 0, None, params.ptr, None, None)
            done = True
    return done


def read_levels() -> Levels:
    """Everything the sliders and toggles show, in one go. The brightness (WMI) is the slow part."""
    return Levels(get_volume(), get_mute(), get_mic_mute(), get_brightness())


# ------------------------------------------------------------------ Bluetooth (WinRT)
def merge_paired(found: list[tuple[str, str, int, bool]]) -> list[PairedDevice]:
    """One entry per device from (id, name, address, connected), connected ones first.

    Earbuds and the like are often paired twice, as classic Bluetooth and as Bluetooth LE (sometimes named
    "LE-Something"), so entries with the same address or the same name are one device.
    """
    merged: list[PairedDevice] = []
    keys: list[set] = []
    for device_id, name, address, connected in found:
        name = (name or "").strip() or "Bluetooth device"
        key = {("name", re.sub(r"^le[-_ ]", "", name, flags=re.IGNORECASE).casefold())}
        if address:
            key.add(("address", address))
        for i, seen in enumerate(keys):
            if seen & key:
                old = merged[i]
                merged[i] = PairedDevice(old.id, old.name, old.connected or connected)
                seen |= key
                break
        else:
            merged.append(PairedDevice(device_id, name, connected))
            keys.append(key)
    return sorted(merged, key=lambda d: (not d.connected, d.name.casefold()))


async def _paired() -> list[tuple[str, str, int, bool]]:
    from winrt.windows.devices.bluetooth import BluetoothConnectionStatus, BluetoothDevice, BluetoothLEDevice
    from winrt.windows.devices.enumeration import DeviceInformation

    found = []
    for kind in (BluetoothDevice, BluetoothLEDevice):  # classic first: its names are the friendlier ones
        infos = await DeviceInformation.find_all_async_aqs_filter(kind.get_device_selector_from_pairing_state(True))
        for info in infos:
            try:
                device = await kind.from_id_async(info.id)
            except Exception:  # unpaired a moment ago, or the radio is busy
                device = None
            if device is None:
                found.append((info.id, info.name, 0, False))
                continue
            try:
                connected = device.connection_status == BluetoothConnectionStatus.CONNECTED
                found.append((info.id, device.name or info.name, int(device.bluetooth_address), connected))
            finally:
                device.close()
    return found


def bluetooth_devices() -> list[PairedDevice] | None:
    """The paired Bluetooth devices, connected ones first ([] when none are paired).

    None when this PC has no Bluetooth (or no WinRT). It can take a second: call it off the UI thread.
    """
    if not WINDOWS:
        return None
    try:
        import winrt.windows.devices.bluetooth  # noqa: F401
        import winrt.windows.devices.enumeration  # noqa: F401
    except Exception:  # the packages aren't there, or this Windows lacks the API
        return None
    try:
        with _com_thread():
            return merge_paired(asyncio.run(asyncio.wait_for(_paired(), BLUETOOTH_WAIT_S)))
    except Exception:  # no radio, the service is off, or it took too long
        return None


# ------------------------------------------------------------------ one-shot actions
def _launch(target: str) -> bool:
    if not WINDOWS:
        return False
    try:
        os.startfile(target)
    except OSError:
        return False
    return True


def lock_pc() -> bool:
    if not WINDOWS:
        return False
    try:
        return bool(_dll().user32.LockWorkStation())
    except OSError:
        return False


def screenshot() -> bool:
    """Opens Windows' screen snip (the same as Win+Shift+S)."""
    return _launch("ms-screenclip:")


def open_sound_settings() -> bool:
    return _launch("ms-settings:sound")


def open_bluetooth_settings() -> bool:
    return _launch("ms-settings:bluetooth")


def open_display_settings() -> bool:
    return _launch("ms-settings:display")


# ------------------------------------------------------------------ off the UI thread
class Jobs(QObject):
    """Runs system calls on a background thread, so the UI never waits on Windows.

    A call still waiting is replaced by a new one with the same key, so dragging a slider sends only the
    latest level. Results come back on the UI thread, to the `then` given with the call. The thread starts
    with the first call and ends after IDLE_S with nothing to do.
    """

    _finished = Signal(object, object)  # (then, result)

    def __init__(self, parent: QObject | None = None, name: str = "pomo-system"):
        super().__init__(parent)
        self.name = name
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._waiting: dict[str, tuple[Callable, tuple, Callable | None]] = {}
        self._thread: threading.Thread | None = None
        self._running = False
        self._finished.connect(self._deliver, Qt.QueuedConnection)

    def run(self, key: str, fn: Callable, *args, then: Callable | None = None) -> None:
        with self._lock:
            self._waiting.pop(key, None)  # the new call goes to the back of the line
            self._waiting[key] = (fn, args, then)
            if self._thread is None:
                self._thread = threading.Thread(target=self._work, name=self.name, daemon=True)
                self._thread.start()
        self._wake.set()

    def busy(self) -> bool:
        with self._lock:
            return bool(self._waiting) or self._running

    def _deliver(self, then: Callable, result) -> None:
        try:
            then(result)
        except RuntimeError:  # its widget closed while the call ran
            pass

    def _next(self):
        with self._lock:
            if self._waiting:
                key = next(iter(self._waiting))
                self._running = True
                return self._waiting.pop(key)
            self._running = False
            self._wake.clear()
            return None

    def _work(self) -> None:
        with _com_thread():  # once for all of this thread's calls
            while True:
                job = self._next()
                if job is None:
                    if self._wake.wait(IDLE_S):
                        continue
                    with self._lock:
                        if self._waiting:
                            continue
                        self._thread = None
                        return
                fn, args, then = job
                try:
                    result = fn(*args)
                except Exception:  # the calls here fail soft already; this is for anything else
                    result = None
                if then is None:
                    continue
                try:
                    self._finished.emit(then, result)
                except RuntimeError:  # this Jobs is gone (its page closed): so is the reason to go on
                    with self._lock:
                        self._thread, self._running = None, False
                    return
