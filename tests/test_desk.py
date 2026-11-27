"""The panel's desk tabs: clipboard history, the shelf, the launcher and recent documents.

Nothing here opens a file or launches an app for real (os.startfile and Explorer are mocked). One test goes
through the real clipboard; it puts back whatever was on it before, and marks its own test text so Windows'
clipboard history and other clipboard tools leave it out.
"""
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QFile, QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QColor, QDragEnterEvent, QDragLeaveEvent, QDropEvent, QGuiApplication, QImage
from PySide6.QtTest import QTest

from pomodoro import panel_desk as desk
from pomodoro.config import Settings
from pomodoro.theme import theme_for

PRIVATE = 'application/x-qt-windows-mime;value="{}"'


class Ctrl:
    """Just enough of the app for the desk tabs."""

    def __init__(self):
        self.theme = theme_for("light")
        self.settings = Settings()
        self.saves = 0

    def save_settings(self):
        self.saves += 1


def text(value: str) -> QMimeData:
    mime = QMimeData()
    mime.setText(value)
    return mime


def picture(color: str, w: int = 40, h: int = 30) -> QMimeData:
    image = QImage(w, h, QImage.Format_ARGB32)
    image.fill(QColor(color))
    mime = QMimeData()
    mime.setImageData(image)
    return mime


def wait_for(condition, ms: int = 2000) -> bool:
    deadline = time.monotonic() + ms / 1000
    while time.monotonic() < deadline:
        QTest.qWait(20)
        if condition():
            return True
    return condition()


def link(target: Path, shortcut: Path, when: float | None = None) -> Path:
    """A Windows shortcut (.lnk) to target, last touched at when."""
    assert QFile.link(str(target), str(shortcut))
    if when is not None:
        os.utime(shortcut, (when, when))
    return shortcut


# ------------------------------------------------------------------ clipboard history
class ClipboardHistoryTests(unittest.TestCase):
    def setUp(self):
        self.ctrl = Ctrl()
        self.history = desk.ClipboardHistory(self.ctrl)

    def texts(self):
        return [e.text for e in self.history.entries]

    def test_newest_first_without_repeats(self):
        for value in ("a", "b", "a", "c"):
            self.history.take(text(value))
        self.assertEqual(self.texts(), ["c", "a", "b"])

    def test_keeps_30_texts_and_8_images(self):
        for i in range(35):
            self.history.take(text(f"t{i}"))
        for i in range(10):
            self.history.take(picture(QColor.fromHsv(i * 30, 200, 200).name()))
        texts = [e for e in self.history.entries if not e.is_image]
        images = [e for e in self.history.entries if e.is_image]
        self.assertEqual(len(texts), desk.MAX_TEXTS)
        self.assertEqual(texts[0].text, "t34")
        self.assertEqual(texts[-1].text, "t5")
        self.assertEqual(len(images), desk.MAX_IMAGES)

    def test_images_get_a_thumbnail_and_dont_repeat(self):
        first = self.history.take(picture("#f0a3b1", 1000, 500))
        self.assertTrue(first.is_image)
        self.assertEqual(first.image.size().toTuple(), (1000, 500))
        self.assertLessEqual(first.thumb.width(), desk.THUMB.width())
        self.assertLessEqual(first.thumb.height(), desk.THUMB.height())
        again = self.history.take(picture("#f0a3b1", 1000, 500))
        self.assertIs(again, first)
        self.assertEqual(len(self.history.entries), 1)

    def test_pinned_stay_on_top_through_trimming_and_clear(self):
        keep = self.history.take(text("keep me"))
        self.history.pin(keep)
        for i in range(40):
            self.history.take(text(f"t{i}"))
        self.assertIs(self.history.entries[0], keep)
        self.assertEqual(len(self.history.entries), desk.MAX_TEXTS + 1)
        self.history.clear()
        self.assertEqual(self.history.entries, [keep])
        self.history.pin(keep, False)
        self.history.take(text("newer"))
        self.assertEqual(self.texts(), ["newer", "keep me"])
        self.history.clear(keep_pinned=False)
        self.assertEqual(self.history.entries, [])

    def test_remove(self):
        a = self.history.take(text("a"))
        self.history.take(text("b"))
        self.history.remove(a)
        self.assertEqual(self.texts(), ["b"])

    def test_private_content_is_skipped(self):
        for name, value in (("ExcludeClipboardContentFromMonitorProcessing", b"\0"),
                            ("Clipboard Viewer Ignore", b""),
                            ("CanIncludeInClipboardHistory", (0).to_bytes(4, "little"))):
            mime = text("hunter2")
            mime.setData(PRIVATE.format(name), value)
            self.assertIsNone(self.history.take(mime), name)
        allowed = text("fine to keep")
        allowed.setData(PRIVATE.format("CanIncludeInClipboardHistory"), (1).to_bytes(4, "little"))
        self.assertIsNotNone(self.history.take(allowed))
        self.assertEqual(self.texts(), ["fine to keep"])

    def test_paused_takes_nothing(self):
        self.ctrl.settings.clipboard_on = False
        self.assertIsNone(self.history.take(text("secret")))
        self.assertEqual(self.history.entries, [])

    def test_skips_blank_text_huge_text_and_copied_files(self):
        self.assertIsNone(self.history.take(text("   \n")))
        self.assertIsNone(self.history.take(text("x" * (desk.MAX_TEXT_CHARS + 1))))
        files = QMimeData()
        files.setUrls([QUrl.fromLocalFile("C:/somewhere/file.txt")])
        self.assertIsNone(self.history.take(files))
        self.assertEqual(self.history.entries, [])

    def test_changed_fires(self):
        seen = []
        self.history.changed.connect(lambda: seen.append(1))
        entry = self.history.take(text("a"))
        self.history.pin(entry)
        self.history.remove(entry)
        self.assertEqual(len(seen), 3)


