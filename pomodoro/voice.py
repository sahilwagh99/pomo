"""Talk to Pomo: push-to-talk questions, and an optional soft voice reading the replies.

Hold the talk shortcut (or the mic button in Ask Pomo) and speak; let go and Pomo asks your
question for you. What you said becomes text one of three ways:
  this PC   Windows' own speech recognition, offline: your voice never leaves the PC
  OpenAI    OpenAI's transcription, with your OpenAI key (more accurate)
  Gemini    a Gemini model, with your Gemini key
"Automatic" uses the provider Ask Pomo already talks to when it can listen (OpenAI or Gemini),
and this PC otherwise. Replies are read with a Windows voice, on this PC.

Windows' speech engines are reached through Windows PowerShell's System.Speech, so Pomo needs
no extra packages; each job is a short-lived, windowless PowerShell process.
"""
from __future__ import annotations

import base64
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QRect, QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .audio import Recorder, loudness
from .providers import model_for, provider

CREATE_NO_WINDOW = 0x08000000
OPENAI_MODELS = ("gpt-4o-mini-transcribe", "whisper-1")
ENGINE_LABELS = {"windows": "this PC", "openai": "OpenAI", "gemini": "Gemini"}
MIN_SPEECH_S = 0.4
SILENT_LEVEL = 0.015
SPEECH_LEVEL = 0.08      # louder than this counts as talking
QUIET_END_S = 1.6        # click-to-talk ends after this long quiet, once you've said something


def _powershell(script: str, timeout: float) -> str:
    """Run a Windows PowerShell script without a window; its output as text."""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
        capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    return result.stdout.decode("utf-8", errors="replace")


def _quote(text: str) -> str:
    """A PowerShell single-quoted string."""
    return "'" + str(text).replace("'", "''") + "'"


UTF8_OUT = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n"


def engine_for(settings, has_key) -> str:
    """Which engine turns speech into text: 'windows', 'openai' or 'gemini'."""
    choice = settings.voice_input
    if choice in ("openai", "gemini", "windows"):
        return choice
    if settings.ai_provider in ("openai", "gemini") and has_key(settings.ai_provider):
        return settings.ai_provider
    return "windows"


# ------------------------------------------------------------- speech to text
def transcribe_windows(wav_path: Path) -> str:
    script = UTF8_OUT + f"""
Add-Type -AssemblyName System.Speech
$all = [System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers()
$ui = [System.Globalization.CultureInfo]::CurrentUICulture
$info = $all | Where-Object {{ $_.Culture.Name -eq $ui.Name }} | Select-Object -First 1
if (-not $info) {{ $info = $all | Where-Object {{ $_.Culture.TwoLetterISOLanguageName -eq $ui.TwoLetterISOLanguageName }} | Select-Object -First 1 }}
if (-not $info) {{ $info = $all | Select-Object -First 1 }}
if (-not $info) {{ 'NONE'; exit }}
$engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine($info)
$engine.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar))
$engine.SetInputToWaveFile({_quote(wav_path)})
$parts = @()
while ($true) {{ try {{ $r = $engine.Recognize() }} catch {{ break }}; if ($r -eq $null) {{ break }}; $parts += $r.Text }}
$engine.Dispose()
'TEXT:' + ($parts -join ' ')
"""
    out = _powershell(script, timeout=60)
    if "NONE" in out.splitlines():
        raise RuntimeError("Windows speech recognition isn't installed for your language. "
                           "Pick OpenAI or Gemini for Talk to Pomo in Settings > AI.")
    for line in out.splitlines():
        if line.startswith("TEXT:"):
            return line[5:].strip()
    raise RuntimeError("Windows couldn't make out what you said.")


