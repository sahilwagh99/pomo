"""The Microsoft Store version: its package manifest, and what Pomo does differently when packaged."""
import importlib.util
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from pomodoro import claude_hooks, updater, winutil

ROOT = Path(__file__).resolve().parent.parent
FOUNDATION = "{http://schemas.microsoft.com/appx/manifest/foundation/windows10}"
DESKTOP = "{http://schemas.microsoft.com/appx/manifest/desktop/windows10}"


def load_make_msix():
    spec = importlib.util.spec_from_file_location("make_msix", ROOT / "tools" / "make_msix.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.make_msix = load_make_msix()

    def test_versions_have_four_numbers_ending_in_zero(self):
        self.assertEqual(self.make_msix.package_version("1.9.0"), "1.9.0.0")
        self.assertEqual(self.make_msix.package_version("2.1"), "2.1.0.0")

    def test_identity_from_partner_center(self):
        env = {"POMO_STORE_NAME": "12345Me.Pomo", "POMO_STORE_PUBLISHER": "CN=ABC-123",
               "POMO_STORE_PUBLISHER_NAME": "Me & Co"}
        root = ET.fromstring(self.make_msix.manifest({**self.make_msix.identity(env, store=True),
                                                      "display_name": "Pomo"}))
        ident = root.find(f"{FOUNDATION}Identity")
        self.assertEqual(ident.get("Name"), "12345Me.Pomo")
        self.assertEqual(ident.get("Publisher"), "CN=ABC-123")
        self.assertEqual(ident.get("Version"), self.make_msix.package_version())
        self.assertEqual(root.find(f"{FOUNDATION}Properties/{FOUNDATION}PublisherDisplayName").text, "Me & Co")

    def test_a_store_build_needs_the_identity(self):
        with self.assertRaises(SystemExit):
            self.make_msix.identity({}, store=True)
        with mock.patch("builtins.print"):
            self.assertEqual(self.make_msix.identity({}, store=False)["name"], "Pomo.LocalTest")

    def test_manifest_matches_what_pomo_looks_for(self):
        with mock.patch("builtins.print"):
            values = {**self.make_msix.identity({}, store=False), "display_name": "Pomo"}
        root = ET.fromstring(self.make_msix.manifest(values))
        tasks = [e.get("TaskId") for e in root.iter(f"{DESKTOP}StartupTask")]
        aliases = [e.get("Alias") for e in root.iter(f"{DESKTOP}ExecutionAlias")]
        self.assertEqual(tasks, [winutil.STARTUP_TASK])
        self.assertEqual(aliases, [winutil.APP_ALIAS])


class PackagedTests(unittest.TestCase):
    def test_the_store_updates_it(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "unins000.exe").write_bytes(b"")
            self.assertEqual(updater.install_mode(frozen=True, folder=Path(folder), packaged=True), "store")
            self.assertEqual(updater.install_mode(frozen=False, folder=Path(folder), packaged=True), "source")

    def test_this_copy_is_not_packaged(self):
        self.assertFalse(winutil.is_packaged())

    def test_hooks_go_through_the_alias(self):
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(winutil, "is_packaged", return_value=True):
            exe, args = claude_hooks.hook_command()
        self.assertEqual(Path(exe), winutil.app_alias_path())
        self.assertEqual(Path(exe).name, "pomo.exe")
        self.assertEqual(args, [claude_hooks.MARKER])

    def test_hooks_run_the_exe_itself_otherwise(self):
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(winutil, "is_packaged", return_value=False):
            exe, _ = claude_hooks.hook_command()
        self.assertEqual(exe, sys.executable)


if __name__ == "__main__":
    unittest.main()
