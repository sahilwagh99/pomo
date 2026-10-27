"""Updates from GitHub: finding a newer release, downloading and verifying it, and how it's installed."""
import hashlib
import tempfile
import unittest
from pathlib import Path

import httpx2

from pomodoro import updater

INSTALLER = b"MZ pretend this is Pomo-Setup.exe " * 1000
SHA = hashlib.sha256(INSTALLER).hexdigest()


def release_json(tag="v1.5.1", digest=True, checksum_asset=False, prerelease=False):
    assets = [{"name": "Pomo-Setup.exe", "size": len(INSTALLER),
               "browser_download_url": "https://github.com/x/releases/download/v1.5.1/Pomo-Setup.exe",
               **({"digest": f"sha256:{SHA}"} if digest else {})},
              {"name": "Pomo-portable.zip", "browser_download_url": "https://github.com/x/portable.zip"}]
    if checksum_asset:
        assets.append({"name": "Pomo-Setup.exe.sha256",
                       "browser_download_url": "https://github.com/x/releases/download/v1.5.1/Pomo-Setup.exe.sha256"})
    return {"tag_name": tag, "html_url": "https://github.com/sahilwagh99/pomo/releases/tag/v1.5.1",
            "body": "## What's Changed\n* **Cheer**: Pomo cheers you on\n* Faster island\n\n**Full Changelog**: x",
            "prerelease": prerelease, "assets": assets}


class VersionTests(unittest.TestCase):
    def test_versions(self):
        self.assertEqual(updater.parse_version("v1.5.0"), (1, 5, 0))
        self.assertIsNone(updater.parse_version("v1.6.0-beta"))
        self.assertTrue(updater.is_newer("1.5.1", "1.5.0"))
        self.assertTrue(updater.is_newer("v1.10.0", "1.9.9"))
        self.assertTrue(updater.is_newer("1.6", "1.5.9"))
        self.assertFalse(updater.is_newer("1.5.0", "1.5"))
        self.assertFalse(updater.is_newer("1.4.0", "1.5.0"))
        self.assertFalse(updater.is_newer("nonsense", "1.5.0"))

    def test_reading_a_release(self):
        release = updater.release_of(release_json())
        self.assertEqual((release.version, release.sha256, release.size), ("1.5.1", SHA, len(INSTALLER)))
        self.assertTrue(release.installer_url.endswith("/Pomo-Setup.exe"))
        self.assertEqual(release.notes, "Cheer: Pomo cheers you on\nFaster island")
        by_file = updater.release_of(release_json(digest=False, checksum_asset=True))
        self.assertEqual((by_file.sha256, by_file.checksum_url.endswith(".sha256")), ("", True))
        self.assertIsNone(updater.release_of(release_json(prerelease=True)))
        self.assertIsNone(updater.release_of(release_json(tag="nightly")))
        self.assertIsNone(updater.release_of("nope"))