def transcribe_openai(wav: bytes, api_key: str, transport=None) -> str:
    import httpx2

    last = ""
    with httpx2.Client(timeout=httpx2.Timeout(60.0, connect=15.0), transport=transport) as client:
        for model in OPENAI_MODELS:
            resp = client.post(f"{provider('openai').base_url}/audio/transcriptions",
                               headers={"Authorization": f"Bearer {api_key}"},
                               files={"file": ("speech.wav", wav, "audio/wav")}, data={"model": model})
            if resp.status_code in (401, 403):
                raise RuntimeError("OpenAI didn't accept your key. Check it in Settings > AI.")
            if resp.status_code in (400, 404) and model != OPENAI_MODELS[-1]:
                last = resp.text
                continue  # this model isn't open to the key: try the classic one
            if resp.status_code >= 400:
                raise RuntimeError(f"OpenAI couldn't transcribe that (error {resp.status_code}).")
            return str(resp.json().get("text") or "").strip()
    raise RuntimeError(f"OpenAI couldn't transcribe that. {last[:120]}")


def transcribe_gemini(wav: bytes, api_key: str, model: str, transport=None) -> str:
    import httpx2

    body = {"model": model, "messages": [{"role": "user", "content": [
        {"type": "text", "text": "Transcribe this recording exactly, in its own language. "
                                 "Reply with only the words that were said."},
        {"type": "input_audio", "input_audio": {"data": base64.b64encode(wav).decode("ascii"), "format": "wav"}},
    ]}]}
    with httpx2.Client(timeout=httpx2.Timeout(60.0, connect=15.0), transport=transport) as client:
        resp = client.post(f"{provider('gemini').base_url}/chat/completions", json=body,
                           headers={"Authorization": f"Bearer {api_key}"})
    if resp.status_code in (401, 403):
        raise RuntimeError("Gemini didn't accept your key. Check it in Settings > AI.")
    if resp.status_code >= 400:
        raise RuntimeError(f"Gemini couldn't transcribe that (error {resp.status_code}).")
    choices = resp.json().get("choices") or [{}]
    return str((choices[0].get("message") or {}).get("content") or "").strip()


class TranscribeWorker(QThread):
    done = Signal(str)     # what you said
    failed = Signal(str)   # a friendly reason

    def __init__(self, ctrl, wav: bytes, engine: str, parent=None):
        super().__init__(parent)
        self.wav = wav
        self.engine = engine
        self.cache = ctrl.cache_dir
        self.key = (ctrl.ai_key(engine) or ctrl.env_ai_key(engine)) if engine != "windows" else None
        self.model = model_for(ctrl.settings, "gemini")

    def run(self) -> None:
        try:
            if self.engine == "windows":
                path = self.cache / "talk.wav"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(self.wav)
                try:
                    text = transcribe_windows(path)
                finally:
                    path.unlink(missing_ok=True)
            elif not self.key:
                raise RuntimeError(f"No {ENGINE_LABELS[self.engine]} key yet. Add one in Settings > AI, "
                                   "or let this PC listen.")
            elif self.engine == "openai":
                text = transcribe_openai(self.wav, self.key)
            else:
                text = transcribe_gemini(self.wav, self.key, self.model)
        except subprocess.TimeoutExpired:
            self.failed.emit("Windows took too long to listen. Try again?")
            return
        except RuntimeError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # offline, a strange answer...
            self.failed.emit(f"Pomo couldn't make that out ({type(exc).__name__}).")
            return
        if text:
            self.done.emit(text)
        else:
            self.failed.emit("Pomo didn't catch any words. Try again a little closer to the mic?")


