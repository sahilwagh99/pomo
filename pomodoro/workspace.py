"""What you're working on: the window in front, and the project and git branch open in your editor.

FrontTracker looks at the window in front every few seconds and remembers
  - the last window you worked in (never Pomo's own), so "Where was I?" can bring it back after a break;
  - the project open in your editor, with its git branch, so a focus session can be named after it.

The project comes from the editor's window title ("app.py - pomo - Visual Studio Code"), matched to a
folder the editor recently opened (its own recent list), a folder your coding agents work in, or a
path in the title. The branch is read from the repository's .git/HEAD. Titles stay in memory: Pomo
never stores them.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse

from PySide6.QtCore import QObject, QTimer, Signal

from .presence import APP_LABELS, BROWSERS, foreground_window

POLL_MS = 3000
KNOWN_TTL_S = 90.0        # rebuild the list of known project folders this often
BRANCH_TTL_S = 20.0       # re-read a repository's branch this often
DEFAULT_BRANCHES = ("main", "master")

VSCODE_FAMILY = {  # exe -> its folder under %APPDATA%
    "code": "Code", "code - insiders": "Code - Insiders", "cursor": "Cursor", "windsurf": "Windsurf",
    "vscodium": "VSCodium", "trae": "Trae", "kiro": "Kiro", "positron": "Positron",
}
VSCODE_SUFFIXES = (" - Visual Studio Code - Insiders", " - Visual Studio Code", " - Cursor", " - Windsurf",
                   " - VSCodium", " - Trae", " - Kiro", " - Positron")
JETBRAINS = {"idea64", "idea", "pycharm64", "pycharm", "webstorm64", "rider64", "goland64", "clion64", "phpstorm64",
             "rubymine64", "datagrip64", "studio64", "rustrover64", "dataspell64", "aqua64"}
EDITOR_LABELS = {"code - insiders": "VS Code Insiders", "cursor": "Cursor", "windsurf": "Windsurf",
                 "vscodium": "VSCodium", "sublime_text": "Sublime Text", "notepad++": "Notepad++", "zed": "Zed",
                 "webstorm64": "WebStorm", "rider64": "Rider", "goland64": "GoLand", "clion64": "CLion",
                 "phpstorm64": "PhpStorm", "rubymine64": "RubyMine", "rustrover64": "RustRover"}
IGNORED_APPS = {"searchhost", "searchapp", "startmenuexperiencehost", "shellexperiencehost", "lockapp",
                "textinputhost", "shellhost", "systemsettingsbroker"}
_DECORATIONS = re.compile(r"\s*(\((Workspace|Administrator)\)|\[(WSL|SSH|Dev Container|Codespaces|Administrator)[^\]]*\])",
                          re.IGNORECASE)
_WIN_PATH = re.compile(r"[A-Za-z]:\\[^<>:\"|?*\r\n]+")


@dataclass(frozen=True)
class Project:
    name: str
    path: str = ""
    branch: str = ""
    app: str = ""  # the editor it was open in

    @property
    def task(self) -> str:
        """The focus task it suggests: 'pomo', or 'pomo · fix-login' away from the main branch."""
        if self.branch and self.branch not in DEFAULT_BRANCHES:
            return f"{self.name} · {self.branch}"
        return self.name

    @property
    def described(self) -> str:
        """'pomo (branch fix-login) in VS Code'."""
        text = self.name + (f" (branch {self.branch})" if self.branch else "")
        return text + (f" in {self.app}" if self.app else "")


@dataclass(frozen=True)
class FrontWindow:
    hwnd: int
    exe: str
    title: str
    pid: int
    project: Project | None = field(default=None, compare=False)

    @property
    def app(self) -> str:
        return app_label(self.exe)

    @property
    def label(self) -> str:
        """'VS Code · pomo', 'Chrome · Pull requests', 'Word · Thesis draft'."""
        if self.project is not None:
            return f"{self.app} · {self.project.name}"
        detail = short_title(self.exe, self.title)
        return f"{self.app} · {detail}" if detail and detail != self.app else self.app


def app_label(exe: str) -> str:
    low = exe.lower()
    return EDITOR_LABELS.get(low) or APP_LABELS.get(low) or (exe[:1].upper() + exe[1:] if exe else "an app")


def short_title(exe: str, title: str, limit: int = 40) -> str:
    """A window title without its app name: the page in a browser, the document elsewhere."""
    text = title.strip()
    if exe.lower() in BROWSERS:  # "Pull requests - Google Chrome", "x - Profile 1 - Microsoft Edge"
        for sep in (" - ", " — ", " – "):
            if sep in text:
                text = text.rsplit(sep, 1)[0]
                break
        if " and " in text and text.endswith("more pages"):
            text = text.split(" and ")[0]
    else:
        for sep in (" - ", " — ", " – "):
            if sep in text:
                head, tail = text.rsplit(sep, 1)
                if len(tail) < 40:  # the app's name usually comes last
                    text = head
                break
    text = text.lstrip("●*• ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


# --------------------------------------------------------------- the project
def name_from_title(exe: str, title: str) -> tuple[str, str, bool]:
    """(project name, a path found in the title, sure) from an editor's window title.

    sure is False when the title might not name a folder (say, a lone "Welcome" tab): such a name is only
    used when it matches a folder Pomo knows.
    """
    low = exe.lower()
    text = title.strip().lstrip("●*• ").strip()
    found = _WIN_PATH.search(text)
    path = found.group(0).strip().rstrip("\\") if found else ""
    if low in VSCODE_FAMILY:
        for suffix in VSCODE_SUFFIXES:
            if text.endswith(suffix):
                text = text[: -len(suffix)]
                break
        text = _DECORATIONS.sub("", text).strip()
        parts = [p.strip() for p in text.split(" - ") if p.strip()]
        if not parts:
            return "", path, False
        return (parts[1], path, True) if len(parts) >= 2 else (parts[0], path, False)
    if low in JETBRAINS:  # "pomo – app.py" or "pomo [C:\code\pomo] – …\app.py"
        head = text.split(" – ")[0].strip()
        bracket = re.search(r"\[([A-Za-z]:\\[^\]]+)\]", head)
        if bracket:
            return head[: bracket.start()].strip(), bracket.group(1), True
        return head, path, len(text.split(" – ")) >= 2
    if low == "devenv":  # "MySolution - Microsoft Visual Studio"
        head = text.split(" - ")[0].strip()
        return head, path, " - Microsoft Visual Studio" in text
    if low == "sublime_text":  # "app.py (pomo) - Sublime Text"
        inner = re.search(r"\(([^()]+)\)\s*-\s*Sublime Text", text)
        return (inner.group(1).strip(), path, True) if inner else ("", path, False)
    if path:  # Notepad++, a terminal that shows its folder...
        return "", path, False
    return "", "", False


def git_root(path: str) -> Path | None:
    """The folder holding .git, at the path or above it."""
    if not path:
        return None
    here = Path(path)
    if here.is_file():
        here = here.parent
    for folder in [here, *list(here.parents)[:8]]:
        try:
            if (folder / ".git").exists():
                return folder
        except OSError:
            return None
    return None


def git_branch(root: Path | str) -> str:
    """The branch checked out in a repository ('' if unknown; a short commit id when detached)."""
    git = Path(root) / ".git"
    try:
        if git.is_file():  # a worktree or submodule: "gitdir: <where the real one is>"
            pointer = git.read_text(encoding="utf-8", errors="replace").strip()
            if not pointer.startswith("gitdir:"):
                return ""
            target = Path(pointer[7:].strip())
            git = target if target.is_absolute() else (Path(root) / target)
        head = (git / "HEAD").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    if head.startswith("ref:"):
        ref = head[4:].strip()
        return ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref.rsplit("/", 1)[-1]
    return head[:7] if re.fullmatch(r"[0-9a-f]{40,64}", head) else ""


def _uri_path(uri) -> str:
    """'file:///c%3A/code/pomo' -> 'c:\\code\\pomo' (remote folders give '')."""
    if not isinstance(uri, str) or not uri.startswith("file://"):
        return ""
    parsed = urlparse(uri)
    path = unquote(parsed.path)
    if re.match(r"^/[A-Za-z]:", path):
        path = path[1:]
    if parsed.netloc:  # a network share
        path = f"//{parsed.netloc}{path}"
    return str(Path(path))


def _vscode_folders(appdata: Path) -> list[str]:
    """Folders VS Code (and its forks) opened recently, newest first."""
    out: list[str] = []
    for folder in dict.fromkeys(VSCODE_FAMILY.values()):
        storage = appdata / folder / "User" / "globalStorage"
        try:
            data = json.loads((storage / "storage.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        windows = data.get("windowsState") if isinstance(data, dict) else None
        if isinstance(windows, dict):
            last = windows.get("lastActiveWindow") or {}
            out.append(_uri_path(last.get("folder")))
            out += [_uri_path(w.get("folder")) for w in windows.get("openedWindows") or [] if isinstance(w, dict)]
        db = storage / "state.vscdb"
        if db.exists():
            try:
                con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=0.2)
                try:
                    row = con.execute("SELECT value FROM ItemTable WHERE key = 'history.recentlyOpenedPathsList'"
                                      ).fetchone()
                finally:
                    con.close()
                entries = json.loads(row[0]).get("entries", []) if row else []
                out += [_uri_path(e.get("folderUri")) for e in entries[:60] if isinstance(e, dict)]
            except (sqlite3.Error, ValueError, TypeError, AttributeError):
                pass
    return [p for p in out if p]


def _jetbrains_folders(appdata: Path, home: Path) -> list[str]:
    out: list[str] = []
    try:
        files = list((appdata / "JetBrains").glob("*/options/recentProjects.xml"))
    except OSError:
        return out
    for xml in files:
        try:
            text = xml.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for key in re.findall(r'<entry key="([^"]+)"', text):
            path = key.replace("$USER_HOME$", str(home)).replace("/", os.sep)
            if re.match(r"^[A-Za-z]:", path):
                out.append(path)
    return out


class ProjectFinder:
    """Turns an editor's window title into a Project, with the folders editors and agents opened as hints."""

    def __init__(self, extra_folders=None, appdata: Path | None = None, home: Path | None = None,
                 clock=time.monotonic):
        self.extra_folders = extra_folders or (lambda: [])  # say, the folders your coding agents work in
        self.appdata = appdata or Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        self.home = home or Path.home()
        self._clock = clock
        self._known: dict[str, str] = {}
        self._known_at = -1e9
        self._branches: dict[str, tuple[float, str]] = {}

    def known(self, refresh: bool = False) -> dict[str, str]:
        """Folder name (lowercase) -> path, newest first wins."""
        now = self._clock()
        if refresh or now - self._known_at > KNOWN_TTL_S:
            self._known_at = now
            known: dict[str, str] = {}
            folders = list(self.extra_folders()) + _vscode_folders(self.appdata) + _jetbrains_folders(
                self.appdata, self.home)
            for path in folders:
                name = Path(path).name.lower()
                if name and name not in known:
                    known[name] = path
            self._known = known
        return self._known

    def branch(self, path: str) -> str:
        root = git_root(path)
        if root is None:
            return ""
        key, now = str(root), self._clock()
        cached = self._branches.get(key)
        if cached is None or now - cached[0] > BRANCH_TTL_S:
            cached = (now, git_branch(root))
            self._branches[key] = cached
        return cached[1]

    def find(self, exe: str, title: str) -> Project | None:
        name, path, sure = name_from_title(exe, title)
        if not name and path:
            root = git_root(path)
            if root is None:
                return None
            name, path, sure = root.name, str(root), True
        if not name:
            return None
        if not path:
            path = self.known().get(name.lower(), "")
            if not path and not sure:
                return None
        if path and not Path(path).exists():
            path = ""
        return Project(name, path, self.branch(path) if path else "", app_label(exe))


