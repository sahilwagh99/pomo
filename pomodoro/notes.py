"""Notes: plain Markdown files in a folder (a vault), linked with [[wikilinks]] and #tags.

Nothing here is Pomo-only: a note is just `Title.md`, a link is `[[Title]]` (or `[[Title#Heading|shown
text]]`), and a tag is `#tag`, the way Obsidian and other Markdown apps write them, so the same folder
opens in those too. Pomo adds three kinds of notes of its own, in their own folders:

- Study/   a note for a step of your path, linked to its milestone and goal
- Tasks/   a note for a task you focus on
- Daily/   one note a day, `2026-11-26.md`, where Pomo keeps the day's focus sessions and parked thoughts
           between two comment lines and leaves everything else in the note alone

This module only reads and writes files (no Qt), so it's easy to test.
"""
from __future__ import annotations

import os
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

SUFFIX = ".md"
DAILY = "Daily"
STUDY = "Study"
TASKS = "Tasks"
TRASH = ".trash"
BLOCK_START = "<!-- pomo:start -->"
BLOCK_END = "<!-- pomo:end -->"
WELCOME = "Welcome"

LINK_RE = re.compile(r"(!?)\[\[([^\[\]\n]+?)\]\]")
# A tag starts after a space or punctuation (not inside a word, URL or "##"), with a letter or _
TAG_RE = re.compile(r"(?<![\w#/&])#([A-Za-z_][\w/-]*)")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
UNSAFE_NAME = re.compile(r'[\\/:*?"<>|#^\[\]\x00-\x1f]')
UNLINKABLE = re.compile(r"[\[\]|#^]")


def replace_file(tmp: Path, path: Path, attempts: int = 8) -> None:
    """Swap a freshly written file into place. Windows refuses while another program has the file open
    for a moment (antivirus, the search indexer, OneDrive, an editor), so try again briefly first."""
    for attempt in range(attempts):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.03 * (attempt + 1))


# ------------------------------------------------------------------ reading notes
def safe_name(title: str) -> str:
    """A file name for a title: characters Windows or links can't take become spaces."""
    name = re.sub(r"\s+", " ", UNSAFE_NAME.sub(" ", title)).strip(" .")
    return name[:120].rstrip(" .")


def link_target(inner: str) -> str:
    """The note a link points to: `Title#Heading|text` -> `Title`."""
    target = inner.split("|", 1)[0].split("#", 1)[0].strip()
    return target[:-len(SUFFIX)] if target.lower().endswith(SUFFIX) else target


def _prose(text: str) -> str:
    """The text without code blocks and inline code, where [[ and # don't mean links or tags."""
    out, fenced = [], False
    for line in text.split("\n"):
        if FENCE_RE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            out.append(INLINE_CODE_RE.sub(" ", line))
    return "\n".join(out)


def links(text: str) -> list[str]:
    """The notes a note links to, in order, once each (case doesn't matter, as in Obsidian)."""
    seen, out = set(), []
    for match in LINK_RE.finditer(_prose(text)):
        target = link_target(match.group(2))
        if target and target.lower() not in seen:
            seen.add(target.lower())
            out.append(target)
    return out


def tags(text: str) -> list[str]:
    seen, out = set(), []
    for line in _prose(text).split("\n"):
        if re.match(r"^\s{0,3}#{1,6}\s", line):  # the # of a heading isn't a tag, but tags in it are
            line = re.sub(r"^\s{0,3}#{1,6}\s", "", line)
        for match in TAG_RE.finditer(line):
            tag = match.group(1).rstrip("/-")
            if tag and tag.lower() not in seen:
                seen.add(tag.lower())
                out.append(tag)
    return out


def link_to(title: str) -> str:
    """A link to the note for title: `[[title]]`, or `[[file name|title]]` when the title has characters a
    file name can't take (the note for "Thesis: ch 3" is Thesis ch 3.md)."""
    title = title.strip()
    name = safe_name(title)
    if not name:
        return title
    shown = re.sub(r"\s+", " ", UNLINKABLE.sub(" ", title)).strip()
    return f"[[{name}]]" if shown == name else f"[[{name}|{shown}]]"


