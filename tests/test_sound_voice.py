"""Focus sounds, the audio helpers, Talk to Pomo, and the taskbar button's preview."""
import base64
import json
import tempfile
import unittest
from array import array
from pathlib import Path
from types import SimpleNamespace

import httpx2

from pomodoro import audio, soundscapes, voice
from pomodoro.config import Settings
from pomodoro.engine import TimerEngine
from pomodoro.theme import theme_for


class SoundscapeTests(unittest.TestCase):
    def test_a_loop_that_joins_smoothly_and_is_cached(self):
        with tempfile.TemporaryDirectory() as folder:
            sound = soundscapes.load(Path(folder), "brown")
            samples = sound.samples
            self.assertEqual((len(samples), sound.rate, sound.channels), (soundscapes.N, soundscapes.RATE, 1))
            self.assertTrue(soundscapes.cache_path(Path(folder), "brown").exists())
            steps = sorted(abs(samples[i + 1] - samples[i]) for i in range(0, len(samples) - 1, 7))
            join = abs(samples[0] - samples[-1])
            self.assertLess(join, steps[int(len(steps) * 0.995)] * 1.5)  # no click where the loop restarts
            self.assertLess(max(abs(v) for v in samples), 32767 * 0.9)  # headroom
            self.assertEqual(soundscapes.load(Path(folder), "brown"), sound)  # read back from the cache

    def test_the_recordings(self):
        import sys

        if sys.platform != "win32":
            self.skipTest("decoding MP3 uses Windows' Media Foundation")
        with tempfile.TemporaryDirectory() as folder:
            for name in soundscapes.RECORDINGS:
                with self.subTest(sound=name):
                    sound = soundscapes.load(Path(folder), name)
                    a, ch = sound.samples, sound.channels
                    self.assertEqual((sound.rate, ch), (44100, 2))  # the recording's own quality, in stereo
                    self.assertGreater(len(a) / ch / sound.rate, 15)
                    for c in range(ch):  # the end runs into the start without a click
                        steps = sorted(abs(a[i + ch] - a[i]) for i in range(c, len(a) - ch, 13 * ch))
                        self.assertLess(abs(a[c] - a[len(a) - ch + c]), steps[int(len(steps) * 0.999)])
                    rms = (sum(v * v for v in a[::9]) / len(a[::9])) ** 0.5 / 32767
                    self.assertAlmostEqual(rms, soundscapes.TARGET_RMS[name], delta=0.01)  # as loud as the others
                    cached = soundscapes.cache_path(Path(folder), name, soundscapes.SOUND_DIR / soundscapes.RECORDINGS[name])
                    self.assertTrue(cached.exists())
                    self.assertEqual(soundscapes.load(Path(folder), name), sound)

    def test_without_the_recording_it_makes_one(self):
        with tempfile.TemporaryDirectory() as folder:
            sound = soundscapes.load(Path(folder), "rain", folder=Path(folder) / "nothing-here")
            self.assertEqual((sound.rate, sound.channels, len(sound.samples)), (soundscapes.RATE, 1, soundscapes.N))

    def test_fades_are_trimmed_and_loops_crossfaded(self):
        rate = 1000
        steady = array("h", [1000, -1000] * (rate * 5))  # 10 s of stereo "sound"
        faded = array("h", [0, 0] * (rate // 2)) + steady + array("h", [10, -10] * (rate // 2))
        trimmed = soundscapes.trim_fades(faded, rate, 2)
        self.assertEqual(len(trimmed), len(steady))
        ramp = array("h", [i % 2000 for i in range(rate * 8)])  # mono, 8 s
        loop = soundscapes.make_loop(ramp, rate, 1, fade_s=1.0)
        self.assertEqual(len(loop), rate * 7)  # a second shorter: it was blended into the start
        self.assertEqual(loop[0], ramp[rate * 7])  # the loop starts where its end left off
        self.assertEqual(loop[-1], ramp[rate * 7 - 1])

    def test_levelling_rounds_off_peaks(self):
        quiet = array("h", [300, -300] * 1000 + [9000])
        out = soundscapes.level(quiet, 0.15)
        self.assertLessEqual(max(abs(v) for v in out), 32767)
        self.assertGreater(abs(out[0]), 300)

    def test_events_wrap_around_the_loop(self):
        mix = [0.0] * 10
        soundscapes._place(mix, 8, [1.0, 2.0, 3.0, 4.0], 1.0)
        self.assertEqual(mix, [3.0, 4.0, 0, 0, 0, 0, 0, 0, 1.0, 2.0])


class AudioHelperTests(unittest.TestCase):
    def test_taking_samples_round_the_loop(self):
        piece, pos = audio.take(array("h", [1, 2, 3, 4, 5]), 3, 7)
        self.assertEqual((list(piece), pos), ([4, 5, 1, 2, 3, 4, 5], 0))

    def test_gain_ramps(self):
        out = array("h")
        out.frombytes(audio.scaled(array("h", [1000] * 4), 0.0, 1.0))
        self.assertEqual(list(out), [0, 250, 500, 750])

    def test_wav_round_trip_and_loudness(self):
        pcm = array("h", [0, 8000, -8000, 4000] * 400).tobytes()
        samples, rate = audio.read_wav(audio.wav_bytes(pcm, 16000))
        self.assertEqual((rate, samples.tobytes()), (16000, pcm))
        self.assertGreater(audio.loudness(pcm), 0.5)
        self.assertEqual(audio.loudness(bytes(400)), 0.0)


class VoiceTests(unittest.TestCase):
    def test_replies_read_as_sentences(self):
        text = voice.speakable("# Plan\n1. **Outline** chapter 3\n2. Read [the paper](http://x.y)\n\n`code` done")
        self.assertEqual(text, "Plan. Outline chapter 3. Read the paper. code done")

    def test_who_listens(self):
        s = Settings()
        has = {"openai": True, "gemini": False}
        key = lambda k: has.get(k, False)
        self.assertEqual(voice.engine_for(s, key), "windows")  # Claude can't listen: this PC does
        s.ai_provider = "openai"
        self.assertEqual(voice.engine_for(s, key), "openai")
        s.ai_provider = "gemini"
        self.assertEqual(voice.engine_for(s, key), "windows")  # no Gemini key
        s.voice_input = "gemini"
        self.assertEqual(voice.engine_for(s, key), "gemini")  # your pick wins

    def test_openai_transcription_falls_back_to_whisper(self):
        seen = []

        def handler(request):
            body = request.read()
            seen.append(b"gpt-4o-mini-transcribe" in body and "new" or "whisper")
            if b"gpt-4o-mini-transcribe" in body:
                return httpx2.Response(404, json={"error": {"message": "no such model"}})
            return httpx2.Response(200, json={"text": " Plan my afternoon. "})

        text = voice.transcribe_openai(b"RIFF....", "sk-test", transport=httpx2.MockTransport(handler))
        self.assertEqual((text, seen), ("Plan my afternoon.", ["new", "whisper"]))
        bad = httpx2.MockTransport(lambda request: httpx2.Response(401, json={}))
        with self.assertRaises(RuntimeError):
            voice.transcribe_openai(b"x", "sk-bad", transport=bad)

    def test_gemini_transcription(self):
        def handler(request):
            body = json.loads(request.content)
            audio_part = body["messages"][0]["content"][1]
            self.assertEqual(audio_part["input_audio"]["format"], "wav")
            self.assertEqual(base64.b64decode(audio_part["input_audio"]["data"]), b"WAVDATA")
            return httpx2.Response(200, json={"choices": [{"message": {"content": "Start a focus session"}}]})

        self.assertEqual(voice.transcribe_gemini(b"WAVDATA", "AIza", "gemini-3.5-flash",
                                                 transport=httpx2.MockTransport(handler)), "Start a focus session")


class TaskbarTests(unittest.TestCase):
    def test_the_hover_preview(self):
        from pomodoro.taskbar import TaskbarButton

        settings = Settings()
        engine = TimerEngine(settings)
        engine.start()
        ctrl = SimpleNamespace(engine=engine, settings=settings, theme=theme_for("light"),
                               icon=__import__("PySide6.QtGui", fromlist=["QIcon"]).QIcon(),
                               current_outfit=lambda: "headband", agent_signal=lambda: None,
                               mode_label=lambda: engine.mode.label)
        button = TaskbarButton(ctrl)
        image = button.thumbnail(200, 110)
        self.assertEqual((image.width(), image.height()), (200, 110))
        colors = {image.pixel(x, y) for x in range(0, 200, 4) for y in range(0, 110, 4)}
        self.assertGreater(len(colors), 8)  # the ring, Pomo and the time, not just the page
        engine.pause()
        button.refresh(force=True)  # not shown: nothing to do, and nothing breaks


if __name__ == "__main__":
    unittest.main()