class ThisCopyTests(unittest.TestCase):
    def test_which_copies_update_themselves(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            self.assertEqual(updater.install_mode(frozen=False, folder=folder), "source")
            self.assertEqual(updater.install_mode(frozen=True, folder=folder), "manual")  # portable: no uninstaller
            (folder / "unins000.exe").write_bytes(b"")
            self.assertEqual(updater.install_mode(frozen=True, folder=folder), "installer")
            self.assertEqual(list(folder.glob(".pomo-write-test")), [])  # the write test leaves nothing behind

    def test_the_silent_install(self):
        args = updater.installer_args(Path(r"C:\data\updates\Pomo-Setup-1.5.1.exe"), Path(r"C:\Apps\Pomo"),
                                      Path(r"C:\data\updates\install.log"), Path(r"C:\Apps\Pomo\Pomo.exe"))
        self.assertEqual(args[0], r"C:\data\updates\Pomo-Setup-1.5.1.exe")
        for flag in ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/UPDATE=1", r"/DIR=C:\Apps\Pomo",
                     "/MERGETASKS=!autostart,!desktopicon", r"/RELAUNCH=C:\Apps\Pomo\Pomo.exe"):
            self.assertIn(flag, args)  # same folder, startup and icon choices untouched, Pomo back at the end


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.folder = Path(self.dir.name) / "updates"
        self.events = []

    def tearDown(self):
        self.dir.cleanup()

    def run_worker(self, handler, current="1.5.0", download=True):
        worker = updater.UpdateWorker(current, self.folder, download, transport=httpx2.MockTransport(handler))
        worker.checked.connect(lambda r: self.events.append(("checked", r.version if r else None)))
        worker.ready.connect(lambda r, path: self.events.append(("ready", path)))
        worker.failed.connect(lambda msg, tell: self.events.append(("failed", tell)))
        worker.run()  # on this thread: the signals arrive right away
        return self.events

    def github(self, release, installer=INSTALLER, checksum=SHA):
        def handler(request):
            url = str(request.url)
            if url == updater.LATEST_URL:
                return httpx2.Response(200, json=release)
            if url.endswith(".sha256"):
                return httpx2.Response(200, text=f"{checksum}  Pomo-Setup.exe")
            if url.endswith("Pomo-Setup.exe"):
                return httpx2.Response(200, content=installer)
            return httpx2.Response(404)
        return handler

    def test_up_to_date(self):
        events = self.run_worker(self.github(release_json(tag="v1.5.0")))
        self.assertEqual(events, [("checked", None)])

    def test_downloads_and_verifies(self):
        events = self.run_worker(self.github(release_json()))
        self.assertEqual(events[0], ("checked", "1.5.1"))
        kind, path = events[1]
        self.assertEqual(kind, "ready")
        self.assertEqual(Path(path).read_bytes(), INSTALLER)
        self.assertEqual(Path(path).name, "Pomo-Setup-1.5.1.exe")
        self.events.clear()
        self.run_worker(self.github(release_json(), installer=b"never fetched"))  # already here and verified
        self.assertEqual(self.events[1][0], "ready")

    def test_checksum_from_a_file(self):
        events = self.run_worker(self.github(release_json(digest=False, checksum_asset=True)))
        self.assertEqual(events[1][0], "ready")

    def test_a_tampered_download_is_thrown_away(self):
        events = self.run_worker(self.github(release_json(), installer=b"something else entirely"))
        self.assertEqual(events, [("checked", "1.5.1"), ("failed", True)])
        self.assertEqual(list(self.folder.iterdir()), [])  # nothing half-downloaded left behind

    def test_no_checksum_no_install(self):
        events = self.run_worker(self.github(release_json(digest=False)))
        self.assertEqual(events, [("checked", "1.5.1"), ("failed", True)])

    def test_portable_copies_only_hear_about_it(self):
        events = self.run_worker(self.github(release_json()), download=False)
        self.assertEqual(events, [("checked", "1.5.1")])

    def test_offline(self):
        def offline(request):
            raise httpx2.ConnectError("no network", request=request)
        events = self.run_worker(offline)
        self.assertEqual(events, [("failed", False)])  # nothing to tell the user: try again later

    def test_no_releases_yet(self):
        events = self.run_worker(lambda request: httpx2.Response(404, json={}))
        self.assertEqual(events, [("checked", None)])


if __name__ == "__main__":
    unittest.main()


class NotificationClickTests(unittest.TestCase):
    """With the timer hidden, 'Pomo X is available' comes as a Windows notification; clicking it offers the update."""

    def make(self):
        from unittest import mock

        from pomodoro.config import Settings
        from pomodoro.notifier import Notifier

        class Tray:
            shown = []

            def isVisible(self):
                return True

            def icon(self):
                return None

            def showMessage(self, title, message, *_):
                self.shown.append(title)

        patch = mock.patch("pomodoro.notifier.QSystemTrayIcon.supportsMessages", return_value=True)
        patch.start()
        self.addCleanup(patch.stop)
        return Notifier(Settings(), Path(tempfile.gettempdir()), Tray()), Tray.shown

    def test_clicking_runs_what_that_notification_was_about(self):
        notifier, shown = self.make()
        clicks = []
        self.assertTrue(notifier.toast("Pomo 9.9.0 is available", "Click here to update.", on_click=lambda: clicks.append(1)))
        notifier.clicked()
        notifier.clicked()  # once only
        self.assertEqual((shown, clicks), (["Pomo 9.9.0 is available"], [1]))

    def test_a_newer_notification_replaces_the_click(self):
        notifier, _shown = self.make()
        clicks = []
        notifier.toast("Pomo 9.9.0 is available", "Click here to update.", on_click=lambda: clicks.append(1))
        notifier.toast("Focus session complete", "Time for a break.")
        notifier.clicked()
        self.assertEqual(clicks, [])

    def test_nothing_shown_when_notifications_are_off(self):
        notifier, shown = self.make()
        notifier.settings.desktop_notifications = False
        self.assertFalse(notifier.toast("Pomo 9.9.0 is available", "Click here to update."))
        self.assertEqual(shown, [])

    def test_checks_every_hour(self):
        self.assertEqual(updater.CHECK_EVERY_MS, 3600 * 1000)
        self.assertGreater(updater.OFFER_AGAIN_S, 0)
