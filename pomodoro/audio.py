"""Sound in and out through Windows' own waveOut and waveIn (winmm), with no extra packages.

LoopPlayer plays a sound over and over on a background thread (Pomo's focus sounds), with its own
volume and soft fades, alongside Pomo's alert sounds. Recorder captures the microphone for Talk
to Pomo and hands back a WAV file's bytes.

Audio is 16-bit mono PCM. Volume and fades are applied in software, a quarter second at a time,
so they never touch the system volume or other apps.
"""
from __future__ import annotations

import io
import math
import struct
import sys
import threading
import time
import wave
from array import array

CHUNK_S = 0.2           # each buffer handed to Windows
BUFFERS = 3             # in flight at once: about 0.6 s of sound queued
WHDR_DONE = 0x1
CALLBACK_EVENT = 0x50000
WAVE_MAPPER = 0xFFFFFFFF
WAIT_MS = 100


def available() -> bool:
    return sys.platform == "win32"


def _winmm():
    import ctypes
    from ctypes import wintypes

    class WAVEFORMATEX(ctypes.Structure):
        _fields_ = [("wFormatTag", wintypes.WORD), ("nChannels", wintypes.WORD), ("nSamplesPerSec", wintypes.DWORD),
                    ("nAvgBytesPerSec", wintypes.DWORD), ("nBlockAlign", wintypes.WORD),
                    ("wBitsPerSample", wintypes.WORD), ("cbSize", wintypes.WORD)]

    class WAVEHDR(ctypes.Structure):
        pass

    WAVEHDR._fields_ = [("lpData", ctypes.c_void_p), ("dwBufferLength", wintypes.DWORD),
                        ("dwBytesRecorded", wintypes.DWORD), ("dwUser", ctypes.c_size_t),
                        ("dwFlags", wintypes.DWORD), ("dwLoops", wintypes.DWORD),
                        ("lpNext", ctypes.POINTER(WAVEHDR)), ("reserved", ctypes.c_size_t)]
    return ctypes, wintypes, WAVEFORMATEX, WAVEHDR


def _format(WAVEFORMATEX, rate: int, channels: int = 1):
    return WAVEFORMATEX(1, channels, rate, rate * 2 * channels, 2 * channels, 16, 0)  # PCM, 16-bit


def take(samples: array, pos: int, count: int) -> tuple[array, int]:
    """count samples from pos, wrapping around to the start (the sound loops), and where to go on from."""
    n = len(samples)
    piece = array("h")
    while len(piece) < count:
        end = min(n, pos + count - len(piece))
        piece += samples[pos:end]
        pos = end % n
    return piece, pos


def scaled(samples, gain_from: float, gain_to: float) -> bytes:
    """16-bit samples with a gain that ramps from one value to another across them, as bytes."""
    n = len(samples)
    if n == 0:
        return b""
    if abs(gain_to - gain_from) < 1e-4:
        g = gain_from
        out = array("h", [int(s * g) for s in samples])
    else:
        step = (gain_to - gain_from) / n
        out = array("h", [int(s * (gain_from + step * i)) for i, s in enumerate(samples)])
    return out.tobytes()