def snippet(text: str, word: str, width: int = 90) -> str:
    """The line around the first match of word, for search results."""
    flat = re.sub(r"\s+", " ", text).strip()
    at = flat.lower().find(word.lower()) if word else -1
    if at < 0:
        return flat[:width] + ("…" if len(flat) > width else "")
    start = max(0, at - width // 3)
    piece = flat[start:start + width]
    return ("…" if start else "") + piece + ("…" if start + width < len(flat) else "")


# ------------------------------------------------------------------ the vault
@dataclass
class Index:
    """Who links to whom, and the tags, across the vault."""
    notes: list[Path] = field(default_factory=list)
    outgoing: dict[Path, list[Path]] = field(default_factory=dict)    # links that lead to a note
    unresolved: dict[Path, list[str]] = field(default_factory=dict)   # links to notes not written yet
    backlinks: dict[Path, list[Path]] = field(default_factory=dict)
    tags: dict[str, list[Path]] = field(default_factory=dict)         # tag (lower case) -> notes
    tag_names: dict[str, str] = field(default_factory=dict)           # lower case -> as first written
    note_tags: dict[Path, list[str]] = field(default_factory=dict)


class Vault:
    def __init__(self, root: Path):
        self.root = Path(root)
        self._cache: dict[Path, tuple[int, int, str, str]] = {}  # path -> (mtime_ns, size, text, line ending)

    # ------------------------------------------------------------- files
    def ensure(self) -> bool:
        """Make the folder if it isn't there. True if it was just made (or is empty): time for a welcome."""
        fresh = not self.root.exists() or not any(self.root.iterdir())
        self.root.mkdir(parents=True, exist_ok=True)
        return fresh

    def notes(self) -> list[Path]:
        """Every note, skipping hidden folders such as .obsidian and .trash."""
        out = []
        if not self.root.is_dir():
            return out
        for folder, dirs, files in os.walk(self.root):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            out += [Path(folder) / f for f in files if f.lower().endswith(SUFFIX) and not f.startswith(".")]
        return sorted(out, key=lambda p: str(p).lower())

    def read(self, path: Path) -> str:
        try:
            stat = path.stat()
        except OSError:
            return ""
        cached = self._cache.get(path)
        if cached and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
            return cached[2]
        try:
            raw = path.read_bytes().decode("utf-8-sig", errors="replace")
        except OSError:
            return ""
        eol = "\r\n" if "\r\n" in raw else "\n"
        text = raw.replace("\r\n", "\n")
        self._cache[path] = (stat.st_mtime_ns, stat.st_size, text, eol)
        return text

    def write(self, path: Path, text: str) -> None:
        """Save, keeping the line endings the file had (new notes get \\n, like Obsidian's)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        eol = self._cache[path][3] if path in self._cache else "\n"
        tmp = path.with_name(f".{path.name}.pomo-tmp")
        tmp.write_bytes(text.replace("\r\n", "\n").replace("\n", eol).encode("utf-8"))
        replace_file(tmp, path)
        stat = path.stat()
        self._cache[path] = (stat.st_mtime_ns, stat.st_size, text, eol)

    def rel(self, path: Path) -> str:
        """`Study/NumPy basics` for root/Study/NumPy basics.md."""
        try:
            rel = path.relative_to(self.root).as_posix()
        except ValueError:
            rel = path.name
        return rel[:-len(SUFFIX)] if rel.lower().endswith(SUFFIX) else rel

    @staticmethod
    def title(path: Path) -> str:
        return path.stem

    def folder(self, path: Path) -> str:
        """The note's folder inside the vault ("" at the top)."""
        rel = self.rel(path)
        return rel.rsplit("/", 1)[0] if "/" in rel else ""

    def kind(self, path: Path) -> str:
        """"daily", "study", "task" or "note", from Pomo's folders."""
        top = self.folder(path).split("/", 1)[0].lower()
        return {DAILY.lower(): "daily", STUDY.lower(): "study", TASKS.lower(): "task"}.get(top, "note")

    # ------------------------------------------------------------- finding notes
    def resolve(self, target: str, notes: list[Path] | None = None) -> Path | None:
        """The note a link points to, like Obsidian: by path, else by name (the shallowest one wins)."""
        want = link_target(target).replace("\\", "/").strip().lower()
        if not want:
            return None
        notes = self.notes() if notes is None else notes
        for path in notes:
            if self.rel(path).lower() == want:
                return path
        name = want.rsplit("/", 1)[-1]
        found = [p for p in notes if p.stem.lower() == name and ("/" not in want or self.rel(p).lower().endswith(want))]
        return min(found, key=lambda p: (len(p.relative_to(self.root).parts), str(p).lower())) if found else None

    def new_path(self, title: str, folder: str = "") -> Path:
        """Where a new note with this title goes: `Title.md`, or `Title 1.md` when that's taken."""
        name = safe_name(title) or "Untitled"
        base = self.root / folder if folder else self.root
        path, n = base / f"{name}{SUFFIX}", 1
        while path.exists():
            path, n = base / f"{name} {n}{SUFFIX}", n + 1
        return path

    def create(self, title: str, folder: str = "", text: str = "") -> Path:
        path = self.new_path(title, folder)
        self.write(path, text)
        return path

    def open_or_create(self, title: str, folder: str = "", seed: str = "") -> Path:
        """The note for this title wherever it is (by its file name), or a new one in folder."""
        return self.resolve(safe_name(title)) or self.create(title, folder, seed)

    def rename(self, path: Path, title: str) -> tuple[Path, int]:
        """Rename a note and fix the links to it in other notes. Returns the new path and the notes fixed."""
        name = safe_name(title)
        if not name:
            raise ValueError("A note needs a name.")
        new = path.with_name(f"{name}{SUFFIX}")
        if new == path:
            return path, 0
        if new.exists() and new.name.lower() != path.name.lower():  # a case-only rename is fine
            raise FileExistsError(f"There's already a note called {name}.")
        old_names = {path.stem.lower(), self.rel(path).lower()}
        notes = [p for p in self.notes() if p != path]
        path.rename(new)
        self._cache.pop(path, None)
        fixed = 0
        for note in [new, *notes]:
            text = self.read(note)
            changed = self.relink(text, old_names, name)
            if changed != text:
                self.write(note, changed)
                fixed += note != new
        return new, fixed

    @staticmethod
    def relink(text: str, old_names: set[str], new_title: str) -> str:
        """Point [[old]] links (keeping #heading and |text) at new_title."""
        def swap(match: re.Match) -> str:
            inner = match.group(2)
            if link_target(inner).replace("\\", "/").lower() not in old_names:
                return match.group(0)
            rest = inner[len(inner.split("|", 1)[0].split("#", 1)[0]):]
            return f"{match.group(1)}[[{new_title}{rest}]]"
        return LINK_RE.sub(swap, text)

    def trash(self, path: Path) -> Path:
        """Move a note into the vault's .trash folder (Obsidian's too), where it can be got back."""
        bin_ = self.root / TRASH
        bin_.mkdir(parents=True, exist_ok=True)
        dest, n = bin_ / path.name, 1
        while dest.exists():
            dest, n = bin_ / f"{path.stem} {n}{path.suffix}", n + 1
        shutil.move(str(path), str(dest))
        self._cache.pop(path, None)
        return dest

    # ------------------------------------------------------------- the whole vault
    def index(self) -> Index:
        idx = Index(notes=self.notes())
        by_name: dict[str, Path] = {}
        for path in idx.notes:  # the shallowest note with a name wins, as in resolve()
            key = path.stem.lower()
            if key not in by_name or len(path.parts) < len(by_name[key].parts):
                by_name[key] = path
        rels = {self.rel(p).lower(): p for p in idx.notes}
        for path in idx.notes:
            text = self.read(path)
            out, missing = [], []
            for target in links(text):
                want = target.replace("\\", "/").lower()
                found = rels.get(want) or (by_name.get(want) if "/" not in want else self.resolve(target, idx.notes))
                if found is None:
                    missing.append(target)
                elif found != path and found not in out:
                    out.append(found)
            idx.outgoing[path], idx.unresolved[path] = out, missing
            for target in out:
                idx.backlinks.setdefault(target, []).append(path)
            idx.note_tags[path] = tags(text)
            for tag in idx.note_tags[path]:
                idx.tags.setdefault(tag.lower(), []).append(path)
                idx.tag_names.setdefault(tag.lower(), tag)
        return idx

    def search(self, query: str, limit: int = 60) -> list[tuple[Path, str]]:
        """Notes matching every word of query (in the title or the text), titles first. `#tag` finds a tag."""
        query = query.strip()
        if not query:
            return []
        words = query.lower().split()
        hits = []
        for path in self.notes():
            text = self.read(path)
            title = self.rel(path).lower()
            if query.startswith("#") and len(words) == 1:
                if words[0][1:] not in {t.lower() for t in tags(text)}:
                    continue
                hits.append((0, path, snippet(text, query)))
                continue
            body = text.lower()
            if all(w in title or w in body for w in words):
                in_title = all(w in title for w in words)
                hits.append((0 if in_title else 1, path, snippet(text, words[0])))
        hits.sort(key=lambda h: (h[0], h[1].stem.lower()))
        return [(path, snip) for _rank, path, snip in hits[:limit]]


# ------------------------------------------------------------------ Pomo's notes
def welcome_text() -> str:
    return (
        "Notes are plain Markdown files in this folder, so any Markdown app (Obsidian, for one) can open it too.\n\n"
        "- Link notes with double brackets: [[My first note]]. Ctrl+click a link to open it, even before it "
        "exists.\n"
        "- Tag with a hash: #idea\n"
        "- **Bold**, *italic*, ~~struck through~~ and `code` work as you type.\n"
        "- [ ] A checklist item (Ctrl+Enter ticks it)\n\n"
        "The **Graph** shows how your notes link up. Pomo keeps a daily note in Daily/, and a note for each step "
        "of your path in Study/.\n"
    )


def step_seed(goal: str, milestone: str) -> str:
    """A step's note starts linked to its milestone and goal, so the graph shows where it belongs."""
    parts = [f"Part of {link_to(milestone)}" if milestone else "", f"on the way to {link_to(goal)}" if goal else ""]
    line = ", ".join(p for p in parts if p)
    return f"{line[0].upper()}{line[1:]}.\n\n" if line else ""


def daily_path(vault: Vault, day: date) -> Path:
    return vault.root / DAILY / f"{day.isoformat()}{SUFFIX}"


def _minutes(seconds: int) -> str:
    minutes = max(1, round(seconds / 60))
    return f"{minutes // 60} h {minutes % 60:02d} min" if minutes >= 60 else f"{minutes} min"


def daily_block(sessions, thoughts) -> str:
    """The part of a daily note Pomo writes: the day's focus sessions and parked thoughts."""
    lines = [BLOCK_START]
    focus = [s for s in sessions if s.mode == "focus" and s.actual_sec >= 60]
    if focus:
        lines.append(f"### Focus: {_minutes(sum(s.actual_sec for s in focus))}")
        feel = {1: " · tough", 2: " · okay", 3: " · great"}
        for s in focus:
            on = f" on {link_to(s.task)}" if s.task.strip() else ""
            stopped = "" if s.completed else " · stopped early"
            lines.append(f"- {s.started_at:%H:%M} · {_minutes(s.actual_sec)}{on}{feel.get(s.rating, '')}{stopped}")
    if thoughts:
        lines.append("### Parked thoughts")
        lines += [f"- ~~{t.text}~~" if t.done else f"- {t.text}" for t in thoughts]
    if len(lines) == 1:
        lines.append("*Pomo adds your focus sessions and parked thoughts here as the day goes.*")
    lines.append(BLOCK_END)
    return "\n".join(lines)


def with_block(text: str, block: str) -> str:
    """The note with Pomo's part swapped in, or added (on top of a new note, at the end of yours)."""
    start, end = text.find(BLOCK_START), text.find(BLOCK_END)
    if start >= 0 and end > start:
        return text[:start] + block + text[end + len(BLOCK_END):]
    if not text.strip():
        return f"{block}\n\n"
    return f"{text.rstrip()}\n\n{block}\n"


def block_span(text: str) -> tuple[int, int] | None:
    """Where Pomo's part is in a note (start, end), if it has one."""
    start, end = text.find(BLOCK_START), text.find(BLOCK_END)
    return (start, end + len(BLOCK_END)) if start >= 0 and end > start else None
