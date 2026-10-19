import random
import unittest
from types import SimpleNamespace

from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QImage, QPainter

from pomodoro.config import OUTFITS
from pomodoro.media import PLAYING, MediaWatcher, Track, app_label, make_track, pick_session
from pomodoro.pomo import Pomo

PAUSED = 5


def session(app="Spotify.exe", status=PLAYING, toggle=True, next_=True, previous=False):
    controls = SimpleNamespace(is_play_pause_toggle_enabled=toggle, is_play_enabled=True, is_pause_enabled=True,
                               is_next_enabled=next_, is_previous_enabled=previous)
    info = SimpleNamespace(playback_status=status, controls=controls)
    return SimpleNamespace(source_app_user_model_id=app, get_playback_info=lambda: info)


def props(title="Matcha Lo-fi", artist="Pomo Beats", album_artist="", album="Tea Time"):
    return SimpleNamespace(title=title, artist=artist, album_artist=album_artist, album_title=album, thumbnail=None)


class AppNameTests(unittest.TestCase):
    def test_known_apps(self):
        for app_id, label in (("Spotify.exe", "Spotify"),
                              ("SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify", "Spotify"),
                              ("Chrome", "Chrome"), ("MSEdge", "Edge"), ("308046B0AF4A39CB", "Firefox"),
                              ("Microsoft.ZuneMusic_8wekyb3d8bbwe!Microsoft.ZuneMusic", "Media Player"),
                              (r"C:\Program Files\VideoLAN\VLC\vlc.exe", "VLC")):
            with self.subTest(app_id=app_id):
                self.assertEqual(app_label(app_id), label)

    def test_unknown_apps_get_a_readable_name(self):
        self.assertEqual(app_label(r"C:\Tools\tunebox.exe"), "Tunebox")
        self.assertEqual(app_label("Contoso.CoolPlayer_abc123!App"), "CoolPlayer")
        self.assertEqual(app_label(""), "Media")


class TrackTests(unittest.TestCase):
    def test_reads_a_playing_song(self):
        track = make_track(session(), props())
        self.assertEqual((track.title, track.artist, track.album, track.app), ("Matcha Lo-fi", "Pomo Beats", "Tea Time", "Spotify"))
        self.assertTrue(track.playing)
        self.assertEqual((track.can_toggle, track.can_next, track.can_previous), (True, True, False))
        self.assertEqual(track.byline, "Pomo Beats  ·  Spotify")

    def test_youtube_in_a_browser(self):
        # browsers report the video title, and the channel as the artist
        track = make_track(session("Chrome", status=PAUSED), props("Lo-fi beats to focus to", "Pomo Radio", album=""))
        self.assertFalse(track.playing)
        self.assertEqual(track.byline, "Pomo Radio  ·  Chrome")

    def test_album_artist_stands_in_for_a_missing_artist(self):
        self.assertEqual(make_track(session(), props(artist="", album_artist="Mochi Trio")).artist, "Mochi Trio")

    def test_nothing_to_show(self):
        self.assertIsNone(make_track(None, props()))
        self.assertIsNone(make_track(session(status=PAUSED), props(title="  ")))  # an idle tab
        # something plays without a title: show the app instead
        self.assertEqual(make_track(session("MSEdge"), props(title="")).title, "Edge")

    def test_same_song_whatever_its_state(self):
        playing, paused = make_track(session(), props()), make_track(session(status=PAUSED), props())
        self.assertEqual(playing.key, paused.key)
        self.assertNotEqual(playing, paused)

    def test_prefers_what_is_playing(self):
        spotify, chrome = session("Spotify.exe", PAUSED), session("Chrome", PLAYING)
        self.assertIs(pick_session([spotify, chrome], spotify), chrome)
        self.assertIs(pick_session([spotify, chrome], chrome), chrome)
        self.assertIs(pick_session([spotify], spotify), spotify)  # nothing plays: the current one
        self.assertIsNone(pick_session([], None))

    def test_only_known_commands(self):
        watcher = MediaWatcher()
        with self.assertRaises(ValueError):
            watcher.command("louder")
        watcher.command("toggle")  # queued until the watcher runs
        self.assertEqual(watcher._commands, ["toggle"])


class ListeningTests(unittest.TestCase):
    def settle(self, pomo: Pomo, seconds: float) -> None:
        for _ in range(int(seconds * 30)):
            pomo.update(1 / 30)

    def test_notes_float_up_while_music_plays(self):
        pomo = Pomo(random.Random(2))
        pomo.listening = True
        self.settle(pomo, 6.0)
        notes = [p for p in pomo.particles if p.kind == "note"]
        self.assertTrue(notes)
        self.assertTrue(all(p.vy < 0 for p in notes))
        pomo.listening = False
        self.settle(pomo, 4.0)
        self.assertFalse([p for p in pomo.particles if p.kind == "note"])  # they drift away when it stops

    def test_grooving_uses_a_medium_frame_rate(self):
        pomo = Pomo(random.Random(4))
        pomo._next_fidget = pomo._next_blink = 999
        self.settle(pomo, 1.0)
        self.assertTrue(pomo.is_calm())
        pomo.listening = True
        self.settle(pomo, 3.0)
        self.assertFalse(pomo.is_calm())
        self.assertTrue(pomo.is_grooving())
        pomo.poke()
        self.assertFalse(pomo.is_grooving())  # a poke needs the full frame rate

    def test_new_song_bop(self):
        pomo = Pomo(random.Random(1))
        pomo.new_song()
        self.assertGreater(pomo.hop_v, 0)
        self.assertEqual(sum(p.kind == "note" for p in pomo.particles), 3)

    def test_paints_with_headphones_under_every_outfit(self):
        def right_of_body(outfit: str, listening: bool) -> str:
            image = QImage(160, 160, QImage.Format_ARGB32)
            image.fill(QColor("#ffffff"))
            pomo = Pomo(random.Random(3))
            pomo.outfit, pomo.listening = outfit, listening
            pomo._next_fidget = pomo._next_blink = 999
            self.settle(pomo, 1.2)
            pomo.t = 0.0  # no sway at this instant
            p = QPainter(image)
            pomo.paint(p, QPointF(80, 100), 90)
            p.end()
            return image.pixelColor(80 + 47, 98).name()  # just past his side: where a headphone cup sits

        self.assertEqual(right_of_body("blossom", listening=False), "#ffffff")
        for outfit in OUTFITS:
            with self.subTest(outfit=outfit):
                self.assertNotEqual(right_of_body(outfit, listening=True), "#ffffff")


if __name__ == "__main__":
    unittest.main()