class _QuietMime(QMimeData):
    """Test text marked so Windows' clipboard history and other clipboard tools don't keep it."""

    def __init__(self):
        super().__init__()
        self.setData(PRIVATE.format("CanIncludeInClipboardHistory"), (0).to_bytes(4, "little"))
        self.setData(PRIVATE.format("ExcludeClipboardContentFromMonitorProcessing"), b"\0")


def snapshot_clipboard() -> QMimeData:
    """A copy of what's on the clipboard now, to put back afterwards."""
    current = QGuiApplication.clipboard().mimeData()
    saved = QMimeData()
    for fmt in current.formats():
        if not fmt.startswith(("image/", "application/x-qt-image")):  # images come back with setImageData
            saved.setData(fmt, current.data(fmt))
    if current.hasImage():
        saved.setImageData(current.imageData())
    if current.hasText():
        saved.setText(current.text())
    return saved


class RealClipboardTests(unittest.TestCase):
    def setUp(self):
        self.ctrl = Ctrl()
        saved = snapshot_clipboard()
        self.addCleanup(self._restore, saved)
        quiet = mock.patch.object(desk, "QMimeData", _QuietMime)  # what copy() puts back, marked too
        private = mock.patch.object(desk, "is_private", lambda mime: False)  # and still read here
        quiet.start()
        private.start()
        self.addCleanup(quiet.stop)
        self.addCleanup(private.stop)
        self.history = desk.ClipboardHistory(self.ctrl)

    def _restore(self, saved: QMimeData) -> None:
        self.ctrl.settings.clipboard_on = False  # don't record the user's own clipboard coming back
        QGuiApplication.clipboard().setMimeData(saved)
        QTest.qWait(100)

    def put(self, value: str) -> None:
        mime = _QuietMime()
        mime.setText(value)
        QGuiApplication.clipboard().setMimeData(mime)

    def texts(self):
        return [e.text for e in self.history.entries]

    def test_copies_come_in_and_go_back_without_repeats(self):
        self.put("pomo test one")
        self.assertTrue(wait_for(lambda: self.texts() == ["pomo test one"]))
        self.put("pomo test two")
        self.assertTrue(wait_for(lambda: self.texts() == ["pomo test two", "pomo test one"]))
        self.history.copy(self.history.entries[1])
        self.assertEqual(QGuiApplication.clipboard().text(), "pomo test one")
        QTest.qWait(300)  # the clipboard's own change notice comes in later
        self.assertEqual(self.texts(), ["pomo test one", "pomo test two"])
        self.ctrl.settings.clipboard_on = False
        self.put("pomo test three")
        QTest.qWait(300)
        self.assertEqual(self.texts(), ["pomo test one", "pomo test two"])


