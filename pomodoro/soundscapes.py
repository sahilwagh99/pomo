"""Pomo's focus sounds, made on this PC: rain, café murmur, brown noise and a soft lo-fi loop.

Like the alert sounds (sounds.py), nothing is downloaded or shipped: each sound is synthesized
the first time it's played (a few seconds, on a background thread) and kept in Pomo's cache
folder. Every sound is a 24-second loop that joins seamlessly: noise beds are crossfaded into
their own start, and every drop, clink and note that rings past the end wraps around to the
beginning. The lo-fi loop is exactly eight bars, so its beat carries on across the join.
"""
from __future__ import annotations

import math
import random
import wave
from array import array
from pathlib import Path

RATE = 22050
LOOP_S = 24.0             # eight bars at 80 bpm
N = int(RATE * LOOP_S)
XFADE = int(RATE * 1.5)   # noise beds blend into their own start over this long
VERSION = 2               # bump when a sound changes, so caches are rebuilt
TARGET_RMS = {"rain": 0.15, "cafe": 0.14, "brown": 0.17, "lofi": 0.14}
TWO_PI = 2 * math.pi


# ------------------------------------------------------------------ helpers
def _loop_noise(gen, rng: random.Random) -> list[float]:
    """N samples of a noise process that ends where it began (crossfaded, equal power)."""
    raw = gen(rng, N + XFADE)
    out = raw[:N]
    for i in range(XFADE):
        t = i / XFADE
        out[i] = raw[i] * math.sin(t * math.pi / 2) + raw[N + i] * math.cos(t * math.pi / 2)
    return out


def _lowpass(x: list[float], cutoff: float, loop: bool = True) -> list[float]:
    """One-pole lowpass. For a loop, the filter starts warmed up on the loop's own end, so the join stays smooth."""
    a = 1.0 - math.exp(-TWO_PI * cutoff / RATE)
    y = 0.0
    if loop:
        for v in x[-4096:]:
            y += a * (v - y)
    out = [0.0] * len(x)
    for i, v in enumerate(x):
        y += a * (v - y)
        out[i] = y
    return out


def _highpass(x: list[float], cutoff: float, loop: bool = True) -> list[float]:
    low = _lowpass(x, cutoff, loop)
    return [v - lo for v, lo in zip(x, low)]


def _place(mix: list[float], start: int, template: list[float], gain: float) -> None:
    """Add a sound into the loop at a position, wrapping past the end back to the start."""
    n = len(mix)
    start %= n
    end = start + len(template)
    if end <= n:
        for k, v in enumerate(template):
            mix[start + k] += v * gain
    else:
        for k, v in enumerate(template):
            mix[(start + k) % n] += v * gain


def _decaying(freqs: list[tuple[float, float]], seconds: float, tau: float, rng: random.Random,
              attack: float = 0.002, noise: float = 0.0) -> list[float]:
    """A struck sound: partials (frequency, weight) under one exponential decay, a pinch of noise on top."""
    n = int(seconds * RATE)
    out = [0.0] * n
    for i in range(n):
        t = i / RATE
        env = min(1.0, t / attack) * math.exp(-t / tau)
        s = 0.0
        for f, w in freqs:
            s += w * math.sin(TWO_PI * f * t)
        if noise:
            s += noise * (rng.random() * 2 - 1)
        out[i] = s * env
    return out


def _finish(name: str, mix: list[float]) -> array:
    """Level to a gentle loudness (no peak above -1 dB), as 16-bit samples."""
    peak = max((abs(v) for v in mix), default=0.0) or 1.0
    rms = math.sqrt(sum(v * v for v in mix[::7]) / max(1, len(mix[::7]))) or 1.0
    gain = min(TARGET_RMS[name] / rms, 0.89 / peak)
    return array("h", [int(max(-1.0, min(1.0, v * gain)) * 32767) for v in mix])


def _white(rng: random.Random, n: int) -> list[float]:
    r = rng.random
    return [r() * 2 - 1 for _ in range(n)]


def _brown_noise(rng: random.Random, n: int) -> list[float]:
    r = rng.random
    out, b = [0.0] * n, 0.0
    for i in range(n):
        b = b * 0.9965 + (r() - 0.5) * 0.08  # leaky: no slow drift
        out[i] = b
    return out


def _pink_noise(rng: random.Random, n: int) -> list[float]:
    """Paul Kellet's economy pink filter."""
    r = rng.random
    out = [0.0] * n
    b0 = b1 = b2 = 0.0
    for i in range(n):
        w = r() * 2 - 1
        b0 = 0.99765 * b0 + w * 0.0990460
        b1 = 0.96300 * b1 + w * 0.2965164
        b2 = 0.57000 * b2 + w * 1.0526913
        out[i] = b0 + b1 + b2 + w * 0.1848
    return out


