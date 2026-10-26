"""What you're working on: project and branch detection for automatic task names, and "Where was I?"."""
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from pomodoro.app import PomoApp
from pomodoro.storage import AgentEvent, Storage
from pomodoro.workspace import FrontTracker, FrontWindow, Project, ProjectFinder, git_branch, name_from_title


def make_repo(folder: Path, head: str = "ref: refs/heads/main\n") -> Path:
    (folder / ".git").mkdir(parents=True)
    (folder / ".git" / "HEAD").write_text(head, encoding="utf-8")
    return folder


class TitleTests(unittest.TestCase):
    def test_editors(self):
        cases = {
            ("Code", "app.py - pomo - Visual Studio Code"): ("pomo", True),
            ("Code", "● island.py - pomo (Workspace) - Visual Studio Code"): ("pomo", True),
            ("Code - Insiders", "app.py - pomo - Visual Studio Code - Insiders"): ("pomo", True),
            ("Code", "x.ts - web [WSL: Ubuntu] - Visual Studio Code"): ("web", True),
            ("Cursor", "main.go - api - Cursor"): ("api", True),
            ("Code", "Welcome - Visual Studio Code"): ("Welcome", False),  # only if a folder is called that
            ("pycharm64", "pomo – app.py"): ("pomo", True),
            ("devenv", "Shop - Microsoft Visual Studio"): ("Shop", True),
            ("sublime_text", "app.py (pomo) - Sublime Text"): ("pomo", True),
        }
        for (exe, title), (name, sure) in cases.items():
            with self.subTest(title=title):
                found, _path, is_sure = name_from_title(exe, title)
                self.assertEqual((found, is_sure), (name, sure))
        self.assertEqual(name_from_title("pycharm64", r"pomo [C:\code\pomo] – …\app.py")[:2], ("pomo", r"C:\code\pomo"))
        self.assertEqual(name_from_title("chrome", "Pull requests - Google Chrome"), ("", "", False))

    def test_task_names(self):
        self.assertEqual(Project("pomo", branch="main").task, "pomo")
        self.assertEqual(Project("pomo", branch="fix-login").task, "pomo · fix-login")
        self.assertEqual(Project("pomo", branch="fix-login", app="VS Code").described,
                         "pomo (branch fix-login) in VS Code")

    def test_window_labels(self):
        self.assertEqual(FrontWindow(1, "chrome", "Lo-fi beats - YouTube - Google Chrome", 2).label,
                         "Chrome · Lo-fi beats - YouTube")
        self.assertEqual(FrontWindow(1, "WINWORD", "Thesis draft - Word", 2).label, "Word · Thesis draft")
        self.assertEqual(FrontWindow(1, "Code", "x", 2, Project("pomo")).label, "VS Code · pomo")


class FinderTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.appdata = self.root / "AppData"
        self.clock = [0.0]

    def tearDown(self):
        self.dir.cleanup()

    def finder(self, extra=()):
        return ProjectFinder(extra_folders=lambda: list(extra), appdata=self.appdata, home=self.root,
                             clock=lambda: self.clock[0])

    def test_branches(self):
        repo = make_repo(self.root / "pomo", "ref: refs/heads/feature/quests\n")
        self.assertEqual(git_branch(repo), "feature/quests")
        (repo / ".git" / "HEAD").write_text("0123456789abcdef0123456789abcdef01234567\n")
        self.assertEqual(git_branch(repo), "0123456")  # detached: the commit
        worktree = self.root / "wt"
        worktree.mkdir()
        real = make_repo(self.root / "real", "ref: refs/heads/hotfix\n") / ".git"
        (worktree / ".git").write_text(f"gitdir: {real}\n")
        self.assertEqual(git_branch(worktree), "hotfix")
        self.assertEqual(git_branch(self.root / "nowhere"), "")

    def test_vscode_recent_folders(self):
        repo = make_repo(self.root / "code" / "pomo", "ref: refs/heads/fix-login\n")
        storage = self.appdata / "Code" / "User" / "globalStorage"
        storage.mkdir(parents=True)
        uri = "file:///" + str(repo).replace("\\", "/").replace(":", "%3A", 1)
        (storage / "storage.json").write_text(json.dumps({"windowsState": {"lastActiveWindow": {"folder": uri}}}))
        project = self.finder().find("Code", "app.py - pomo - Visual Studio Code")
        self.assertEqual((project.name, Path(project.path), project.branch, project.app),
                         ("pomo", repo, "fix-login", "VS Code"))

    def test_vscode_history_database(self):
        repo = make_repo(self.root / "work" / "api")
        storage = self.appdata / "Cursor" / "User" / "globalStorage"
        storage.mkdir(parents=True)
        db = sqlite3.connect(storage / "state.vscdb")
        db.execute("CREATE TABLE ItemTable (key TEXT UNIQUE, value BLOB)")
        uri = "file:///" + str(repo).replace("\\", "/").replace(":", "%3A", 1)
        db.execute("INSERT INTO ItemTable VALUES (?, ?)",
                   ("history.recentlyOpenedPathsList", json.dumps({"entries": [{"folderUri": uri}]})))
        db.commit()
        db.close()
        project = self.finder().find("Cursor", "Welcome - api - Cursor")
        self.assertEqual((project.name, project.branch), ("api", "main"))

    def test_agent_folders_and_unknown_names(self):
        repo = make_repo(self.root / "shop")
        found = self.finder(extra=[str(repo)]).find("Code", "Welcome - shop - Visual Studio Code")
        self.assertEqual(Path(found.path), repo)
        self.assertIsNone(self.finder().find("Code", "Welcome - Visual Studio Code"))  # no folder called Welcome
        unknown = self.finder().find("Code", "a.py - elsewhere - Visual Studio Code")
        self.assertEqual((unknown.name, unknown.path, unknown.branch), ("elsewhere", "", ""))

    def test_a_path_in_the_title(self):
        repo = make_repo(self.root / "notes")
        (repo / "docs").mkdir()
        project = self.finder().find("WindowsTerminal", str(repo / "docs"))
        self.assertEqual((project.name, project.branch, project.app), ("notes", "main", "Terminal"))

    def test_jetbrains_recent_projects(self):
        repo = make_repo(self.root / "code" / "game")
        options = self.appdata / "JetBrains" / "PyCharm2026.2" / "options"
        options.mkdir(parents=True)
        (options / "recentProjects.xml").write_text(
            '<application><component name="RecentProjectsManager"><option name="additionalInfo"><map>'
            '<entry key="$USER_HOME$/code/game"><value/></entry></map></option></component></application>')
        project = self.finder().find("pycharm64", "game – main.py")
        self.assertEqual((Path(project.path), project.branch), (repo, "main"))


class TrackerTests(unittest.TestCase):
    def test_remembers_your_last_window_and_project(self):
        now = [100.0]
        front = [(11, "Code", "app.py - pomo - Visual Studio Code", 999)]
        finder = SimpleNamespace(find=lambda exe, title: Project("pomo", branch="main") if exe == "Code" else None)
        tracker = FrontTracker(finder, source=lambda: front[0], clock=lambda: now[0])
        seen = []
        tracker.project_seen.connect(seen.append)
        tracker.sample()
        self.assertEqual(tracker.last_work.hwnd, 11)
        self.assertEqual(tracker.recent_project().task, "pomo")
        front[0] = (12, "python", "Pomo", os.getpid())  # Pomo's own window: ignored
        tracker.sample()
        self.assertEqual(tracker.last_work.hwnd, 11)
        front[0] = (13, "chrome", "Docs - Google Chrome", 998)
        tracker.sample()
        self.assertEqual((tracker.last_work.hwnd, tracker.last_work.label), (13, "Chrome · Docs"))
        self.assertEqual(tracker.recent_project().name, "pomo")  # still the last project you had open
        tracker.sample()
        self.assertEqual(len(seen), 1)
        now[0] += 3600
        self.assertIsNone(tracker.recent_project())  # too long ago to name a session after


class RecapTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.dir.name) / "history.db")

    def tearDown(self):
        self.storage.close()
        self.dir.cleanup()

    def test_agent_news_from_your_break(self):
        start = datetime.now().replace(microsecond=0) - timedelta(minutes=6)
        add = self.storage.add_agent_event
        add(AgentEvent(start - timedelta(minutes=30), "a", "old", "Stop"))  # before the break: not news
        add(AgentEvent(start + timedelta(minutes=1), "a", "api", "PostToolUse", "Edit", "x.py", 3, 1))
        add(AgentEvent(start + timedelta(minutes=2), "a", "api", "PostToolUse", "Write", "y.py", 9, 0))
        add(AgentEvent(start + timedelta(minutes=3), "a", "api", "Stop"))
        add(AgentEvent(start + timedelta(minutes=4), "b", "web", "Stop"))
        waiting = SimpleNamespace(project="web", status="waiting")
        fake = SimpleNamespace(storage=self.storage, agents=SimpleNamespace(live_sessions=lambda: [waiting]))
        news = PomoApp._agent_news_since(fake, start)
        self.assertEqual(news, "web is waiting for you, api finished and changed 2 files")
        self.assertEqual(PomoApp._agent_news_since(fake, None), "")


if __name__ == "__main__":
    unittest.main()