# ------------------------------------------------------------------ pages
class PageTests(unittest.TestCase):
    def setUp(self):
        self.ctrl = Ctrl()
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)

    def file(self, name: str) -> str:
        path = self.root / name
        path.write_text("x")
        return str(path)

    def test_every_page_builds_and_paints(self):
        history = desk.ClipboardHistory(self.ctrl)
        history.take(text("hello"))
        history.take(picture("#a7b3e3", 300, 200))
        self.ctrl.settings.shelf_items = [self.file("a.txt")]
        self.ctrl.settings.launcher_apps = [self.file("tool.exe")]
        recent = self.root / "Recent"
        recent.mkdir()
        link(Path(self.file("doc.md")), recent / "doc.md.lnk")
        pages = [desk.ClipboardPage(self.ctrl, history), desk.ShelfPage(self.ctrl), desk.LauncherPage(self.ctrl),
                 desk.DocsPage(self.ctrl, recent)]
        for page in pages:
            page.resize(1100, 240)
            page.refresh()
            self.assertGreater(page.list.count(), 0, type(page).__name__)
            self.assertFalse(page.grab().isNull())
            page.list.clear()
            self.assertFalse(page.grab().isNull())  # the empty hint
            page.deleteLater()

    # -------------------------------------------------------------- Clipboard
    def test_clipboard_page(self):
        history = desk.ClipboardHistory(self.ctrl)
        history.take(text("first"))
        history.take(picture("#f0a3b1"))
        pinned = history.take(text("pinned"))
        history.pin(pinned)
        page = desk.ClipboardPage(self.ctrl, history)
        page.refresh()
        self.assertEqual(page.list.count(), 3)
        self.assertTrue(page.list.item(0).data(desk.PINNED))
        self.assertEqual(page.list.item(1).data(Qt.DisplayRole), "Image")
        with mock.patch.object(history, "copy") as copy:
            page._clicked(page.list.item(2))
        copy.assert_called_once_with(history.find(page.list.item(2).data(desk.KEY)))
        self.assertEqual(page.note.text(), "Copied")
        self.assertTrue(page.list.item(2).data(desk.FLASH))
        page.clear()
        self.assertEqual(history.entries, [pinned])
        page.remember.setChecked(False)
        self.assertFalse(self.ctrl.settings.clipboard_on)
        self.assertEqual(self.ctrl.saves, 1)
        self.assertIn("Paused", page.list.hint)

    # -------------------------------------------------------------- Shelf
    def test_shelf_adds_newest_first_without_repeats(self):
        page = desk.ShelfPage(self.ctrl)
        a, b = self.file("a.txt"), self.file("b.txt")
        page.take([a])
        page.take([b, a.upper().replace("\\", "/")])  # the same file, spelled differently
        self.assertEqual(self.ctrl.settings.shelf_items, [b, a.upper()])
        self.assertEqual(page.list.count(), 2)
        self.assertEqual(page.list.item(0).text(), "b.txt")
        self.assertEqual(page.list.item(0).data(desk.SUB), str(self.root))
        page.remove(b)
        self.assertEqual(len(self.ctrl.settings.shelf_items), 1)
        page.clear()
        self.assertEqual(self.ctrl.settings.shelf_items, [])

    def test_shelf_holds_40(self):
        page = desk.ShelfPage(self.ctrl)
        paths = [self.file(f"f{i}.txt") for i in range(45)]
        for path in paths:
            page.take([path])
        self.assertEqual(len(self.ctrl.settings.shelf_items), desk.SHELF_MAX)
        self.assertEqual(self.ctrl.settings.shelf_items[0], paths[-1])

    def test_shelf_drops_missing_files_on_refresh(self):
        page = desk.ShelfPage(self.ctrl)
        a, b = self.file("a.txt"), self.file("b.txt")
        page.take([a, b])
        os.remove(a)
        saves = self.ctrl.saves
        page.refresh()
        self.assertEqual(self.ctrl.settings.shelf_items, [b])
        self.assertEqual(self.ctrl.saves, saves + 1)

    def test_shelf_opens_and_drags_out(self):
        page = desk.ShelfPage(self.ctrl)
        a = self.file("a.txt")
        page.take([a])
        with mock.patch("os.startfile") as start:
            page.list.itemDoubleClicked.emit(page.list.item(0))
        start.assert_called_once_with(a)
        mime = desk.file_mime([a])
        self.assertEqual([u.toLocalFile() for u in mime.urls()], [a.replace("\\", "/")])

    def test_drops_are_copies_never_moves(self):
        page = desk.ShelfPage(self.ctrl)
        a = self.file("a.txt")
        mime = desk.file_mime([a])
        enter = QDragEnterEvent(QPointF(5, 5), Qt.CopyAction | Qt.MoveAction, mime, Qt.LeftButton, Qt.NoModifier)
        page.dragEnterEvent(enter)
        self.assertTrue(enter.isAccepted())
        self.assertIn("border", page.drop_card.styleSheet())  # lit up while you hover
        page.dragLeaveEvent(QDragLeaveEvent())
        self.assertEqual(page.drop_card.styleSheet(), "")
        drop = QDropEvent(QPointF(5, 5), Qt.CopyAction | Qt.MoveAction, mime, Qt.LeftButton, Qt.NoModifier)
        page.dropEvent(drop)
        self.assertTrue(drop.isAccepted())
        self.assertEqual(drop.dropAction(), Qt.CopyAction)  # so Explorer never deletes the original
        self.assertEqual(self.ctrl.settings.shelf_items, [a])
        other = desk.file_mime([self.file("b.txt")])  # events only point at their data: keep it alive
        move_only = QDropEvent(QPointF(5, 5), Qt.MoveAction, other, Qt.LeftButton, Qt.NoModifier)
        page.dropEvent(move_only)
        self.assertFalse(move_only.isAccepted())
        self.assertEqual(self.ctrl.settings.shelf_items, [a])

    # -------------------------------------------------------------- Launcher
    def test_launcher_takes_apps_only_in_order_without_repeats(self):
        page = desk.LauncherPage(self.ctrl)
        exe, lnk, url, txt = (self.file(n) for n in ("Tool.exe", "Code.lnk", "Game.url", "notes.txt"))
        page.take([exe, txt, lnk])
        page.take([url, exe.lower()])
        self.assertEqual(self.ctrl.settings.launcher_apps, [exe, lnk, url])
        self.assertEqual([page.list.item(i).text() for i in range(3)], ["Tool", "Code", "Game"])
        page.remove(lnk)
        self.assertEqual(self.ctrl.settings.launcher_apps, [exe, url])

    def test_launcher_holds_24(self):
        page = desk.LauncherPage(self.ctrl)
        apps = [self.file(f"app{i}.exe") for i in range(30)]
        page.take(apps[:20])
        page.take(apps[20:])
        self.assertEqual(self.ctrl.settings.launcher_apps, apps[:desk.LAUNCHER_MAX])
        self.assertIn(str(desk.LAUNCHER_MAX), page.note.text())

    def test_launcher_drop_wants_apps(self):
        page = desk.LauncherPage(self.ctrl)
        notes = desk.file_mime([self.file("notes.txt")])
        enter = QDragEnterEvent(QPointF(5, 5), Qt.CopyAction, notes, Qt.LeftButton, Qt.NoModifier)
        page.dragEnterEvent(enter)
        self.assertFalse(enter.isAccepted())
        exe = self.file("tool.exe")
        app = desk.file_mime([exe])
        page.dropEvent(QDropEvent(QPointF(5, 5), Qt.CopyAction, app, Qt.LeftButton, Qt.NoModifier))
        self.assertEqual(self.ctrl.settings.launcher_apps, [exe])

    def test_launcher_add_app_launch_and_show(self):
        page = desk.LauncherPage(self.ctrl)
        exe, lnk = self.file("tool.exe"), self.file("Thing.lnk")
        with mock.patch.object(desk, "QFileDialog") as dialog:
            dialog.getOpenFileNames.return_value = ([exe, lnk], "")
            page.pick()
        self.assertIn("*.exe *.lnk *.url", dialog.getOpenFileNames.call_args.args[3])
        self.assertEqual(self.ctrl.settings.launcher_apps, [exe, lnk])
        with mock.patch("os.startfile") as start:
            page.list.itemClicked.emit(page.list.item(0))
            page.list.itemClicked.emit(page.list.item(1))
        self.assertEqual(start.call_args_list, [mock.call(exe, cwd=str(self.root)), mock.call(lnk)])
        with mock.patch("os.startfile", side_effect=OSError):
            page.launch(exe)
        self.assertIn("Couldn't open", page.note.text())
        with mock.patch.object(desk.subprocess, "Popen") as popen:
            desk.show_in_folder(exe)
        popen.assert_called_once_with(f'explorer /select,"{exe}"')

    # -------------------------------------------------------------- Docs
    def test_recent_docs_newest_first_without_folders_or_missing(self):
        recent = self.root / "Recent"
        recent.mkdir()
        now = time.time()
        old, new = Path(self.file("old.docx")), Path(self.file("new.xlsx"))
        gone = Path(self.file("gone.txt"))
        folder = self.root / "Folder"
        folder.mkdir()
        link(old, recent / "old.docx.lnk", now - 3 * 86400)
        link(new, recent / "new.xlsx.lnk", now - 60)
        link(new, recent / "new copy.lnk", now - 120)  # another shortcut to the same file
        link(gone, recent / "gone.txt.lnk", now - 30)
        link(folder, recent / "Folder.lnk", now - 10)
        (recent / "not a shortcut.txt").write_text("x")
        gone.unlink()
        docs = desk.recent_docs(recent)
        self.assertEqual([d.name for d in docs], ["new.xlsx", "old.docx"])
        self.assertTrue(os.path.samefile(docs[0].folder, self.root))
        self.assertTrue(os.path.samefile(docs[0].path, new))
        self.assertEqual(desk.recent_docs(self.root / "nowhere"), [])

    def test_when_text(self):
        now = datetime(2026, 11, 28, 16, 30)
        self.assertEqual(desk.when_text(datetime(2026, 11, 28, 14, 5), now), "today 14:05")
        self.assertEqual(desk.when_text(now - timedelta(days=1), now), "yesterday")
        self.assertEqual(desk.when_text(datetime(2026, 11, 20, 9, 0), now), "Nov 20")
        self.assertEqual(desk.when_text(datetime(2025, 11, 20, 9, 0), now), "Nov 20, 2025")

    def test_docs_page_filters_and_opens(self):
        recent = self.root / "Recent"
        recent.mkdir()
        now = time.time()
        for i, name in enumerate(("Thesis draft.docx", "Budget.xlsx", "thesis notes.md")):
            link(Path(self.file(name)), recent / (name + ".lnk"), now - i * 60)
        page = desk.DocsPage(self.ctrl, recent)
        page.refresh()
        self.assertEqual(page.list.count(), 3)
        self.assertTrue(page.list.item(0).data(desk.CORNER).startswith("today "))
        page.filter.setText("thesis")
        self.assertEqual([page.list.item(i).text() for i in range(page.list.count())],
                         ["Thesis draft.docx", "thesis notes.md"])
        page.filter.setText("zzz")
        self.assertEqual(page.list.count(), 0)
        self.assertIn("Nothing matches", page.list.hint)
        page.filter.clear()
        with mock.patch("os.startfile") as start:
            page.list.itemClicked.emit(page.list.item(1))
        start.assert_called_once()
        self.assertTrue(os.path.samefile(start.call_args.args[0], self.root / "Budget.xlsx"))


class HelperTests(unittest.TestCase):
    def test_dedupe_keeps_the_first_and_caps(self):
        paths = ["C:/a/b.txt", "c:\\A\\B.TXT", "", "C:/c.txt", "C:/d.txt"]
        self.assertEqual(desk.dedupe(paths, 2), ["C:\\a\\b.txt", "C:\\c.txt"])

    def test_two_lines(self):
        font = desk._font(13)
        self.assertEqual(desk.two_lines("short\n\n  text", font, 400), ["short text"])
        lines = desk.two_lines("word " * 200, font, 150)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[1].endswith("…"))
        self.assertEqual(desk.two_lines("   ", font, 100), [])

    def test_app_name(self):
        self.assertEqual(desk.app_name(r"C:\Apps\Visual Studio Code.lnk"), "Visual Studio Code")
        self.assertEqual(desk.app_name(r"C:\Windows\notepad.exe"), "notepad")


if __name__ == "__main__":
    unittest.main()