# ------------------------------------------------------------------ sounds
def brown(rng: random.Random) -> list[float]:
    """Deep, even brown noise, softened a little more at the top."""
    return _lowpass(_loop_noise(_brown_noise, rng), 900.0)


def rain(rng: random.Random) -> list[float]:
    """Steady rain: a hiss that swells in slow gusts, thousands of small drops, a few heavy ones."""
    bed = _lowpass(_highpass(_loop_noise(_pink_noise, rng), 350.0), 6500.0)
    mix = [0.0] * N
    for i, v in enumerate(bed):
        t = i / RATE  # gusts repeat a whole number of times per loop, so they line up at the join
        mix[i] = v * 0.55 * (1.0 + 0.22 * math.sin(TWO_PI * 2 * t / LOOP_S) + 0.12 * math.sin(TWO_PI * 5 * t / LOOP_S + 1.3))
    drops = [_decaying([(rng.uniform(1800, 5200), 1.0), (rng.uniform(5200, 8000), 0.3)], 0.03,
                       rng.uniform(0.004, 0.009), rng, attack=0.0005, noise=0.35) for _ in range(28)]
    for _ in range(int(LOOP_S * 70)):
        _place(mix, rng.randrange(N), rng.choice(drops), rng.uniform(0.02, 0.11))
    heavy = [_decaying([(rng.uniform(450, 1100), 1.0), (rng.uniform(1500, 2600), 0.35)], 0.09,
                       rng.uniform(0.018, 0.032), rng, attack=0.001, noise=0.2) for _ in range(10)]
    for _ in range(int(LOOP_S * 2.2)):
        _place(mix, rng.randrange(N), rng.choice(heavy), rng.uniform(0.05, 0.16))
    return mix


def cafe(rng: random.Random) -> list[float]:
    """A café: low room tone, indistinct chatter at a few tables, the odd cup and spoon."""
    room = _lowpass(_loop_noise(_brown_noise, rng), 260.0)
    mix = [v * 0.5 for v in room]
    control = 200  # envelope points per second
    steps = int(LOOP_S * control)
    for low, high, level in ((140.0, 650.0, 0.9), (240.0, 1150.0, 0.75), (380.0, 1900.0, 0.5)):
        voice = _lowpass(_highpass(_loop_noise(lambda r, n: _white(r, n), rng), low), high)
        envelope = [0.0] * steps
        for _talker in range(3):  # each table talks in phrases, with pauses
            t = rng.uniform(0, LOOP_S)
            for _phrase in range(14):
                phrase = rng.uniform(0.9, 3.2)
                syllable_rate = rng.uniform(3.5, 6.0)
                loud = rng.uniform(0.4, 1.0)
                for k in range(int(phrase * control)):
                    s = k / control
                    syllable = max(0.0, math.sin(math.pi * ((s * syllable_rate) % 1.0))) ** 1.6
                    swell = math.sin(math.pi * min(1.0, s / phrase))
                    envelope[int((t + s) * control) % steps] += loud * syllable * (0.35 + 0.65 * swell)
                t += phrase + rng.uniform(0.4, 2.2)
        for i in range(N):
            x = i * control / RATE
            k = int(x)
            e = envelope[k % steps] + (envelope[(k + 1) % steps] - envelope[k % steps]) * (x - k)
            mix[i] += voice[i] * level * (0.18 + e)
    clinks = [_decaying([(f, 1.0), (f * 2.76, 0.5), (f * 5.4, 0.22)], 0.5, rng.uniform(0.08, 0.18), rng, attack=0.0008)
              for f in (rng.uniform(1700, 3100) for _ in range(8))]
    for _ in range(int(LOOP_S / 2.6)):
        _place(mix, rng.randrange(N), rng.choice(clinks), rng.uniform(0.04, 0.1))
    return mix


# the lo-fi loop: 80 bpm, eight bars, Dm9 - G9 - Cmaj9 - Am9 (two bars each)
BPM = 80
BEAT = 60.0 / BPM
CHORDS = ((38, (53, 57, 60, 64)), (43, (53, 57, 59, 64)), (36, (52, 55, 59, 62)), (45, (48, 52, 55, 59)))