class LoopPlayer:
    """Loops one sound on a background thread. play(), set_volume() and stop() return at once."""

    def __init__(self):
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._samples = array("h")
        self._rate = 22050
        self._channels = 1
        self._volume = 0.5
        self._stop_requested = False
        self._fade_in_s = 1.5
        self._fade_out_s = 1.2
        self.error = ""

    @property
    def playing(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop_requested

    def play(self, samples: array, rate: int, volume: float, fade_in_s: float = 1.5, channels: int = 1) -> None:
        """Start looping (or switch to) a sound. Stereo samples come interleaved."""
        self.stop(fade_out_s=0.25, wait=True)
        with self._lock:
            self._samples, self._rate, self._channels = samples, rate, channels
            self._volume = max(0.0, min(1.0, volume))
            self._fade_in_s = fade_in_s
            self._stop_requested = False
        if not available() or not len(samples):
            return
        self._thread = threading.Thread(target=self._run, name="pomo-sounds", daemon=True)
        self._thread.start()

    def set_volume(self, volume: float) -> None:
        with self._lock:
            self._volume = max(0.0, min(1.0, volume))

    def stop(self, fade_out_s: float = 1.2, wait: bool = False) -> None:
        with self._lock:
            self._stop_requested = True
            self._fade_out_s = fade_out_s
        thread = self._thread
        if wait and thread is not None:
            thread.join(timeout=fade_out_s + 2.0)
        if thread is None or not thread.is_alive():
            self._thread = None

    # ------------------------------------------------------------ the thread
    def _run(self) -> None:
        try:
            self._stream()
        except Exception as exc:  # no sound device, or it went away: Pomo carries on silently
            self.error = str(exc)

    def _stream(self) -> None:
        ctypes, wintypes, WAVEFORMATEX, WAVEHDR = _winmm()
        winmm, kernel32 = ctypes.windll.winmm, ctypes.windll.kernel32
        with self._lock:
            samples, rate, channels = self._samples, self._rate, self._channels
        event = kernel32.CreateEventW(None, False, False, None)
        hwo = ctypes.c_void_p()
        fmt = _format(WAVEFORMATEX, rate, channels)
        if winmm.waveOutOpen(ctypes.byref(hwo), WAVE_MAPPER, ctypes.byref(fmt), event, 0, CALLBACK_EVENT):
            kernel32.CloseHandle(event)
            raise OSError("no sound device")
        chunk = max(256, int(rate * CHUNK_S)) * channels  # whole frames, so the channels never swap
        buffers = [ctypes.create_string_buffer(chunk * 2) for _ in range(BUFFERS)]
        headers = [WAVEHDR() for _ in range(BUFFERS)]
        size = ctypes.sizeof(WAVEHDR)
        pos = 0
        gain = 0.0          # the fade, 0..1, times the volume
        fading_in = True
        ending_from: float | None = None  # the fade-out starts at this gain
        ended = 0.0         # seconds of fade-out done
        try:
            for hdr, buf in zip(headers, buffers):
                hdr.lpData = ctypes.cast(buf, ctypes.c_void_p)
                hdr.dwBufferLength = chunk * 2
                winmm.waveOutPrepareHeader(hwo, ctypes.byref(hdr), size)
                hdr.dwFlags |= WHDR_DONE  # free to fill
            finished = False
            while not finished:
                for hdr, buf in zip(headers, buffers):
                    if not hdr.dwFlags & WHDR_DONE:
                        continue
                    with self._lock:
                        volume, stopping = self._volume, self._stop_requested
                        fade_in, fade_out = self._fade_in_s, self._fade_out_s
                    piece, pos = take(samples, pos, chunk)
                    start = gain
                    if stopping:
                        if ending_from is None:
                            ending_from = gain
                        ended += CHUNK_S
                        gain = ending_from * max(0.0, 1.0 - ended / max(0.05, fade_out))
                    elif fading_in:
                        gain = min(volume, gain + volume * CHUNK_S / max(0.05, fade_in))
                        fading_in = gain < volume
                    else:
                        gain += max(-0.15, min(0.15, volume - gain))  # volume changes glide
                    data = scaled(piece, start, gain)
                    ctypes.memmove(buf, data, len(data))
                    hdr.dwFlags &= ~WHDR_DONE
                    winmm.waveOutWrite(hwo, ctypes.byref(hdr), size)
                    if stopping and gain <= 0.0:
                        finished = True
                        break
                kernel32.WaitForSingleObject(event, WAIT_MS)
            deadline = time.monotonic() + 2.0  # let the last buffers play out
            while any(not h.dwFlags & WHDR_DONE for h in headers) and time.monotonic() < deadline:
                kernel32.WaitForSingleObject(event, WAIT_MS)
        finally:
            winmm.waveOutReset(hwo)
            for hdr in headers:
                winmm.waveOutUnprepareHeader(hwo, ctypes.byref(hdr), size)
            winmm.waveOutClose(hwo)
            kernel32.CloseHandle(event)


class Recorder:
    """Records the default microphone on a background thread: start(), then stop() for the WAV bytes."""

    RATE = 16000
    MAX_S = 60.0

    def __init__(self):
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._chunks: list[bytes] = []
        self.level = 0.0  # loudness of the last bit of sound, 0..1, for a little meter
        self.error = ""
        self._opened = threading.Event()

    @property
    def recording(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        if self.recording:
            return True
        if not available():
            self.error = "Talking to Pomo needs Windows."
            return False
        self._chunks, self.level, self.error = [], 0.0, ""
        self._stop.clear()
        self._opened.clear()
        self._thread = threading.Thread(target=self._run, name="pomo-mic", daemon=True)
        self._thread.start()
        self._opened.wait(1.0)
        return not self.error

    def stop(self) -> bytes:
        """Stop and return what was heard as a WAV file (empty if nothing was recorded)."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        self._thread = None
        pcm = b"".join(self._chunks)
        return wav_bytes(pcm, self.RATE) if pcm else b""

    def _run(self) -> None:
        try:
            self._capture()
        except Exception as exc:
            self.error = str(exc) or "The microphone isn't available."
        finally:
            self._opened.set()

    def _capture(self) -> None:
        ctypes, wintypes, WAVEFORMATEX, WAVEHDR = _winmm()
        winmm, kernel32 = ctypes.windll.winmm, ctypes.windll.kernel32
        event = kernel32.CreateEventW(None, False, False, None)
        hwi = ctypes.c_void_p()
        fmt = _format(WAVEFORMATEX, self.RATE)
        if winmm.waveInOpen(ctypes.byref(hwi), WAVE_MAPPER, ctypes.byref(fmt), event, 0, CALLBACK_EVENT):
            kernel32.CloseHandle(event)
            raise OSError("Pomo couldn't open a microphone.")
        chunk = int(self.RATE * 0.1) * 2
        buffers = [ctypes.create_string_buffer(chunk) for _ in range(4)]
        headers = [WAVEHDR() for _ in range(4)]
        size = ctypes.sizeof(WAVEHDR)
        started = time.monotonic()
        try:
            for hdr, buf in zip(headers, buffers):
                hdr.lpData = ctypes.cast(buf, ctypes.c_void_p)
                hdr.dwBufferLength = chunk
                winmm.waveInPrepareHeader(hwi, ctypes.byref(hdr), size)
                winmm.waveInAddBuffer(hwi, ctypes.byref(hdr), size)
            winmm.waveInStart(hwi)
            self._opened.set()
            while not self._stop.is_set() and time.monotonic() - started < self.MAX_S:
                kernel32.WaitForSingleObject(event, WAIT_MS)
                for hdr, buf in zip(headers, buffers):
                    if hdr.dwFlags & WHDR_DONE and hdr.dwBytesRecorded:
                        data = ctypes.string_at(buf, hdr.dwBytesRecorded)
                        self._chunks.append(data)
                        self.level = loudness(data)
                        hdr.dwFlags &= ~WHDR_DONE
                        hdr.dwBytesRecorded = 0
                        winmm.waveInAddBuffer(hwi, ctypes.byref(hdr), size)
            winmm.waveInStop(hwi)
            winmm.waveInReset(hwi)  # hands back the buffers still waiting
            for hdr, buf in zip(headers, buffers):
                if hdr.dwBytesRecorded:
                    self._chunks.append(ctypes.string_at(buf, hdr.dwBytesRecorded))
        finally:
            for hdr in headers:
                winmm.waveInUnprepareHeader(hwi, ctypes.byref(hdr), size)
            winmm.waveInClose(hwi)
            kernel32.CloseHandle(event)


def loudness(pcm: bytes) -> float:
    """RMS level of 16-bit audio, scaled so speech lands around 0.3 to 0.8."""
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) // 2 * 2])
    if not samples:
        return 0.0
    rms = math.sqrt(sum(s * s for s in samples) / len(samples))
    return min(1.0, rms / 6000.0)


def wav_bytes(pcm: bytes, rate: int) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return out.getvalue()


def decode_file(path) -> tuple[array, int, int]:
    """Decode an audio file (MP3, AAC, WMA...) to 16-bit PCM with Windows' Media Foundation.

    Returns (interleaved samples, rate, channels). Raises OSError where Windows can't decode it
    (say, an N edition without the Media Feature Pack).
    """
    if sys.platform != "win32":
        raise OSError("decoding needs Windows")
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16), ("d", ctypes.c_ubyte * 8)]

    ole32, mfplat = ctypes.windll.ole32, ctypes.windll.mfplat
    mfreadwrite = ctypes.windll.mfreadwrite

    def guid(text: str) -> GUID:
        g = GUID()
        ole32.CLSIDFromString(text, ctypes.byref(g))
        return g

    def call(ptr, index: int, argtypes: list, *args) -> int:
        vtable = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtable[index])(ptr, *args)

    def check(hr: int, what: str) -> None:
        if hr < 0:
            raise OSError(f"{what} failed (0x{hr & 0xFFFFFFFF:08X})")

    major, subtype = guid("{48eba18e-f8c9-4687-bf11-0a74c9f96a8f}"), guid("{f7e34c9a-42e8-4714-b74b-cb29d72c35e5}")
    audio, pcm = guid("{73647561-0000-0010-8000-00AA00389B71}"), guid("{00000001-0000-0010-8000-00AA00389B71}")
    num_channels = guid("{37e48bf5-645e-4c5b-89de-ada9e29b696a}")
    rate_key = guid("{5faeeae7-0290-4c31-9e8a-c534f68d9dba}")
    bits_key = guid("{f2deb57f-40fa-4764-aa33-ed4f2d1ff669}")
    first_audio = 0xFFFFFFFD  # MF_SOURCE_READER_FIRST_AUDIO_STREAM
    vp = ctypes.c_void_p
    # vtable slots: IMFAttributes GetUINT32 7, SetGUID 24, SetUINT32 21; IMFSourceReader GetCurrentMediaType 6,
    # SetCurrentMediaType 7, ReadSample 9; IMFSample ConvertToContiguousBuffer 41; IMFMediaBuffer Lock 3, Unlock 4
    # COM may already be up on this thread in another mode (Qt's UI thread): then it's not ours to shut down
    com_ours = ole32.CoInitializeEx(None, 0) >= 0
    started = mfplat.MFStartup(0x00020070, 0) >= 0  # MF_VERSION, MFSTARTUP_FULL
    reader, wanted, current = vp(), vp(), vp()
    try:
        if not started:
            raise OSError("Media Foundation isn't available")
        check(mfreadwrite.MFCreateSourceReaderFromURL(ctypes.c_wchar_p(str(path)), None, ctypes.byref(reader)),
              "opening the file")
        check(mfplat.MFCreateMediaType(ctypes.byref(wanted)), "MFCreateMediaType")
        call(wanted, 24, [vp, vp], ctypes.byref(major), ctypes.byref(audio))
        call(wanted, 24, [vp, vp], ctypes.byref(subtype), ctypes.byref(pcm))
        call(wanted, 21, [vp, ctypes.c_uint32], ctypes.byref(bits_key), 16)
        check(call(reader, 7, [wintypes.DWORD, vp, vp], first_audio, None, wanted), "asking for PCM")
        check(call(reader, 6, [wintypes.DWORD, vp], first_audio, ctypes.byref(current)), "reading the format")
        channels, rate, bits = ctypes.c_uint32(), ctypes.c_uint32(), ctypes.c_uint32()
        call(current, 7, [vp, vp], ctypes.byref(num_channels), ctypes.byref(channels))
        call(current, 7, [vp, vp], ctypes.byref(rate_key), ctypes.byref(rate))
        call(current, 7, [vp, vp], ctypes.byref(bits_key), ctypes.byref(bits))
        if bits.value != 16 or not channels.value or not rate.value:
            raise OSError("an unexpected sample format")
        pcm_bytes = bytearray()
        while len(pcm_bytes) < 200_000_000:
            index, flags, stamp, sample = wintypes.DWORD(), wintypes.DWORD(), ctypes.c_longlong(), vp()
            check(call(reader, 9, [wintypes.DWORD, wintypes.DWORD, vp, vp, vp, vp], first_audio, 0,
                       ctypes.byref(index), ctypes.byref(flags), ctypes.byref(stamp), ctypes.byref(sample)),
                  "decoding")
            if sample.value:
                buffer = vp()
                try:
                    check(call(sample, 41, [vp], ctypes.byref(buffer)), "reading a sample")
                    data, length = vp(), wintypes.DWORD()
                    check(call(buffer, 3, [vp, vp, vp], ctypes.byref(data), None, ctypes.byref(length)), "Lock")
                    pcm_bytes += ctypes.string_at(data, length.value)
                    call(buffer, 4, [])
                finally:
                    if buffer.value:
                        call(buffer, 2, [])
                    call(sample, 2, [])
            if flags.value & 0x2:  # MF_SOURCE_READERF_ENDOFSTREAM
                break
    finally:
        for ptr in (current, wanted, reader):
            if ptr.value:
                call(ptr, 2, [])  # Release
        if started:
            mfplat.MFShutdown()
        if com_ours:
            ole32.CoUninitialize()
    samples = array("h")
    samples.frombytes(bytes(pcm_bytes[: len(pcm_bytes) // 2 * 2]))
    return samples, rate.value, channels.value


def read_wav(data: bytes) -> tuple[array, int]:
    """16-bit mono samples and the rate from WAV bytes."""
    with wave.open(io.BytesIO(data), "rb") as w:
        rate, channels, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        frames = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError("only 16-bit audio")
    samples = array("h")
    samples.frombytes(frames)
    if channels == 2:
        samples = array("h", [(samples[i] + samples[i + 1]) // 2 for i in range(0, len(samples) - 1, 2)])
    return samples, rate


def pcm_of(samples: array) -> bytes:
    return samples.tobytes() if sys.byteorder == "little" else struct.pack(f"<{len(samples)}h", *samples)
