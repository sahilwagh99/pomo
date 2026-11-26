"""The notes window, its editor, the daily note and sticky notes, driven like a user would."""
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest

from pomodoro import notes, stickies
from pomodoro.app import PomoApp
from pomodoro.config import Settings
from pomodoro.notes_window import NoteEditor, NotesWindow
from pomodoro.storage import SessionRecord, Thought
from pomodoro.theme import theme_for


class Ctrl:
    """Just enough of the app for these windows."""

    def __init__(self, root: Path):
        self.theme = theme_for("light")
        self.settings = Settings()
        self.settings.notes_dir = str(root)
        self.vault = notes.Vault(root)
        self.vault.ensure()
        from pomodoro.icons import app_icon
        self.icon = app_icon()
        self.daily_opened = 0

    def notes_vault(self):
        return self.vault

    def save_settings(self):
        pass

    def open_daily_note(self):
        self.daily_opened += 1

    def refresh_daily_note(self, force=False):
        return None


def type_line(editor: NoteEditor, text: str) -> None:
    QTest.keyClicks(editor, text)


class EditorTests(unittest.TestCase):
    def setUp(self):
        self.editor = NoteEditor()

    def tearDown(self):
        self.editor.deleteLater()

    def test_lists_continue_and_end(self):
        type_line(self.editor, "- milk")
        QTest.keyClick(self.editor, Qt.Key_Return)
        type_line(self.editor, "eggs")
        QTest.keyClick(self.editor, Qt.Key_Return)
        QTest.keyClick(self.editor, Qt.Key_Return)  # an empty bullet ends the list
        type_line(self.editor, "done")
        self.assertEqual(self.editor.toPlainText(), "- milk\n- eggs\ndone")

    def test_numbers_and_checkboxes_continue(self):
        type_line(self.editor, "1. one")
        QTest.keyClick(self.editor, Qt.Key_Return)
        type_line(self.editor, "two")
        self.assertEqual(self.editor.toPlainText(), "1. one\n2. two")
        self.editor.clear()
        type_line(self.editor, "- [x] task")
        QTest.keyClick(self.editor, Qt.Key_Return)
        self.assertEqual(self.editor.toPlainText(), "- [x] task\n- [ ] ")

    def test_ctrl_enter_ticks(self):
        type_line(self.editor, "buy milk")
        QTest.keyClick(self.editor, Qt.Key_Return, Qt.ControlModifier)
        self.assertEqual(self.editor.toPlainText(), "- [ ] buy milk")
        QTest.keyClick(self.editor, Qt.Key_Return, Qt.ControlModifier)
        self.assertEqual(self.editor.toPlainText(), "- [x] buy milk")
        QTest.keyClick(self.editor, Qt.Key_Return, Qt.ControlModifier)
        self.assertEqual(self.editor.toPlainText(), "- [ ] buy milk")

    def test_formatting_shortcuts(self):
        type_line(self.editor, "word")
        self.editor.selectAll()
        QTest.keyClick(self.editor, Qt.Key_B, Qt.ControlModifier)
        self.assertEqual(self.editor.toPlainText(), "**word**")
        QTest.keyClick(self.editor, Qt.Key_B, Qt.ControlModifier)  # again: off
        self.assertEqual(self.editor.toPlainText(), "word")
        QTest.keyClick(self.editor, Qt.Key_X, Qt.ControlModifier | Qt.ShiftModifier)
        self.assertEqual(self.editor.toPlainText(), "~~word~~")

    def test_link_suggestions(self):
        self.editor.set_titles(["SQL basics", "Pandas groupby"])
        type_line(self.editor, "see [[sq")
        self.editor._complete("SQL basics")
        self.assertEqual(self.editor.toPlainText(), "see [[SQL basics]]")

    def test_replace_text_keeps_the_cursor(self):
        self.editor.setPlainText("top\n<!-- pomo:start -->\nA\n<!-- pomo:end -->\n\nmine")
        cursor = self.editor.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.editor.setTextCursor(cursor)
        self.editor.replace_text("top\n<!-- pomo:start -->\nA longer\n<!-- pomo:end -->\n\nmine")
        self.assertEqual(self.editor.textCursor().position(), len(self.editor.toPlainText()))


class WindowTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name) / "Notes"
        self.ctrl = Ctrl(self.root)
        self.vault = self.ctrl.vault
        self.a = self.vault.create("Alpha", text="Links to [[Beta]] #idea")
        self.b = self.vault.create("Beta", text="Back to [[Alpha]]")
        self.window = NotesWindow(self.ctrl)

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self._dir.cleanup()

    def test_edits_are_saved(self):
        self.window.open_path(self.a)
        self.window.editor.setPlainText("New words")
        self.window.save()
        self.assertEqual(self.vault.read(self.a), "New words")

    def test_backlinks_and_unwritten_links(self):
        self.vault.write(self.a, "Links to [[Beta]] and [[Gamma]]")
        self.window.refresh()
        self.window.open_path(self.a)
        self.assertEqual(self.window.back_list.item(0).text(), "Beta")
        outs = [self.window.out_list.item(i).text() for i in range(self.window.out_list.count())]
        self.assertEqual(outs, ["Beta", "Gamma"])
        self.window.open_link("Gamma")  # following it starts the note
        self.assertTrue((self.root / "Gamma.md").exists())
        self.assertEqual(self.window.current, self.root / "Gamma.md")

    def test_rename_updates_links(self):
        self.window.open_path(self.b)
        self.window.title_edit.setText("Bravo")
        self.window._rename()
        self.assertEqual(self.window.current, self.root / "Bravo.md")
        self.assertEqual(self.vault.read(self.a), "Links to [[Bravo]] #idea")

    def test_new_and_delete(self):
        self.window.new_note()
        new = self.window.current
        self.assertEqual(new.name, "Untitled.md")
        self.vault.trash(new)  # what Delete does once you confirm
        self.window.refresh()
        self.assertNotIn(new, self.window.index.notes)

    def test_search_and_graph(self):
        self.window.search.setText("#idea")
        self.assertEqual(self.window.list.count(), 1)
        self.window.show_graph(True)
        keys = {n["key"] for n in self.window.graph.nodes}
        self.assertEqual(keys, {str(self.a), str(self.b)})
        self.assertEqual(len(self.window.graph.edges), 1)

    def test_the_daily_note_updates_in_place(self):
        path = notes.daily_path(self.vault, date.today())
        self.vault.write(path, notes.with_block("", notes.daily_block([], [])) + "My own words.")
        self.window.refresh()
        self.window.open_path(path)
        block = notes.daily_block([], [Thought(1, datetime.now(), "call mum")])
        self.window.edit_open_note(lambda text: notes.with_block(text, block))
        saved = self.vault.read(path)
        self.assertIn("- call mum", saved)
        self.assertTrue(saved.endswith("My own words."))


class DailyNoteFromAppTests(unittest.TestCase):
    """PomoApp.refresh_daily_note, run against a stand-in for the app."""

    def test_writes_todays_sessions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "Notes"
            vault = notes.Vault(root)
            vault.ensure()
            now = datetime.now().replace(second=0, microsecond=0)

            class Storage:
                def sessions(self, since=None):
                    return [SessionRecord(now, now, "focus", 1500, 1500, True, "Thesis")]

                def thoughts(self, include_done=False):
                    return [Thought(1, now, "email Sam")]

            class App:
                settings = Settings()
                notes_window = None
                storage = Storage()

                def notes_vault(self):
                    return vault

            App.settings.notes_dir = str(root)
            path = PomoApp.refresh_daily_note(App())
            text = vault.read(path)
            self.assertIn("25 min on [[Thesis]]", text)
            self.assertIn("- email Sam", text)
            App.settings.notes_daily = False
            self.assertIsNone(PomoApp.refresh_daily_note(App()))


class StickyTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.path = Path(self._dir.name) / "stickies.json"

        class C:
            theme = theme_for("light")

        self.board = stickies.StickyBoard(C(), self.path)

    def tearDown(self):
        for note in list(self.board.notes):
            note.close()
            note.deleteLater()
        self._dir.cleanup()

    def test_write_with_bullets_and_strikethrough(self):
        note = self.board.new_note()
        QTest.keyClicks(note.body, "Shopping")
        QTest.keyClick(note.body, Qt.Key_Return)
        QTest.keyClicks(note.body, "- milk")  # "- " starts a bullet list
        QTest.keyClick(note.body, Qt.Key_Return)
        QTest.keyClicks(note.body, "eggs")
        note.body.moveCursor(QTextCursor.StartOfBlock, QTextCursor.KeepAnchor)
        QTest.keyClick(note.body, Qt.Key_T, Qt.ControlModifier)  # strike "eggs"
        self.board.save_now()
        saved = json.loads(self.path.read_text(encoding="utf-8"))[0]["text"]
        self.assertIn("Shopping", saved)
        self.assertIn("- milk", saved)
        self.assertIn("- ~~eggs~~", saved)

    def test_saved_and_restored(self):
        note = self.board.new_note()
        note.body.setPlainText("Remember this")
        note.set_color("long")
        note.hide_note()
        self.board.save_now()

        class C:
            theme = theme_for("plum")

        again = stickies.StickyBoard(C(), self.path)
        again.restore()
        self.assertEqual(again.count(), 1)
        self.assertEqual(again.shown(), 0)  # it was hidden
        self.assertEqual(again.notes[0].data["color"], "long")
        self.assertEqual(again.notes[0].body.toPlainText(), "Remember this")
        again.show_all()
        self.assertEqual(again.shown(), 1)
        for n in again.notes:
            n.close()

    def test_checklists(self):
        note = self.board.new_note()
        QTest.keyClicks(note.body, "[] milk")  # "[] " starts a checklist
        self.assertEqual(note.body.textCursor().blockFormat().marker(), stickies.UNCHECKED)
        QTest.keyClick(note.body, Qt.Key_Return, Qt.ControlModifier)  # Ctrl+Enter ticks it
        self.assertEqual(note.body.textCursor().blockFormat().marker(), stickies.CHECKED)
        QTest.keyClick(note.body, Qt.Key_Return)
        QTest.keyClicks(note.body, "eggs")  # the next item starts unticked
        self.assertEqual(note.body.textCursor().blockFormat().marker(), stickies.UNCHECKED)
        self.assertEqual(note.data["text"], "- [x] milk\n- [ ] eggs")
        box = note.body._box_rect(note.body.document().findBlockByNumber(1)).center().toPoint()
        QTest.mouseClick(note.body.viewport(), Qt.LeftButton, Qt.NoModifier, box)  # clicking the box ticks it too
        self.assertEqual(note.data["text"], "- [x] milk\n- [x] eggs")
        note.body.selectAll()
        note.toggle_checklist()  # the button turns the checklist back into bullets
        self.assertEqual(note.data["text"], "- milk\n- eggs")

    def test_reminders(self):
        heard = []
        self.board.ctrl.sticky_reminder = lambda note, late=False: heard.append((note.first_line(), late))
        note = self.board.new_note()
        note.body.setPlainText("Call Mum")
        due = datetime.now().replace(second=0, microsecond=0) + timedelta(hours=1)
        note.set_reminder(due)
        self.assertTrue(note.chip.isVisibleTo(note))
        self.assertEqual(self.board.check_reminders(due - timedelta(minutes=1)), [])  # not yet
        self.assertEqual(self.board.check_reminders(due), [note])
        self.assertEqual(heard, [("Call Mum", False)])
        self.assertEqual((note.data["remind_at"], note.data["rang"]), ("", due.isoformat(timespec="minutes")))
        self.assertTrue(note.rebounce_timer.isActive())
        note.acknowledge()
        self.assertEqual(note.data["rang"], "")
        self.assertFalse(note.rebounce_timer.isActive())
        self.assertFalse(note.chip.isVisibleTo(note))
        # a reminder that came due while Pomo was off goes off (as missed) when it's back
        note.set_reminder(due)
        self.board.save_now()

        class C:
            theme = theme_for("light")
            sticky_reminder = staticmethod(lambda n, late=False: heard.append((n.first_line(), late)))

        again = stickies.StickyBoard(C(), self.path)
        again.restore()
        again.check_reminders(due + timedelta(hours=3))
        self.assertEqual(heard[-1], ("Call Mum", True))
        for n in again.notes:
            n.rebounce_timer.stop()
            n.close()

    def test_quick_times(self):
        afternoon = stickies.quick_times(datetime(2026, 11, 26, 15, 10))
        self.assertEqual([label for label, _ in afternoon],
                         ["In 15 minutes", "In 1 hour", "This evening", "Tomorrow morning"])
        self.assertEqual(afternoon[2][1], datetime(2026, 11, 26, 18, 0))
        late = stickies.quick_times(datetime(2026, 11, 26, 17, 45))
        self.assertNotIn("This evening", [label for label, _ in late])
        self.assertEqual(stickies.when_text(datetime(2026, 11, 27, 9, 0), datetime(2026, 11, 26, 20, 0)),
                         "tomorrow 9:00 AM")
        self.assertEqual(stickies.clean([{"remind_at": "not a time"}])[0]["remind_at"], "")

    def test_delete_and_clean(self):
        note = self.board.new_note()
        self.board.remove(note)
        self.assertEqual(self.board.count(), 0)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), [])
        tidy = stickies.clean([{"w": 5, "color": "neon", "on_top": "yes"}, "junk"])
        self.assertEqual(len(tidy), 1)
        self.assertEqual((tidy[0]["w"], tidy[0]["color"], tidy[0]["on_top"]), (stickies.MIN_SIZE[0], "focus", False))


if __name__ == "__main__":
    unittest.main()