def _hz(midi: int) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def _electric_piano(notes: tuple[int, ...], seconds: float) -> list[float]:
    """A soft electric-piano chord, from one shared single-cycle wavetable."""
    size = 2048
    table = [math.sin(TWO_PI * k / size) + 0.32 * math.sin(2 * TWO_PI * k / size)
             + 0.1 * math.sin(3 * TWO_PI * k / size) + 0.04 * math.sin(4 * TWO_PI * k / size) for k in range(size)]
    n = int(seconds * RATE)
    out = [0.0] * n
    for note in notes:
        step = _hz(note) * size / RATE
        detune = step * 1.0016  # a second, slightly sharp voice: a gentle chorus
        phase_a = phase_b = 0.0
        for i in range(n):
            t = i / RATE
            env = min(1.0, t / 0.012) * math.exp(-t / 1.1)
            out[i] += (table[int(phase_a) % size] + table[int(phase_b) % size]) * 0.5 * env
            phase_a += step
            phase_b += detune
    return out


def lofi(rng: random.Random) -> list[float]:
    music = [0.0] * N
    bar = 4 * BEAT
    for c, (root, notes) in enumerate(CHORDS):
        chord = _electric_piano(notes, 2.4)
        bass = _decaying([(_hz(root), 1.0), (_hz(root) * 2, 0.18)], 1.6, 0.7, rng, attack=0.01)
        for b in range(2):
            start = (c * 2 + b) * bar
            _place(music, int(start * RATE), chord, 0.22)
            _place(music, int((start + 2.5 * BEAT) * RATE), chord, 0.12)
            _place(music, int(start * RATE), bass, 0.5)
            _place(music, int((start + 2.5 * BEAT) * RATE), bass, 0.32)
    kick_n = int(0.35 * RATE)
    kick, phase = [0.0] * kick_n, 0.0
    for i in range(kick_n):
        t = i / RATE
        phase += TWO_PI * (45 + 70 * math.exp(-t / 0.04)) / RATE
        kick[i] = math.sin(phase) * math.exp(-t / 0.11)
    snare_noise = _lowpass(_highpass(_white(rng, int(0.2 * RATE)), 900, loop=False), 4500, loop=False)
    snare = [v + b for v, b in zip(snare_noise, _decaying([(190.0, 1.0)], 0.2, 0.05, rng))]
    snare = [v * math.exp(-(i / RATE) / 0.07) for i, v in enumerate(snare)]
    hat_raw = _highpass(_white(rng, int(0.05 * RATE)), 6000, loop=False)
    hat = [v * math.exp(-(i / RATE) / 0.012) for i, v in enumerate(hat_raw)]
    swing = 0.62  # the off-beat eighth sits a little late
    for bar_index in range(8):
        start = bar_index * bar
        for beat in range(4):
            at = start + beat * BEAT
            if beat in (0, 2):
                _place(music, int(at * RATE), kick, 0.55)
            else:
                _place(music, int(at * RATE), snare, 0.22)
            _place(music, int(at * RATE), hat, 0.07 + rng.uniform(0, 0.02))
            _place(music, int((at + swing * BEAT) * RATE), hat, 0.045 + rng.uniform(0, 0.02))
        if bar_index % 2:  # a lazy extra kick before the next bar
            _place(music, int((start + 3.62 * BEAT) * RATE), kick, 0.3)
    warm = _lowpass(music, 3000.0)
    tremolo = [v * (1.0 + 0.08 * math.sin(TWO_PI * 16 * i / N)) for i, v in enumerate(warm)]
    hiss = _lowpass(_loop_noise(lambda r, n: _white(r, n), rng), 4500.0)
    mix = [math.tanh(v * 1.4) + h * 0.012 for v, h in zip(tremolo, hiss)]
    crackle = [_decaying([(rng.uniform(1500, 4000), 1.0)], 0.006, 0.0015, rng, attack=0.0002, noise=0.8)
               for _ in range(12)]
    for _ in range(int(LOOP_S * 9)):
        _place(mix, rng.randrange(N), rng.choice(crackle), rng.uniform(0.01, 0.06))
    return mix


SOUNDS = {"rain": rain, "cafe": cafe, "brown": brown, "lofi": lofi}
SEEDS = {"rain": 11, "cafe": 23, "brown": 5, "lofi": 42}


def render(name: str) -> array:
    """The named focus sound as 16-bit mono samples at RATE (takes a few seconds)."""
    return _finish(name, SOUNDS[name](random.Random(SEEDS[name])))


def cache_path(cache_dir: Path, name: str) -> Path:
    return cache_dir / f"focus_{name}_v{VERSION}.wav"


def load(cache_dir: Path, name: str) -> array:
    """The sound from the cache, made and saved first if needed."""
    path = cache_path(cache_dir, name)
    try:
        with wave.open(str(path), "rb") as w:
            if w.getframerate() == RATE and w.getnchannels() == 1 and w.getsampwidth() == 2:
                samples = array("h")
                samples.frombytes(w.readframes(w.getnframes()))
                if len(samples) == N:
                    return samples
    except (OSError, EOFError, wave.Error):
        pass
    samples = render(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(samples.tobytes())
    tmp.replace(path)
    return samples
