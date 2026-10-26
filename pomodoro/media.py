"""Now playing: see and control whatever is playing on this PC, from Pomo.

Windows keeps a list of media sessions, the same ones its volume flyout shows: Spotify,
YouTube or any other site playing in Chrome, Edge or Firefox, Apple Music, VLC, and so on.
Pomo reads the current one (title, artist, cover, playing or paused) and can play/pause
and skip it, through Windows' own media controls (the WinRT
GlobalSystemMediaTransportControlsSessionManager). Nothing leaves the PC.
"""
from __future__ import annotations

import asyncio
import re
import sys
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

from PySide6.QtCore import QThread, Signal

POLL_S = 1.0         # how often to look while something is playing or paused
IDLE_POLL_S = 2.5    # while nothing is
PLAYING = 4          # GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING
MAX_ART = 2_000_000  # bytes; covers are small JPEGs or PNGs
ART_TRIES = 4        # apps often share the cover a moment after the title
COMMANDS = ("toggle", "next", "previous")

KNOWN_APPS = (  # a piece of the app id (lowercase) -> the name people know it by
    ("spotify", "Spotify"),
    ("youtubemusic", "YouTube Music"),
    ("music.youtube", "YouTube Music"),
    ("chrome", "Chrome"),
    ("msedge", "Edge"),
    ("microsoftedge", "Edge"),
    ("firefox", "Firefox"),
    ("308046b0af4a39cb", "Firefox"),  # Firefox's app id on Windows
    ("brave", "Brave"),
    ("opera", "Opera"),
    ("vivaldi", "Vivaldi"),
    ("zunemusic", "Media Player"),
    ("applemusic", "Apple Music"),
    ("itunes", "iTunes"),
    ("vlc", "VLC"),
    ("amazon", "Amazon Music"),
    ("deezer", "Deezer"),
    ("tidal", "TIDAL"),
    ("foobar2000", "foobar2000"),
    ("musicbee", "MusicBee"),
    ("aimp", "AIMP"),
)


def app_label(app_id: str) -> str:
    """A friendly name for a media session's app id ("Spotify.exe", "MSEdge", "Publisher.App_hash!App")."""
    low = app_id.lower()
    for key, label in KNOWN_APPS:
        if key in low:
            return label
    if "!" in app_id:  # a Store app: Publisher.AppName_hash!EntryPoint
        name = app_id.split("!")[0].split("_")[0].split(".")[-1]
    else:  # a desktop app: a name, an exe or a full path
        name = re.sub(r"\.exe$", "", re.split(r"[\\/]", app_id)[-1], flags=re.IGNORECASE)
    return (name[:1].upper() + name[1:]) if name else "Media"


@dataclass(frozen=True)
class Track:
    app_id: str
    title: str
    artist: str = ""
    album: str = ""
    playing: bool = False
    can_toggle: bool = True
    can_next: bool = False
    can_previous: bool = False
    art: bytes | None = field(default=None, repr=False)  # the cover (JPEG or PNG bytes), if the app shares one
    duration: float = 0.0  # song length in seconds, if the app shares it
    # where the song was when read; it moves every second, so it doesn't count as a change
    position: float = field(default=0.0, compare=False)
    read_at: float = field(default_factory=time.monotonic, compare=False, repr=False)

    def position_now(self) -> float:
        if not self.playing:
            return self.position
        return min(self.duration or 1e9, self.position + time.monotonic() - self.read_at)

    @property
    def app(self) -> str:
        return app_label(self.app_id)

    @property
    def key(self) -> tuple[str, str, str]:
        """Identifies the song, whatever its play state."""
        return self.app_id, self.title, self.artist

    @property
    def byline(self) -> str:
        return "  ·  ".join(part for part in (self.artist, self.app) if part)


def available() -> bool:
    """True if this PC and build can read Windows' media sessions."""
    if sys.platform != "win32":
        return False
    try:
        import winrt.windows.media.control  # noqa: F401
    except Exception:  # missing package, or a Windows without the API
        return False
    return True


def is_playing(session) -> bool:
    try:
        return int(session.get_playback_info().playback_status) == PLAYING
    except Exception:  # the session closed under us
        return False


def pick_session(sessions, current):
    """The session to show: the current one if it plays, else one that plays, else the current one."""
    if current is not None and is_playing(current):
        return current
    for session in sessions:
        if is_playing(session):
            return session
    return current


def timeline_of(session, playing: bool) -> tuple[float, float]:
    """(song length, position now) in seconds, or zeros when the app doesn't say."""
    try:
        tl = session.get_timeline_properties()
        duration = (tl.end_time - tl.start_time).total_seconds()
        position = (tl.position - tl.start_time).total_seconds()
        updated = tl.last_updated_time
    except Exception:
        return 0.0, 0.0
    if duration <= 0:
        return 0.0, 0.0
    if playing and isinstance(updated, datetime) and updated.year > 2000:
        since = (datetime.now(timezone.utc) - updated.astimezone(timezone.utc)).total_seconds()
        if 0 <= since < duration:  # apps report the position now and then; carry it forward
            position += since
    return round(duration), max(0.0, min(duration, position))


