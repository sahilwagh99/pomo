"""Notes: links, tags, the vault on disk, renaming, search and the daily note."""
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from pomodoro import notes
from pomodoro.storage import SessionRecord, Thought


def session(hour, minutes, task="", completed=True, rating=0, mode="focus"):
    start = datetime(2026, 11, 26, hour, 0)
    return SessionRecord(start, start, mode, minutes * 60, minutes * 60, completed, task, rating)


class ParsingTests(unittest.TestCase):
    def test_links(self):
        text = "See [[NumPy basics]], [[Study/Pandas#Groupby|grouping]] and ![[chart.md]]. [[numpy BASICS]] again."
        self.assertEqual(notes.links(text), ["NumPy basics", "Study/Pandas", "chart"])

    def test_links_in_code_dont_count(self):
        text = "Real [[One]]\n```\nnot [[Two]]\n```\nand `[[Three]]` either"
        self.assertEqual(notes.links(text), ["One"])

    def test_tags(self):
        text = "# Heading #inside\nA #idea, a #study/python tag, #idea again\nnot a#tag, not http://x.com/#frag, ## nor #123"
        self.assertEqual(notes.tags(text), ["inside", "idea", "study/python"])

    def test_safe_names_and_linking(self):
        self.assertEqual(notes.safe_name('What: is "this"?'), "What is this")
        self.assertEqual(notes.link_to("pomo · fix-login"), "[[pomo · fix-login]]")
        # the link goes to the note's file name, showing the task as typed
        self.assertEqual(notes.link_to("Thesis: ch 3"), "[[Thesis ch 3|Thesis: ch 3]]")
        self.assertEqual(notes.link_to("issue #42"), "[[issue 42]]")
        self.assertEqual(notes.links("Done [[Thesis ch 3|Thesis: ch 3]]"), ["Thesis ch 3"])


class VaultTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name) / "Pomo Notes"
        self.vault = notes.Vault(self.root)
        self.assertTrue(self.vault.ensure())

    def tearDown(self):
        self._dir.cleanup()

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_lists_notes_but_not_hidden_folders(self):
        self.write("A.md", "")
        self.write("Study/B.md", "")
        self.write(".obsidian/C.md", "")
        self.write(".trash/D.md", "")
        self.write("notes.txt", "")
        self.assertEqual([self.vault.rel(p) for p in self.vault.notes()], ["A", "Study/B"])

    def test_resolving_like_obsidian(self):
        deep = self.write("Study/Deep/Pandas.md", "")
        shallow = self.write("Study/Pandas.md", "")
        self.assertEqual(self.vault.resolve("pandas"), shallow)
        self.assertEqual(self.vault.resolve("Study/Deep/Pandas#Intro"), deep)
        self.assertIsNone(self.vault.resolve("Missing"))

    def test_index_links_backlinks_and_tags(self):
        a = self.write("A.md", "Links to [[B]] and [[Nowhere]] #idea")
        b = self.write("Sub/B.md", "Back to [[a]] #Idea #other")
        idx = self.vault.index()
        self.assertEqual(idx.outgoing[a], [b])
        self.assertEqual(idx.unresolved[a], ["Nowhere"])
        self.assertEqual(idx.backlinks[b], [a])
        self.assertEqual(idx.backlinks[a], [b])
        self.assertEqual(sorted(idx.tags), ["idea", "other"])
        self.assertEqual(idx.tags["idea"], [a, b])

    def test_keeps_line_endings(self):
        path = self.root / "Windows.md"
        path.write_bytes(b"one\r\ntwo\r\n")
        self.assertEqual(self.vault.read(path), "one\ntwo\n")
        self.vault.write(path, "one\ntwo\nthree\n")
        self.assertEqual(path.read_bytes(), b"one\r\ntwo\r\nthree\r\n")
        new = self.vault.create("Fresh", text="a\nb")
        self.assertEqual(new.read_bytes(), b"a\nb")

    def test_saving_waits_out_a_brief_lock(self):
        # antivirus or OneDrive holding the file for a moment makes Windows refuse the swap: try again
        path = self.vault.create("Locked", text="before")
        real, refusals = notes.os.replace, []

        def flaky(src, dst):
            if len(refusals) < 3:
                refusals.append(dst)
                raise PermissionError(13, "The process cannot access the file")
            real(src, dst)

        from unittest import mock
        with mock.patch.object(notes.os, "replace", flaky):
            self.vault.write(path, "after")
        self.assertEqual(len(refusals), 3)
        self.assertEqual(path.read_text(encoding="utf-8"), "after")

    def test_new_notes_get_free_names(self):
        first = self.vault.create("Idea")
        second = self.vault.create("Idea")
        self.assertEqual((first.name, second.name), ("Idea.md", "Idea 1.md"))
        self.assertEqual(self.vault.open_or_create("idea"), first)
        task = self.vault.open_or_create("Thesis: ch 3", notes.TASKS)
        self.assertEqual(self.vault.rel(task), "Tasks/Thesis ch 3")
        self.assertEqual(self.vault.open_or_create("Thesis: ch 3", notes.TASKS), task)  # the same note again

    def test_rename_fixes_links(self):
        old = self.write("Old.md", "Self link [[Old]]")
        other = self.write("Other.md", "See [[old]], [[Old#Part|that part]] and [[Older]]")
        new, fixed = self.vault.rename(old, "New name")
        self.assertEqual(new.name, "New name.md")
        self.assertFalse(old.exists())
        self.assertEqual(fixed, 1)
        self.assertEqual(self.vault.read(other), "See [[New name]], [[New name#Part|that part]] and [[Older]]")
        self.assertEqual(self.vault.read(new), "Self link [[New name]]")
        self.write("Taken.md", "")
        with self.assertRaises(FileExistsError):
            self.vault.rename(new, "Taken")

    def test_trash(self):
        path = self.write("Gone.md", "bye")
        dest = self.vault.trash(path)
        self.assertFalse(path.exists())
        self.assertEqual(dest, self.root / ".trash" / "Gone.md")
        self.assertEqual(self.vault.notes(), [])

    def test_search(self):
        self.write("Pandas groupby.md", "split apply combine")
        self.write("Other.md", "uses pandas a lot #python")
        self.assertEqual([p.stem for p, _ in self.vault.search("pandas")], ["Pandas groupby", "Other"])
        self.assertEqual([p.stem for p, _ in self.vault.search("#python")], ["Other"])
        self.assertEqual(self.vault.search("missing words"), [])

    def test_kinds(self):
        self.assertEqual(self.vault.kind(self.root / "Daily" / "2026-11-26.md"), "daily")
        self.assertEqual(self.vault.kind(self.root / "Study" / "Step.md"), "study")
        self.assertEqual(self.vault.kind(self.root / "Idea.md"), "note")