# ------------------------------------------------------------- text to speech
def speakable(markdown: str) -> str:
    """A reply as plain sentences: no markdown marks, links read as their words, lists as pauses."""
    text = re.sub(r"```.*?```", " ", markdown, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^\s{0,3}(#+|[-*+]|\d+[.)])\s+", "", text, flags=re.M)
    text = re.sub(r"[*_~>#|]", "", text)
    return re.sub(r"\s*\n\s*", ". ", text.strip()).replace("..", ".")[:4000]


def list_voices() -> list[tuple[str, str]]:
    """Installed Windows voices: (name, 'Female · en-US')."""
    if sys.platform != "win32":
        return []
    script = UTF8_OUT + """
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.GetInstalledVoices() | Where-Object Enabled | ForEach-Object { 'VOICE:' + $_.VoiceInfo.Name + '|' + $_.VoiceInfo.Gender + '|' + $_.VoiceInfo.Culture.Name }
"""
    try:
        out = _powershell(script, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return []
    voices = []
    for line in out.splitlines():
        if line.startswith("VOICE:"):
            name, gender, culture = (line[6:].split("|") + ["", ""])[:3]
            voices.append((name.strip(), f"{gender.strip()} · {culture.strip()}"))
    return voices


class Speaker(QObject):
    """Reads text aloud with a Windows voice, one reply at a time (a new one cuts the last one off)."""

    started = Signal()
    finished = Signal()

    def __init__(self, cache_dir: Path, parent: QObject | None = None):
        super().__init__(parent)
        self.cache_dir = cache_dir
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._token = 0

    @property
    def speaking(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def speak(self, text: str, voice: str = "", volume: int = 80) -> None:
        text = speakable(text)
        self.stop()
        if not text or sys.platform != "win32":
            return
        self._token += 1
        token = self._token
        path = self.cache_dir / f"speak_{token}.txt"  # its own file: an older one's cleanup can't touch it
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        chosen = f"try {{ $s.SelectVoice({_quote(voice)}) }} catch {{ }}" if voice else (
            "$soft = $s.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Gender -eq 'Female' } "
            "| Select-Object -First 1\nif ($soft) { $s.SelectVoice($soft.VoiceInfo.Name) }")
        script = f"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
{chosen}
$s.Rate = -1
$s.Volume = {max(0, min(100, int(volume)))}
$s.Speak([IO.File]::ReadAllText({_quote(path)}, [Text.Encoding]::UTF8))
"""
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        try:
            proc = subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-EncodedCommand", encoded],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
        except OSError:
            return
        with self._lock:
            self._proc = proc
        self.started.emit()
        threading.Thread(target=self._wait, args=(proc, token, path), daemon=True).start()

    def _wait(self, proc: subprocess.Popen, token: int, path: Path) -> None:
        proc.wait()
        try:
            path.unlink(missing_ok=True)  # Pomo's words don't stay on disk
        except OSError:
            pass
        if token == self._token:
            self.finished.emit()  # queued to the UI thread

    def stop(self) -> None:
        with self._lock:
            proc, self._proc = self._proc, None
            self._token += 1  # its waiting thread stays quiet: this is the one "finished"
        if proc is not None and proc.poll() is None:
            proc.kill()
            self.finished.emit()


# ------------------------------------------------------------- the listen card
class ListenCard(QWidget):
    """A little card by the timer while Pomo listens: a level meter, then 'Thinking…'."""

    SHADOW, WIDTH, GAP = 14, 300, 10

    def __init__(self, ctrl):
        super().__init__(None)
        self.ctrl = ctrl
        self.level = 0.0
        self.mode = "listening"  # listening | thinking | error
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedWidth(self.WIDTH + 2 * self.SHADOW)
        lay = QVBoxLayout(self)
        s = self.SHADOW
        lay.setContentsMargins(s + 64, s + 14, s + 16, s + 16)
        lay.setSpacing(4)
        self.title = QLabel("Listening…")
        self.title.setProperty("role", "title")
        self.hint = QLabel()
        self.hint.setProperty("role", "muted")
        self.hint.setWordWrap(True)
        lay.addWidget(self.title)
        lay.addWidget(self.hint)
        lay.addSpacing(16)  # room for the level meter
        from .pomo import Pomo

        self.pomo = Pomo()
        self.pomo.outfit = "headphones"
        self._last = time.monotonic()
        self._frames = QTimer(self)
        self._frames.setInterval(40)
        self._frames.timeout.connect(self._tick)
        self._close = QTimer(self)
        self._close.setSingleShot(True)
        self._close.timeout.connect(self.hide)

    def show_state(self, mode: str, title: str, hint: str, anchor: QRect, close_after_ms: int = 0) -> None:
        self.mode = mode
        self.title.setText(title)
        self.hint.setText(hint)
        self.adjustSize()
        self.setFixedHeight(max(110, self.sizeHint().height()))
        screen = QGuiApplication.screenAt(anchor.center()) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        s = self.SHADOW
        if anchor.top() - geo.top() < 60:  # under the island
            x, y = anchor.center().x() - self.width() // 2, anchor.bottom() + self.GAP - s
        else:  # above the orb
            x, y = anchor.center().x() - self.width() // 2, anchor.top() - self.GAP - self.height() + s
        self.move(max(geo.left() - s, min(x, geo.right() + 1 - self.width() + s)),
                  max(geo.top() - s, min(y, geo.bottom() + 1 - self.height() + s)))
        if mode == "error":
            self.pomo._react("ouch", 1.0)
        elif mode == "thinking":
            self.pomo.signal = "busy"
        else:
            self.pomo.signal = None
            self.pomo.hello()
        self.show()
        self.raise_()
        self._frames.start()
        if close_after_ms:
            self._close.start(close_after_ms)
        else:
            self._close.stop()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._frames.stop()
        self.pomo.signal = None

    def mousePressEvent(self, event) -> None:
        talk = getattr(self.ctrl, "talk", None)
        if self.mode == "listening" and talk is not None:
            talk.end()  # a click on the card: done talking
        else:
            self.hide()

    def _tick(self) -> None:
        now = time.monotonic()
        dt, self._last = min(0.1, now - self._last), now
        self.pomo.set_mood("ready" if self.mode == "listening" else "idle")
        self.pomo.update(dt)
        self.update()

    def paintEvent(self, _event) -> None:
        t = self.ctrl.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = self.SHADOW
        card = QRectF(self.rect()).adjusted(s, s, -s, -s)
        p.setPen(Qt.NoPen)
        for i in range(s, 0, -1):
            p.setBrush(t.shadow(int(t.shadow_alpha * 0.22 * (1 - i / s) ** 2)))
            p.drawRoundedRect(card.adjusted(-i, -i + 4, i, i + 4), 14 + i, 14 + i)
        p.setBrush(QColor(t.surface))
        p.setPen(QPen(t.border, 1))
        p.drawRoundedRect(card.adjusted(0.5, 0.5, -0.5, -0.5), 14, 14)
        from .pomo import BODY_RY

        self.pomo.paint(p, QPointF(card.left() + 32, card.top() + 30 + 44 * BODY_RY), 44)
        if self.mode == "listening":  # a soft level meter that follows your voice
            accent = QColor(t.focus)
            base = QPointF(card.left() + 64, card.bottom() - 16)
            for k in range(12):
                lit = self.level * 12 > k
                bar = QColor(accent if lit else t.grid)
                p.setPen(Qt.NoPen)
                p.setBrush(bar)
                h = 4 + 10 * abs(((k * 0.37) % 1.0) - 0.5) * (1.4 if lit else 0.6)
                p.drawRoundedRect(QRectF(base.x() + k * 9, base.y() - h, 5, h), 2, 2)
        p.end()


# ------------------------------------------------------------- push to talk
class Talk(QObject):
    """Push-to-talk: record while a key (or button) is held, then turn it into text."""

    heard = Signal(str)
    state_changed = Signal(str)  # listening | thinking | idle

    def __init__(self, ctrl, parent: QObject | None = None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.recorder = Recorder()
        self.card: ListenCard | None = None
        self.worker: TranscribeWorker | None = None
        self.show_card = True
        self._held_key: int | None = None
        self._started = 0.0
        self._spoke = False
        self._quiet_since = 0.0
        self._poll = QTimer(self)
        self._poll.setInterval(50)
        self._poll.timeout.connect(self._check)

    @property
    def busy(self) -> bool:
        return self.recorder.recording or self.worker is not None

    def begin(self, held_key: int | None = None, show_card: bool = True) -> bool:
        """Start listening. held_key: a virtual key to watch; letting it go ends the recording."""
        if self.busy:
            return False
        self.show_card = show_card
        if self.ctrl.speaker is not None:
            self.ctrl.speaker.stop()  # don't record Pomo's own voice
        if not self.recorder.start():
            self._fail(self.recorder.error or "Pomo couldn't open a microphone.")
            return False
        self._held_key, self._started = held_key, time.monotonic()
        self._spoke, self._quiet_since = False, self._started
        self._poll.start()
        engine = engine_for(self.ctrl.settings, self.ctrl.has_ai_key)
        hint = ("Let go of the keys when you're done." if held_key else "Pomo stops when you pause, or click.") + \
               (" Your voice stays on this PC." if engine == "windows" else f" {ENGINE_LABELS[engine]} turns it into text.")
        self._card("listening", "Listening…", hint)
        self.state_changed.emit("listening")
        return True

    def end(self) -> None:
        """Stop listening and work out what was said."""
        if not self.recorder.recording:
            return
        self._poll.stop()
        long_enough = time.monotonic() - self._started >= MIN_SPEECH_S
        wav = self.recorder.stop()
        if not wav or not long_enough:
            self._card("error", "Too quick!", "Hold the keys while you speak, then let go.", close_after_ms=3000)
            self.state_changed.emit("idle")
            return
        if loudness(wav[44:]) < SILENT_LEVEL and max_level(wav) < SILENT_LEVEL * 3:
            self._fail("Pomo couldn't hear anything. Check your microphone, and that desktop apps may use it "
                       "(Windows Settings > Privacy & security > Microphone).")
            return
        engine = engine_for(self.ctrl.settings, self.ctrl.has_ai_key)
        self._card("thinking", "Got it, one moment…", f"Turning your words into text with {ENGINE_LABELS[engine]}.")
        self.state_changed.emit("thinking")
        self.worker = TranscribeWorker(self.ctrl, wav, engine, self)
        self.worker.done.connect(self._heard)
        self.worker.failed.connect(self._fail)
        self.worker.finished.connect(self._worker_done)
        self.worker.start()

    def cancel(self) -> None:
        self._poll.stop()
        if self.recorder.recording:
            self.recorder.stop()
        if self.card is not None:
            self.card.hide()
        self.state_changed.emit("idle")

    def _check(self) -> None:
        if self.card is not None and self.card.isVisible():
            self.card.level = self.card.level * 0.6 + self.recorder.level * 0.4
        if self.recorder.error and not self.recorder.recording:
            self._poll.stop()
            self._fail(self.recorder.error)
            return
        if self._held_key is not None and sys.platform == "win32":
            import ctypes

            if not ctypes.windll.user32.GetAsyncKeyState(self._held_key) & 0x8000:
                self.end()
            return
        if not self.recorder.recording:  # the one-minute limit
            self.end()
            return
        now = time.monotonic()  # click-to-talk: stop once you've said something and gone quiet
        if self.recorder.level >= SPEECH_LEVEL:
            self._spoke, self._quiet_since = True, now
        elif self._spoke and now - self._quiet_since >= QUIET_END_S:
            self.end()

    def _heard(self, text: str) -> None:
        if self.card is not None:
            self.card.hide()
        self.state_changed.emit("idle")
        self.heard.emit(text)

    def _fail(self, message: str) -> None:
        self._card("error", "Pomo didn't catch that", message, close_after_ms=6000)
        self.state_changed.emit("idle")

    def _worker_done(self) -> None:
        if self.worker is not None:
            self.worker.deleteLater()
        self.worker = None

    def _card(self, mode: str, title: str, hint: str, close_after_ms: int = 0) -> None:
        if not self.show_card and mode != "error":
            return
        if self.card is None:
            self.card = ListenCard(self.ctrl)
        self.card.show_state(mode, title, hint, self.ctrl.view.anchor_rect_global(), close_after_ms)

    def shutdown(self) -> None:
        self.cancel()
        if self.worker is not None:
            self.worker.wait(2000)


def max_level(wav: bytes) -> float:
    """The loudest tenth of a second in a WAV recording."""
    pcm = wav[44:]
    step = 3200  # 0.1 s at 16 kHz
    return max((loudness(pcm[i:i + step]) for i in range(0, len(pcm), step)), default=0.0)