def make_track(session, props, art: bytes | None = None, timeline: tuple[float, float] = (0.0, 0.0)) -> Track | None:
    """A Track from a session and its media properties, or None if there is nothing to show."""
    if session is None or props is None:
        return None
    info = session.get_playback_info()
    playing = int(info.playback_status) == PLAYING
    app_id = session.source_app_user_model_id or ""
    title = (props.title or "").strip()
    artist = (props.artist or props.album_artist or "").strip()
    if not title:
        if not playing:
            return None  # an idle tab or player with nothing loaded
        title = app_label(app_id)
    controls = info.controls
    return Track(
        app_id=app_id, title=title, artist=artist, album=(props.album_title or "").strip(), playing=playing,
        can_toggle=bool(controls.is_play_pause_toggle_enabled or controls.is_play_enabled or controls.is_pause_enabled),
        can_next=bool(controls.is_next_enabled), can_previous=bool(controls.is_previous_enabled), art=art,
        duration=timeline[0], position=timeline[1])


async def read_art(ref) -> bytes | None:
    if ref is None:
        return None
    from winrt.windows.storage.streams import Buffer, InputStreamOptions

    stream = await ref.open_read_async()
    size = min(int(stream.size), MAX_ART)
    if size <= 0:
        return None
    buf = Buffer(size)
    await stream.read_async(buf, size, InputStreamOptions.READ_AHEAD)
    return bytes(memoryview(buf)[: buf.length]) or None


async def send(session, command: str) -> None:
    if command == "next":
        await session.try_skip_next_async()
    elif command == "previous":
        await session.try_skip_previous_async()
    elif command == "toggle":
        info = session.get_playback_info()
        controls = info.controls
        if controls.is_play_pause_toggle_enabled:
            await session.try_toggle_play_pause_async()
        elif int(info.playback_status) == PLAYING:
            await session.try_pause_async()
        else:
            await session.try_play_async()


def check() -> dict:
    """What Pomo sees right now, for troubleshooting (Pomo.exe --media-check)."""
    if not available():
        return {"available": False}

    async def look():
        from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager

        manager = await Manager.request_async()
        sessions = list(manager.get_sessions())
        session = pick_session(sessions, manager.get_current_session())
        track = None
        if session is not None:
            props = await session.try_get_media_properties_async()
            try:
                art = await read_art(props.thumbnail)
            except Exception:
                art = None
            track = make_track(session, props, art)
        return sessions, track

    sessions, track = asyncio.run(look())
    now = None
    if track is not None:
        now = {"title": track.title, "artist": track.artist, "app": track.app, "playing": track.playing,
               "cover_bytes": len(track.art or b"")}
    return {"available": True, "sessions": [app_label(s.source_app_user_model_id or "") for s in sessions],
            "now_playing": now}


class MediaWatcher(QThread):
    """Watches what's playing on a background thread; changed(Track | None) fires on the UI thread."""

    changed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._commands: list[str] = []
        self._stopping = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._art_key: tuple | None = None
        self._art: bytes | None = None
        self._art_tries = 0

    # ------------------------------------------------------------ UI thread
    def command(self, name: str) -> None:
        if name not in COMMANDS:
            raise ValueError(name)
        with self._lock:
            self._commands.append(name)
        self._poke()

    def stop(self) -> None:
        self._stopping = True
        self._poke()
        self.wait(3000)

    def _poke(self) -> None:
        loop, wake = self._loop, self._wake
        if loop is not None and wake is not None:
            try:
                loop.call_soon_threadsafe(wake.set)
            except RuntimeError:  # the loop already finished
                pass

    # -------------------------------------------------------- worker thread
    def run(self) -> None:
        try:
            import winrt._winrt as rt

            try:
                rt.init_apartment(rt.MTA)
            except Exception:  # already initialized on this thread
                pass
            asyncio.run(self._main())
        except Exception:  # no media support this time; Pomo works without it
            self.changed.emit(None)

    async def _main(self) -> None:
        from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager

        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        manager = await Manager.request_async()
        last: Track | None = None
        first = True
        while not self._stopping:
            with self._lock:
                commands, self._commands = self._commands, []
            try:
                session = pick_session(list(manager.get_sessions()), manager.get_current_session())
                if session is not None and commands:
                    for command in commands:
                        await send(session, command)
                    await asyncio.sleep(0.3)  # let the app catch up before reading it again
                track = await self._read(session)
            except Exception:  # sessions come and go between calls
                track = None
            if first or track != last:
                first, last = False, track
                self.changed.emit(track)
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), POLL_S if track else IDLE_POLL_S)
            except asyncio.TimeoutError:
                pass

    async def _read(self, session) -> Track | None:
        if session is None:
            return None
        props = await session.try_get_media_properties_async()
        track = make_track(session, props)
        if track is None:
            return None
        track = replace(track, **dict(zip(("duration", "position"), timeline_of(session, track.playing))))
        if track.key != self._art_key:
            self._art_key, self._art, self._art_tries = track.key, None, 0
        if self._art is None and self._art_tries < ART_TRIES:
            self._art_tries += 1
            try:
                self._art = await read_art(props.thumbnail)
            except Exception:
                self._art = None
        return replace(track, art=self._art)