class DailyNoteTests(unittest.TestCase):
    def test_block(self):
        thoughts = [Thought(1, datetime(2026, 11, 26, 9), "call mum"), Thought(2, datetime(2026, 11, 26, 9), "buy milk", True)]
        block = notes.daily_block([session(9, 25, "Thesis", rating=3), session(10, 10, completed=False),
                                   session(11, 5, mode="short")], thoughts)
        self.assertEqual(block.splitlines(), [
            notes.BLOCK_START,
            "### Focus: 35 min",
            "- 09:00 · 25 min on [[Thesis]] · great",
            "- 10:00 · 10 min · stopped early",
            "### Parked thoughts",
            "- call mum",
            "- ~~buy milk~~",
            notes.BLOCK_END,
        ])

    def test_pomo_only_touches_its_part(self):
        first = notes.with_block("", "<!-- pomo:start -->\nA\n<!-- pomo:end -->")
        self.assertTrue(first.startswith(notes.BLOCK_START))
        mine = first + "My own words.\n"
        again = notes.with_block(mine, "<!-- pomo:start -->\nB\n<!-- pomo:end -->")
        self.assertEqual(again, "<!-- pomo:start -->\nB\n<!-- pomo:end -->\n\nMy own words.\n")
        added = notes.with_block("Written in Obsidian.", "<!-- pomo:start -->\nC\n<!-- pomo:end -->")
        self.assertEqual(added, "Written in Obsidian.\n\n<!-- pomo:start -->\nC\n<!-- pomo:end -->\n")

    def test_step_seed(self):
        self.assertEqual(notes.step_seed("Data analyst", "SQL basics"),
                         "Part of [[SQL basics]], on the way to [[Data analyst]].\n\n")
        self.assertEqual(notes.daily_path(notes.Vault(Path("v")), date(2026, 11, 26)),
                         Path("v") / "Daily" / "2026-11-26.md")


if __name__ == "__main__":
    unittest.main()
