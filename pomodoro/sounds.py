"""Alert sounds, synthesized at runtime so the app ships without audio files."""
from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

RATE = 22050


def _note(buf: list[float], start: float, freq: float, dur: float, decay: float,
          partials=((1.0, 1.0),), attack: float = 0.004, amp: float = 1.0) -> None:
    i0 = int(start * RATE)
    n = int(dur * RATE)
    if i0 + n > len(buf):
        buf.extend([0.0] * (i0 + n - len(buf)))
    two_pi = 2 * math.pi
    for i in range(n):
        t = i / RATE
        env = min(1.0, t / attack) * math.exp(-t / decay)
        s = 0.0
        for ratio, weight in partials:
            # higher partials die out faster, like a struck object
            s += weight * math.sin(two_pi * freq * ratio * t) * math.exp(-t * (ratio - 1) * 1.5)
        buf[i0 + i] += amp * env * s


def _chime() -> list[float]:
    buf: list[float] = []
    for k, f in enumerate((1046.5, 1318.5, 1568.0)):
        _note(buf, k * 0.16, f, 1.1, 0.32, ((1, 1.0), (2, 0.22)))
    return buf


def _bell() -> list[float]:
    buf: list[float] = []
    _note(buf, 0, 659.25, 2.2, 0.75, ((1, 1.0), (2.0, 0.45), (2.76, 0.32), (5.4, 0.12), (8.93, 0.05)))
    return buf


def _digital() -> list[float]:
    buf: list[float] = []
    soft_square = ((1, 1.0), (3, 0.28), (5, 0.12))
    t = 0.0
    for _ in range(3):
        for _ in range(2):
            _note(buf, t, 1760.0, 0.09, 1.0, soft_square, attack=0.003, amp=0.6)
            t += 0.15
        t += 0.22
    return buf


def _soft() -> list[float]:
    buf: list[float] = []
    _note(buf, 0.0, 880.0, 0.9, 0.38, ((1, 1.0), (3, 0.08)), attack=0.05)
    _note(buf, 0.28, 659.25, 1.1, 0.45, ((1, 1.0), (3, 0.08)), attack=0.05)
    return buf


_GENERATORS = {"Chime": _chime, "Bell": _bell, "Digital": _digital, "Soft": _soft}


def render_wav(name: str, volume: int, path: Path) -> float:
    """Write the named sound at the given volume (0-100). Returns its length in seconds."""
    samples = _GENERATORS.get(name, _chime)()
    fade = int(0.02 * RATE)
    for i in range(min(fade, len(samples))):
        samples[-1 - i] *= i / fade
    peak = max((abs(s) for s in samples), default=1.0) or 1.0
    gain = 0.9 * max(0, min(100, volume)) / 100 / peak
    frames = b"".join(struct.pack("<h", int(max(-1.0, min(1.0, s * gain)) * 32767)) for s in samples)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(frames)
    return len(samples) / RATE
