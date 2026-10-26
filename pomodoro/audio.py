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


def _format(WAVEFORMATEX, rate: int):
    return WAVEFORMATEX(1, 1, rate, rate * 2, 2, 16, 0)  # PCM, mono, 16-bit


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
        self._volume = 0.5
        self._stop_requested = False
        self._fade_in_s = 1.5
        self._fade_out_s = 1.2
        self.error = ""

    @property
    def playing(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop_requested

    def play(self, samples: array, rate: int, volume: float, fade_in_s: float = 1.5) -> None:
        """Start looping (or switch to) a sound."""
        self.stop(fade_out_s=0.25, wait=True)
        with self._lock:
            self._samples, self._rate = samples, rate
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
            samples, rate = self._samples, self._rate
        event = kernel32.CreateEventW(None, False, False, None)
        hwo = ctypes.c_void_p()
        fmt = _format(WAVEFORMATEX, rate)
        if winmm.waveOutOpen(ctypes.byref(hwo), WAVE_MAPPER, ctypes.byref(fmt), event, 0, CALLBACK_EVENT):
            kernel32.CloseHandle(event)
            raise OSError("no sound device")
        chunk = max(256, int(rate * CHUNK_S))
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