# --------------------------------------------------------------- the tracker
class FrontTracker(QObject):
    """Remembers the last window you worked in and the last project you had open in an editor."""

    project_seen = Signal(object)  # Project, when it changes

    def __init__(self, finder: ProjectFinder, source=foreground_window, clock=time.monotonic,
                 parent: QObject | None = None):
        super().__init__(parent)
        self.finder = finder
        self._source = source
        self._clock = clock
        self._own_pid = os.getpid()
        self.last_work: FrontWindow | None = None
        self.last_work_at = -1e9
        self.last_project: Project | None = None
        self.last_project_at = -1e9
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self.sample)

    def set_active(self, active: bool) -> None:
        if active and not self._timer.isActive():
            self._timer.start()
            self.sample()
        elif not active:
            self._timer.stop()

    def sample(self) -> None:
        front = self._source()
        if front is None:
            return
        hwnd, exe, title, pid = front
        if pid == self._own_pid or not title.strip() or exe.lower() in IGNORED_APPS or title == "Program Manager":
            return
        project = None
        try:
            project = self.finder.find(exe, title)
        except Exception:  # a strange title or an unreadable folder: just no project this time
            project = None
        now = self._clock()
        self.last_work = FrontWindow(hwnd, exe, title, pid, project)
        self.last_work_at = now
        if project is not None:
            changed = project != self.last_project
            self.last_project, self.last_project_at = project, now
            if changed:
                self.project_seen.emit(project)

    def recent_project(self, max_age_s: float = 600.0) -> Project | None:
        if self.last_project is None or self._clock() - self.last_project_at > max_age_s:
            return None
        return self.last_project
