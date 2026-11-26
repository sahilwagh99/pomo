"""The notes window: Markdown notes that format as you type, [[links]] between them, and a graph of it all.

The files live in a vault folder (see notes.py), so whatever you write here is plain Markdown that
Obsidian, VS Code or Notepad open as well. This window only shows and edits them.
"""
from __future__ import annotations

import math
import random
import re
from datetime import date, datetime
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QPointF, QRectF, QSize, QStringListModel, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QFontMetricsF,
    QKeySequence,
    QPainter,
    QPen,
    QShortcut,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QCompleter,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from . import notes
from .pomo import Pomo
from .theme import mode_color
from .winutil import set_dark_title_bar

PATH_ROLE = Qt.UserRole
SUB_ROLE = Qt.UserRole + 1
SAVE_DELAY_MS = 600
REFRESH_DELAY_MS = 900
BASE_PT = 11.5
HEADING_SCALE = (1.75, 1.45, 1.25, 1.12, 1.05, 1.0)
LIST_RE = re.compile(r"^(\s*)([-*+]|(\d+)([.)]))(\s+)(\[([ xX])\]\s+)?")
URL_LINK_RE = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")
BARE_URL_RE = re.compile(r"https?://[^\s)>\]]+")


def _alpha(color: str | QColor, alpha: int) -> QColor:
    c = QColor(color)
    c.setAlpha(alpha)
    return c


def _when(path: Path) -> str:
    try:
        moment = datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return ""
    days = (date.today() - moment.date()).days
    if days == 0:
        return f"today {moment:%H:%M}"
    if days == 1:
        return "yesterday"
    return f"{moment:%b} {moment.day}" + (f", {moment.year}" if moment.year != date.today().year else "")


# ------------------------------------------------------------------ the editor
class MarkdownHighlighter(QSyntaxHighlighter):
    """Formats Markdown as you type, with the marks (**, ~~, [[ ]]) faded so the words stand out."""

    CODE, COMMENT = 1, 2

    def __init__(self, document, theme, accent: QColor):
        super().__init__(document)
        self.known: set[str] = set()  # note names (lower case), so links to notes not written yet look fainter
        self.set_theme(theme, accent)

    def set_theme(self, theme, accent: QColor) -> None:
        def fmt(color=None, *, bold=False, italic=False, strike=False, bg=None, pt=None, mono=False):
            f = QTextCharFormat()
            if color is not None:
                f.setForeground(QColor(color))
            if bold:
                f.setFontWeight(QFont.DemiBold)
            if italic:
                f.setFontItalic(True)
            if strike:
                f.setFontStrikeOut(True)
            if bg is not None:
                f.setBackground(bg)
            if pt is not None:
                f.setFontPointSize(pt)
            if mono:
                f.setFontFamilies(["Cascadia Mono", "Consolas", "Courier New"])
            return f

        link = QColor(theme.focus if theme.dark else theme.chart)  # the brighter pastel reads better on dark
        self.f_mark = fmt(theme.muted)
        self.f_heading = [fmt(theme.ink, bold=True, pt=BASE_PT * s) for s in HEADING_SCALE]
        self.f_quote = fmt(theme.ink_2, italic=True)
        self.f_code = fmt(theme.ink, bg=QColor(theme.hover), mono=True)
        self.f_link = fmt(link)
        self.f_ghost = fmt(_alpha(link, 150))
        self.f_url = fmt(link)
        self.f_url.setFontUnderline(True)
        self.f_tag = fmt(link, bg=_alpha(link, 28))
        self.f_bold = fmt(bold=True)
        self.f_italic = fmt(italic=True)
        self.f_strike = fmt(theme.ink_2, strike=True)
        self.f_highlight = fmt(bg=_alpha(accent, 90))
        self.f_bullet = fmt(link, bold=True)
        self.f_done = fmt(theme.muted, strike=True)
        self.f_comment = fmt(theme.muted, italic=True)
        self.rehighlight()

    # ------------------------------------------------------------- one line
    def _merge(self, start: int, length: int, extra: QTextCharFormat) -> None:
        for i in range(max(0, start), start + length):
            f = self.format(i)
            f.merge(extra)
            self.setFormat(i, 1, f)

    def highlightBlock(self, text: str) -> None:
        prev = self.previousBlockState()
        self.setCurrentBlockState(0)
        if prev == self.CODE:
            self.setFormat(0, len(text), self.f_code)
            if not notes.FENCE_RE.match(text):
                self.setCurrentBlockState(self.CODE)
            else:
                self._merge(0, len(text), self.f_mark)
            return
        if notes.FENCE_RE.match(text):
            self.setFormat(0, len(text), self.f_code)
            self._merge(0, len(text), self.f_mark)
            self.setCurrentBlockState(self.CODE)
            return
        start = 0
        if prev == self.COMMENT:
            end = text.find("-->")
            if end < 0:
                self.setFormat(0, len(text), self.f_comment)
                self.setCurrentBlockState(self.COMMENT)
                return
            self.setFormat(0, end + 3, self.f_comment)
            start = end + 3

        heading = re.match(r"^(#{1,6})(\s+)", text)
        if heading:
            level = len(heading.group(1))
            self.setFormat(0, len(text), self.f_heading[level - 1])
            self._merge(0, level, self.f_mark)
        elif re.match(r"^\s*([-*_])(\s*\1){2,}\s*$", text):
            self.setFormat(0, len(text), self.f_mark)
            return
        else:
            quote = re.match(r"^(\s*>+)", text)
            if quote:
                self.setFormat(0, len(text), self.f_quote)
                self._merge(0, quote.end(), self.f_mark)
            item = LIST_RE.match(text)
            if item:
                marker = item.start(2)
                self._merge(marker, len(item.group(2)), self.f_bullet)
                if item.group(6):
                    box = item.start(6)
                    self._merge(box, 3, self.f_mark)
                    if item.group(7) in "xX":
                        self._merge(item.end(), len(text) - item.end(), self.f_done)

        code_spans = [(m.start(), m.end()) for m in notes.INLINE_CODE_RE.finditer(text) if m.start() >= start]

        def free(a: int, b: int) -> bool:
            return a >= start and all(b <= s or a >= e for s, e in code_spans)

        for s, e in code_spans:
            self._merge(s, e - s, self.f_code)
            self._merge(s, 1, self.f_mark)
            self._merge(e - 1, 1, self.f_mark)
        for m in re.finditer(r"<!--.*?(-->|$)", text):
            if free(m.start(), m.end()):
                self.setFormat(m.start(), m.end() - m.start(), self.f_comment)
                if not m.group(1):
                    self.setCurrentBlockState(self.COMMENT)
        for m in notes.LINK_RE.finditer(text):
            if not free(m.start(), m.end()):
                continue
            target = notes.link_target(m.group(2))
            known = target.lower().rsplit("/", 1)[-1] in self.known
            self._merge(m.start(), m.end() - m.start(), self.f_link if known else self.f_ghost)
            self._merge(m.start(), 2 + len(m.group(1)), self.f_mark)
            self._merge(m.end() - 2, 2, self.f_mark)
            bar = m.group(2).find("|")
            if bar >= 0:  # [[target|shown text]]: fade the target, show the text
                self._merge(m.start() + 2 + len(m.group(1)), bar + 1, self.f_mark)
        for m in URL_LINK_RE.finditer(text):
            if free(m.start(), m.end()):
                self._merge(m.start(1), len(m.group(1)), self.f_url)
                self._merge(m.start(), 1, self.f_mark)
                self._merge(m.end(1), m.end() - m.end(1), self.f_mark)
        for m in BARE_URL_RE.finditer(text):
            if free(m.start(), m.end()):
                self._merge(m.start(), m.end() - m.start(), self.f_url)
        for pattern, f, mark in ((r"\*\*(?=\S)(.+?)(?<=\S)\*\*", self.f_bold, 2),
                                 (r"__(?=\S)(.+?)(?<=\S)__", self.f_bold, 2),
                                 (r"(?<![*\w])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![*\w])", self.f_italic, 1),
                                 (r"(?<![_\w])_(?=[^\s_])(.+?)(?<=[^\s_])_(?![_\w])", self.f_italic, 1),
                                 (r"~~(?=\S)(.+?)(?<=\S)~~", self.f_strike, 2),
                                 (r"==(?=\S)(.+?)(?<=\S)==", self.f_highlight, 2)):
            for m in re.finditer(pattern, text):
                if free(m.start(), m.end()):
                    self._merge(m.start(), m.end() - m.start(), f)
                    self._merge(m.start(), mark, self.f_mark)
                    self._merge(m.end() - mark, mark, self.f_mark)
        if not heading:
            for m in notes.TAG_RE.finditer(text):
                if free(m.start(), m.end()):
                    self._merge(m.start(), m.end() - m.start(), self.f_tag)
        else:
            for m in notes.TAG_RE.finditer(text, heading.end()):
                if free(m.start(), m.end()):
                    self._merge(m.start(), m.end() - m.start(), self.f_tag)


class NoteEditor(QPlainTextEdit):
    """Plain Markdown, typed comfortably: lists continue on Enter, [[ suggests notes, Ctrl+click follows links."""

    link_activated = Signal(str)  # a [[link]]'s target
    tag_activated = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("editor")
        font = QFont("Segoe UI")
        font.setPointSizeF(BASE_PT)
        self.setFont(font)
        self.setTabStopDistance(QFontMetricsF(font).horizontalAdvance(" ") * 4)
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.setFrameShape(QFrame.NoFrame)
        self.setMouseTracking(True)
        self.document().setDocumentMargin(4)
        self._press_pos = None
        self.model = QStringListModel(self)
        self.completer = QCompleter(self.model, self)
        self.completer.setWidget(self)
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchContains)
        self.completer.setCompletionMode(QCompleter.PopupCompletion)
        self.completer.setMaxVisibleItems(8)
        self.completer.activated.connect(self._complete)

    def set_titles(self, titles: list[str]) -> None:
        self.model.setStringList(sorted(set(titles), key=str.lower))

    # ------------------------------------------------------------- keys
    def keyPressEvent(self, event) -> None:
        popup = self.completer.popup()
        key, mods = event.key(), event.modifiers()
        if popup.isVisible() and key in (Qt.Key_Enter, Qt.Key_Return, Qt.Key_Escape, Qt.Key_Tab, Qt.Key_Backtab):
            event.ignore()  # the completer takes these
            return
        ctrl = bool(mods & Qt.ControlModifier)
        shift = bool(mods & Qt.ShiftModifier)
        if ctrl and key in (Qt.Key_Return, Qt.Key_Enter):
            self.toggle_checkbox()
            return
        if ctrl and not shift and key == Qt.Key_B:
            self.wrap("**")
            return
        if ctrl and not shift and key == Qt.Key_I:
            self.wrap("*")
            return
        if ctrl and shift and key == Qt.Key_X:
            self.wrap("~~")
            return
        if key in (Qt.Key_Return, Qt.Key_Enter) and not mods and self._continue_list():
            return
        if key == Qt.Key_Tab and not mods and self._indent(1):
            return
        if key == Qt.Key_Backtab and self._indent(-1):
            return
        super().keyPressEvent(event)
        if event.text() or key == Qt.Key_Backspace:
            self._suggest()

    def _continue_list(self) -> bool:
        cursor = self.textCursor()
        if cursor.hasSelection():
            return False
        line = cursor.block().text()
        item = LIST_RE.match(line)
        if not item or cursor.positionInBlock() < item.end():
            return False
        if not line[item.end():].strip():  # Enter on an empty item ends the list
            cursor.movePosition(QTextCursor.StartOfBlock)
            cursor.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
            cursor.insertText("")
            self.setTextCursor(cursor)
            return True
        indent, marker = item.group(1), item.group(2)
        if item.group(3):
            marker = f"{int(item.group(3)) + 1}{item.group(4)}"
        box = "[ ] " if item.group(6) else ""
        cursor.insertText(f"\n{indent}{marker}{item.group(5)}{box}")
        self.setTextCursor(cursor)
        self.ensureCursorVisible()
        return True

    def _indent(self, step: int) -> bool:
        cursor = self.textCursor()
        if not LIST_RE.match(cursor.block().text()):
            return False
        line_start = cursor.block().position()
        edit = QTextCursor(self.document())
        edit.setPosition(line_start)
        if step > 0:
            edit.insertText("\t")
        else:
            text = cursor.block().text()
            cut = 1 if text.startswith("\t") else min(4, len(text) - len(text.lstrip(" ")))
            if not cut:
                return True
            edit.setPosition(line_start + cut, QTextCursor.KeepAnchor)
            edit.removeSelectedText()
        return True

    def toggle_checkbox(self) -> None:
        cursor = self.textCursor()
        line = cursor.block().text()
        item = LIST_RE.match(line)
        edit = QTextCursor(cursor.block())
        if item and item.group(6):
            at = cursor.block().position() + item.start(7)
            edit.setPosition(at)
            edit.setPosition(at + 1, QTextCursor.KeepAnchor)
            edit.insertText(" " if item.group(7) in "xX" else "x")
        elif item:
            edit.setPosition(cursor.block().position() + item.end())
            edit.insertText("[ ] ")
        else:
            lead = len(line) - len(line.lstrip())
            edit.setPosition(cursor.block().position() + lead)
            edit.insertText("- [ ] ")

    def wrap(self, mark: str) -> None:
        cursor = self.textCursor()
        if not cursor.hasSelection():
            cursor.insertText(mark + mark)
            cursor.movePosition(QTextCursor.Left, QTextCursor.MoveAnchor, len(mark))
            self.setTextCursor(cursor)
            return
        text = cursor.selectedText()
        start = cursor.selectionStart()
        if text.startswith(mark) and text.endswith(mark) and len(text) >= 2 * len(mark):
            new = text[len(mark):-len(mark)]
        else:
            new = f"{mark}{text}{mark}"
        cursor.insertText(new)
        cursor.setPosition(start)
        cursor.setPosition(start + len(new), QTextCursor.KeepAnchor)
        self.setTextCursor(cursor)

    # ------------------------------------------------------------- [[ suggestions
    def _link_prefix(self) -> str | None:
        cursor = self.textCursor()
        before = cursor.block().text()[:cursor.positionInBlock()]
        at = before.rfind("[[")
        if at < 0 or "]]" in before[at:]:
            return None
        prefix = before[at + 2:]
        return None if any(c in prefix for c in "|#^[]") or len(prefix) > 80 else prefix

    def _suggest(self) -> None:
        prefix = self._link_prefix()
        popup = self.completer.popup()
        if prefix is None:
            popup.hide()
            return
        self.completer.setCompletionPrefix(prefix)
        if self.completer.completionCount() == 0:
            popup.hide()
            return
        popup.setCurrentIndex(self.completer.completionModel().index(0, 0))
        rect = self.cursorRect()
        rect.setWidth(max(240, popup.sizeHintForColumn(0) + popup.verticalScrollBar().sizeHint().width() + 24))
        self.completer.complete(rect)

    def _complete(self, title: str) -> None:
        prefix = self._link_prefix()
        if prefix is None:
            return
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.Left, QTextCursor.KeepAnchor, len(prefix))
        cursor.insertText(title)
        if cursor.block().text()[cursor.positionInBlock():].startswith("]]"):
            cursor.movePosition(QTextCursor.Right, QTextCursor.MoveAnchor, 2)
        else:
            cursor.insertText("]]")
        self.setTextCursor(cursor)

    # ------------------------------------------------------------- following links
    def _thing_at(self, pos) -> tuple[str, str] | None:
        """("note", target), ("url", url), ("tag", tag) or ("box", "") under the mouse."""
        cursor = self.cursorForPosition(pos)
        block, col = cursor.block(), cursor.positionInBlock()
        text = block.text()
        for m in notes.LINK_RE.finditer(text):
            if m.start() <= col <= m.end():
                return "note", notes.link_target(m.group(2))
        for m in URL_LINK_RE.finditer(text):
            if m.start() <= col <= m.end():
                return "url", m.group(2)
        for m in BARE_URL_RE.finditer(text):
            if m.start() <= col <= m.end():
                return "url", m.group(0)
        for m in notes.TAG_RE.finditer(text):
            if m.start() <= col <= m.end():
                return "tag", m.group(1)
        item = LIST_RE.match(text)
        if item and item.group(6) and item.start(6) <= col <= item.start(6) + 3:
            return "box", ""
        return None

    def mousePressEvent(self, event) -> None:
        self._press_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        pos = event.position().toPoint()
        moved = self._press_pos is None or (pos - self._press_pos).manhattanLength() > 3
        if moved or event.button() != Qt.LeftButton or self.textCursor().hasSelection():
            return
        thing = self._thing_at(pos)
        if thing is None:
            return
        kind, value = thing
        if kind == "box":
            self.setTextCursor(self.cursorForPosition(pos))
            self.toggle_checkbox()
        elif event.modifiers() & Qt.ControlModifier:
            if kind == "note":
                self.link_activated.emit(value)
            elif kind == "url":
                QDesktopServices.openUrl(QUrl(value))
            elif kind == "tag":
                self.tag_activated.emit(value)

    def mouseMoveEvent(self, event) -> None:
        super().mouseMoveEvent(event)
        thing = self._thing_at(event.position().toPoint())
        hand = thing is not None and (thing[0] == "box" or event.modifiers() & Qt.ControlModifier)
        self.viewport().setCursor(Qt.PointingHandCursor if hand else Qt.IBeamCursor)
        if thing is not None and thing[0] != "box":
            self.setToolTip({"note": "Ctrl+click to open", "url": "Ctrl+click to open in your browser",
                             "tag": "Ctrl+click to find this tag"}[thing[0]])
        else:
            self.setToolTip("")

    def replace_text(self, new: str) -> None:
        """Swap in new text with one minimal edit, so your cursor and undo stay put."""
        old = self.toPlainText()
        if old == new:
            return
        head = 0
        while head < min(len(old), len(new)) and old[head] == new[head]:
            head += 1
        tail = 0
        while tail < min(len(old), len(new)) - head and old[-1 - tail] == new[-1 - tail]:
            tail += 1
        cursor = QTextCursor(self.document())
        cursor.setPosition(head)
        cursor.setPosition(len(old) - tail, QTextCursor.KeepAnchor)
        cursor.insertText(new[head:len(new) - tail])


# ------------------------------------------------------------------ the graph
class GraphView(QWidget):
    """Notes as bubbles and links as lines, settling into place like a little galaxy. Click to open."""

    open_requested = Signal(str)

    def __init__(self, ctrl, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.setMouseTracking(True)
        self.setMinimumSize(300, 240)
        self.nodes: list[dict] = []
        self.edges: list[tuple[int, int]] = []
        self.neighbors: list[set[int]] = []
        self.current = ""
        self.hover: int | None = None
        self.scale = 1.0
        self.offset = QPointF(0, 0)
        self.heat = 0.0
        self._fit_pending = True
        self._drag: tuple[str, int | None, QPointF] | None = None
        self._moved = False
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)
        self.pomo = Pomo()

    # ------------------------------------------------------------- data
    def set_graph(self, nodes: list[dict], edges: list[tuple[str, str]], current: str) -> None:
        """nodes: {"key", "title", "kind"}; edges: key pairs. Bubbles already on screen keep their place."""
        old = {n["key"]: n for n in self.nodes}
        rng = random.Random(len(nodes))
        out = []
        for n in nodes:
            prev = old.get(n["key"])
            if prev:
                out.append({**n, "x": prev["x"], "y": prev["y"], "vx": 0.0, "vy": 0.0})
            else:
                angle, radius = rng.uniform(0, 2 * math.pi), rng.uniform(20, 60 + 8 * math.sqrt(len(nodes)))
                out.append({**n, "x": radius * math.cos(angle), "y": radius * math.sin(angle), "vx": 0.0, "vy": 0.0})
        index = {n["key"]: i for i, n in enumerate(out)}
        pairs = set()
        for a, b in edges:
            if a in index and b in index and a != b:
                pairs.add((min(index[a], index[b]), max(index[a], index[b])))
        self.nodes, self.edges = out, sorted(pairs)
        self.neighbors = [set() for _ in out]
        for a, b in self.edges:
            self.neighbors[a].add(b)
            self.neighbors[b].add(a)
        for i, n in enumerate(out):
            n["deg"] = len(self.neighbors[i])
            n["r"] = 4.5 + min(11.0, math.sqrt(n["deg"]) * 2.4)
        self.current = current
        if not old:
            self._fit_pending = True
        self.hover = None
        self.heat = 1.0 if len(old) != len(out) or not old else max(self.heat, 0.3)
        self.timer.start()
        self.update()

    def set_current(self, key: str) -> None:
        self.current = key
        self.update()

    # ------------------------------------------------------------- the simulation
    def _tick(self) -> None:
        nodes, n = self.nodes, len(self.nodes)
        if n == 0 or self.heat < 0.01:
            self.timer.stop()
            if self._fit_pending and n:
                self.fit()
            return
        ideal = 70.0
        fx = [0.0] * n
        fy = [0.0] * n
        xs = [p["x"] for p in nodes]
        ys = [p["y"] for p in nodes]
        for i in range(n):  # everything pushes everything else away...
            xi, yi = xs[i], ys[i]
            for j in range(i + 1, n):
                dx, dy = xi - xs[j], yi - ys[j]
                d2 = dx * dx + dy * dy
                if d2 > 250000:
                    continue
                d2 = max(d2, 1.0)
                f = ideal * ideal / d2
                fx[i] += dx * f * 0.08
                fy[i] += dy * f * 0.08
                fx[j] -= dx * f * 0.08
                fy[j] -= dy * f * 0.08
        for a, b in self.edges:  # ...links pull their notes together...
            dx, dy = xs[b] - xs[a], ys[b] - ys[a]
            d = math.hypot(dx, dy) or 1.0
            f = (d - ideal) * 0.04 / d
            fx[a] += dx * f
            fy[a] += dy * f
            fx[b] -= dx * f
            fy[b] -= dy * f
        dragged = self._drag[1] if self._drag and self._drag[0] == "node" else None
        for i, p in enumerate(nodes):  # ...and a gentle pull keeps the whole thing together
            if i == dragged:
                continue
            fx[i] -= p["x"] * 0.012
            fy[i] -= p["y"] * 0.012
            p["vx"] = (p["vx"] + fx[i] * self.heat) * 0.82
            p["vy"] = (p["vy"] + fy[i] * self.heat) * 0.82
            speed = math.hypot(p["vx"], p["vy"])
            if speed > 30:
                p["vx"], p["vy"] = p["vx"] * 30 / speed, p["vy"] * 30 / speed
            p["x"] += p["vx"]
            p["y"] += p["vy"]
        self.heat *= 0.985 if n < 300 else 0.96
        if self._fit_pending and self.heat < 0.25:
            self.fit()
        self.update()

    def fit(self) -> None:
        self._fit_pending = False
        if not self.nodes:
            return
        xs = [p["x"] for p in self.nodes]
        ys = [p["y"] for p in self.nodes]
        w, h = max(xs) - min(xs) + 80, max(ys) - min(ys) + 80
        self.scale = max(0.25, min(1.6, min(self.width() / w, self.height() / h)))
        self.offset = QPointF(-(max(xs) + min(xs)) / 2 * self.scale, -(max(ys) + min(ys)) / 2 * self.scale)
        self.update()

    # ------------------------------------------------------------- geometry
    def _screen(self, p: dict) -> QPointF:
        return QPointF(self.width() / 2 + self.offset.x() + p["x"] * self.scale,
                       self.height() / 2 + self.offset.y() + p["y"] * self.scale)

    def _world(self, pos: QPointF) -> QPointF:
        return QPointF((pos.x() - self.width() / 2 - self.offset.x()) / self.scale,
                       (pos.y() - self.height() / 2 - self.offset.y()) / self.scale)

    def _node_at(self, pos: QPointF) -> int | None:
        best, best_d = None, 1e9
        for i, p in enumerate(self.nodes):
            spot = self._screen(p)
            d = math.hypot(spot.x() - pos.x(), spot.y() - pos.y())
            if d <= max(8.0, p["r"] * self.scale + 4) and d < best_d:
                best, best_d = i, d
        return best

    # ------------------------------------------------------------- painting
    def _color(self, kind: str) -> QColor:
        t = self.ctrl.theme
        return QColor({"daily": t.short, "study": t.long, "task": t.long, "ghost": t.surface}.get(
            kind, mode_color(t, self.ctrl.settings, "focus").name()))

    def paintEvent(self, _event) -> None:
        t = self.ctrl.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(t.surface))
        if not self.nodes:
            p.setPen(QColor(t.muted))
            p.drawText(self.rect(), Qt.AlignCenter, "No notes yet. Write one, and link it to another with [[ ]].")
            p.end()
            return
        lit = None if self.hover is None else {self.hover, *self.neighbors[self.hover]}
        for a, b in self.edges:
            on = lit is not None and a in lit and b in lit and self.hover in (a, b)
            color = QColor(t.chart) if on else _alpha(t.axis, 60 if lit is not None else 255)
            p.setPen(QPen(color, 1.6 if on else 1.1))
            p.drawLine(self._screen(self.nodes[a]), self._screen(self.nodes[b]))
        font = QFont(self.font())
        font.setPixelSize(11)
        p.setFont(font)
        fm = QFontMetricsF(font)
        current_at = None
        for i, node in enumerate(self.nodes):
            c = self._screen(node)
            r = node["r"] * max(0.6, min(1.4, self.scale))
            faded = lit is not None and i not in lit
            if node["kind"] == "ghost":
                p.setPen(QPen(_alpha(t.muted, 90 if faded else 220), 1.2, Qt.DashLine))
                p.setBrush(QColor(t.surface))
            else:
                p.setPen(QPen(_alpha(QColor(t.ink), 40 if faded else 70), 1))
                p.setBrush(_alpha(self._color(node["kind"]), 70 if faded else 255))
            p.drawEllipse(c, r, r)
            if node["key"] == self.current:
                p.setPen(QPen(QColor(t.chart), 2))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(c, r + 4, r + 4)
                current_at = (c, r)
            show_label = (self.scale > 0.85 or node["deg"] >= 4 or i == self.hover or node["key"] == self.current
                          or (lit is not None and i in lit))
            if show_label and not faded:
                title = fm.elidedText(node["title"], Qt.ElideRight, 160)
                width = fm.horizontalAdvance(title)
                p.setPen(QColor(t.ink if i == self.hover else t.ink_2))
                p.drawText(QPointF(c.x() - width / 2, c.y() + r + 13), title)
        if current_at is not None:  # Pomo sits on the note you have open
            c, r = current_at
            self.pomo.paint(p, QPointF(c.x(), c.y() - r - 13), 24, ground_shadow=False)
        self._legend(p, fm)
        p.end()

    def _legend(self, p: QPainter, fm: QFontMetricsF) -> None:
        t = self.ctrl.theme
        x, y = 14.0, self.height() - 14.0
        for kind, label in (("note", "note"), ("study", "study and task"), ("daily", "daily"),
                            ("ghost", "linked, not written yet")):
            if not any(n["kind"] == kind or (kind == "study" and n["kind"] == "task") for n in self.nodes):
                continue
            if kind == "ghost":
                p.setPen(QPen(QColor(t.muted), 1.2, Qt.DashLine))
                p.setBrush(QColor(t.surface))
            else:
                p.setPen(Qt.NoPen)
                p.setBrush(self._color(kind))
            p.drawEllipse(QPointF(x + 5, y - 4), 5, 5)
            p.setPen(QColor(t.muted))
            p.drawText(QPointF(x + 14, y), label)
            x += 14 + fm.horizontalAdvance(label) + 16

    # ------------------------------------------------------------- mouse
    def mousePressEvent(self, event) -> None:
        pos = event.position()
        hit = self._node_at(pos)
        self._drag = ("node" if hit is not None else "pan", hit, pos)
        self._moved = False

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        if self._drag is not None:
            kind, hit, last = self._drag
            if (pos - last).manhattanLength() > 2:
                self._moved = True
            if kind == "node" and self._moved:
                world = self._world(pos)
                node = self.nodes[hit]
                node["x"], node["y"], node["vx"], node["vy"] = world.x(), world.y(), 0.0, 0.0
                self.heat = max(self.heat, 0.25)
                self.timer.start()
            elif kind == "pan" and self._moved:
                self.offset += pos - last
                self._drag = (kind, hit, pos)
            self.update()
            return
        hover = self._node_at(pos)
        if hover != self.hover:
            self.hover = hover
            self.setCursor(Qt.PointingHandCursor if hover is not None else Qt.ArrowCursor)
            if hover is not None:
                node = self.nodes[hover]
                self.setToolTip(node["title"] + ("\nNot written yet: click to start it" if node["kind"] == "ghost" else ""))
            else:
                self.setToolTip("")
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if self._drag is not None:
            kind, hit, _ = self._drag
            if kind == "node" and not self._moved and hit is not None:
                self.open_requested.emit(self.nodes[hit]["key"])
        self._drag = None

    def mouseDoubleClickEvent(self, event) -> None:
        if self._node_at(event.position()) is None:
            self.fit()

    def wheelEvent(self, event) -> None:
        pos = event.position()
        before = self._world(pos)
        self.scale = max(0.2, min(4.0, self.scale * 1.0015 ** event.angleDelta().y()))
        after = self._world(pos)
        self.offset += QPointF((after.x() - before.x()) * self.scale, (after.y() - before.y()) * self.scale)
        self.update()

    def leaveEvent(self, event) -> None:
        if self.hover is not None:
            self.hover = None
            self.update()
        super().leaveEvent(event)

    def sizeHint(self) -> QSize:
        return QSize(700, 520)


# ------------------------------------------------------------------ the list of notes
class NoteDelegate(QStyledItemDelegate):
    """A note in the list: its title, and under it where it lives and when it changed (or a search match)."""

    def __init__(self, ctrl, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl

    def sizeHint(self, option, index) -> QSize:
        return QSize(200, 46)

    def paint(self, p: QPainter, option, index) -> None:
        t = self.ctrl.theme
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(option.rect).adjusted(2, 2, -2, -2)
        if option.state & QStyle.State_Selected:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(t.raised))
            p.drawRoundedRect(rect, 8, 8)
            p.setBrush(QColor(t.chart))
            p.drawRoundedRect(QRectF(rect.left(), rect.top() + 9, 3, rect.height() - 18), 1.5, 1.5)
        elif option.state & QStyle.State_MouseOver:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(t.hover))
            p.drawRoundedRect(rect, 8, 8)
        title_font = QFont(option.font)
        title_font.setPixelSize(13)
        title_font.setWeight(QFont.DemiBold)
        sub_font = QFont(option.font)
        sub_font.setPixelSize(11)
        text_rect = rect.adjusted(12, 5, -8, -5)
        p.setFont(title_font)
        p.setPen(QColor(t.ink))
        fm = QFontMetricsF(title_font)
        p.drawText(QRectF(text_rect.left(), text_rect.top(), text_rect.width(), 18), Qt.AlignLeft | Qt.AlignVCenter,
                   fm.elidedText(index.data(Qt.DisplayRole) or "", Qt.ElideRight, text_rect.width()))
        p.setFont(sub_font)
        p.setPen(QColor(t.muted))
        fm = QFontMetricsF(sub_font)
        p.drawText(QRectF(text_rect.left(), text_rect.top() + 19, text_rect.width(), 16), Qt.AlignLeft | Qt.AlignVCenter,
                   fm.elidedText(index.data(SUB_ROLE) or "", Qt.ElideRight, text_rect.width()))
        p.restore()


def _label(text: str = "", role: str | None = None, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    if role:
        label.setProperty("role", role)
    label.setWordWrap(wrap)
    return label


# ------------------------------------------------------------------ the window
class NotesWindow(QWidget):
    def __init__(self, ctrl):
        super().__init__(None, Qt.Window)
        self.ctrl = ctrl
        self.vault: notes.Vault = ctrl.notes_vault()
        self.index = notes.Index()
        self.current: Path | None = None
        self.dirty = False
        self._loading = False
        self.setWindowTitle("Pomo · Notes")
        self.setWindowIcon(ctrl.icon)
        self.resize(1240, 800)

        page = QWidget()
        page.setObjectName("page")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(page)
        root = QHBoxLayout(page)
        root.setContentsMargins(0, 0, 0, 0)
        self.split = QSplitter(Qt.Horizontal)
        self.split.setChildrenCollapsible(False)
        self.split.setHandleWidth(1)
        root.addWidget(self.split)
        self.split.addWidget(self._sidebar())
        self.split.addWidget(self._center())
        self.split.addWidget(self._side_panel())
        self.split.setStretchFactor(1, 1)
        self.split.setSizes([270, 730, 240])

        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(SAVE_DELAY_MS)
        self.save_timer.timeout.connect(self.save)
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.setInterval(REFRESH_DELAY_MS)
        self.refresh_timer.timeout.connect(self.refresh)
        self.watcher = QFileSystemWatcher(self)
        self.watcher.fileChanged.connect(self._changed_on_disk)
        for keys, fn in (("Ctrl+N", self.new_note), ("Ctrl+P", self._focus_search), ("Ctrl+O", self._focus_search),
                         ("Ctrl+G", lambda: self.show_graph(self.stack.currentIndex() != 2)),
                         ("Ctrl+S", self.save)):
            QShortcut(QKeySequence(keys), self, fn, context=Qt.WindowShortcut)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.save)
            app.commitDataRequest.connect(lambda _manager: self.save())  # Windows is shutting down
        self.retheme()
        self.refresh()
        self._open_recent()

    def _open_recent(self) -> None:
        recent = sorted(self.index.notes, key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
        if recent:
            self.open_path(recent[0])
        else:
            self.current = None
            self.show_graph(False)

    # ------------------------------------------------------------- building it
    def _sidebar(self) -> QWidget:
        box = QFrame()
        box.setObjectName("notesSidebar")
        box.setMinimumWidth(220)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(14, 16, 10, 12)
        lay.setSpacing(8)
        head = QHBoxLayout()
        head.addWidget(_label("Notes", "h1"))
        head.addStretch(1)
        new = QPushButton("New")
        new.setObjectName("primary")
        new.setToolTip("New note  (Ctrl+N)")
        new.clicked.connect(self.new_note)
        head.addWidget(new)
        lay.addLayout(head)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search, or #tag  (Ctrl+P)")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self._fill_list())
        self.search.returnPressed.connect(self._open_first_result)
        lay.addWidget(self.search)
        today = QPushButton("Today's note")
        today.setObjectName("ghost")
        today.setToolTip("Your daily note, with today's focus sessions and parked thoughts")
        today.clicked.connect(self.ctrl.open_daily_note)
        lay.addWidget(today, 0, Qt.AlignLeft)
        self.list = QListWidget()
        self.list.setItemDelegate(NoteDelegate(self.ctrl, self.list))
        self.list.setMouseTracking(True)
        self.list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.list.itemClicked.connect(lambda item: self.open_path(Path(item.data(PATH_ROLE))))
        self.list.itemActivated.connect(lambda item: self.open_path(Path(item.data(PATH_ROLE))))
        lay.addWidget(self.list, 1)
        lay.addWidget(_label("TAGS", "section"))
        self.tag_list = QListWidget()
        self.tag_list.setObjectName("tags")
        self.tag_list.setMaximumHeight(130)
        self.tag_list.itemClicked.connect(lambda item: self.search.setText("#" + item.data(PATH_ROLE)))
        lay.addWidget(self.tag_list)
        foot = QHBoxLayout()
        self.folder_label = _label("", "muted")
        self.folder_label.setMinimumWidth(60)
        foot.addWidget(self.folder_label, 1)
        change = QPushButton("Change…")
        change.setObjectName("ghost")
        change.setToolTip("Keep notes in another folder (an Obsidian vault works too)")
        change.clicked.connect(self._choose_folder)
        foot.addWidget(change)
        lay.addLayout(foot)
        self.daily_box = QCheckBox("Daily note")
        self.daily_box.setToolTip("Pomo writes each day's focus sessions and parked thoughts into Daily/")
        self.daily_box.setChecked(self.ctrl.settings.notes_daily)
        self.daily_box.toggled.connect(self._set_daily)
        lay.addWidget(self.daily_box)
        return box

    def _center(self) -> QWidget:
        box = QFrame()
        box.setObjectName("notesCenter")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(26, 14, 26, 12)
        lay.setSpacing(6)
        top = QHBoxLayout()
        seg = QFrame()
        seg.setObjectName("segment")
        seg_lay = QHBoxLayout(seg)
        seg_lay.setContentsMargins(3, 3, 3, 3)
        seg_lay.setSpacing(2)
        self.note_btn = QPushButton("Note")
        self.graph_btn = QPushButton("Graph")
        for b in (self.note_btn, self.graph_btn):
            b.setObjectName("seg")
            b.setCheckable(True)
            seg_lay.addWidget(b)
        self.note_btn.setChecked(True)
        self.note_btn.clicked.connect(lambda: self.show_graph(False))
        self.graph_btn.clicked.connect(lambda: self.show_graph(True))
        self.graph_btn.setToolTip("How your notes link up  (Ctrl+G)")
        top.addWidget(seg)
        top.addStretch(1)
        self.daily_graph = QCheckBox("Daily notes")
        self.ghost_graph = QCheckBox("Unwritten links")
        for b in (self.daily_graph, self.ghost_graph):
            b.setChecked(True)
            b.toggled.connect(lambda _on: self._fill_graph())
            top.addWidget(b)
        self.delete_btn = QPushButton("Delete")
        self.delete_btn.setObjectName("ghost")
        self.delete_btn.setToolTip("Move this note to the .trash folder in your notes folder")
        self.delete_btn.clicked.connect(self.delete_note)
        top.addWidget(self.delete_btn)
        lay.addLayout(top)

        self.stack = QStackedWidget()
        note_page = QWidget()
        note_lay = QVBoxLayout(note_page)
        note_lay.setContentsMargins(0, 8, 0, 0)
        note_lay.setSpacing(2)
        self.title_edit = QLineEdit()
        self.title_edit.setObjectName("noteTitle")
        self.title_edit.setPlaceholderText("Untitled")
        self.title_edit.editingFinished.connect(self._rename)
        self.title_edit.returnPressed.connect(lambda: self.editor.setFocus())
        note_lay.addWidget(self.title_edit)
        self.meta = _label("", "muted")
        note_lay.addWidget(self.meta)
        self.editor = NoteEditor()
        self.editor.textChanged.connect(self._edited)
        self.editor.link_activated.connect(self.open_link)
        self.editor.tag_activated.connect(lambda tag: self.search.setText("#" + tag))
        note_lay.addWidget(self.editor, 1)
        self.highlighter = MarkdownHighlighter(self.editor.document(), self.ctrl.theme,
                                               mode_color(self.ctrl.theme, self.ctrl.settings, "focus"))
        self.stack.addWidget(note_page)
        self.empty = _label("", "secondary", wrap=True)
        self.empty.setAlignment(Qt.AlignCenter)
        self.stack.addWidget(self.empty)
        self.graph = GraphView(self.ctrl)
        self.graph.open_requested.connect(self._open_from_graph)
        self.stack.addWidget(self.graph)
        lay.addWidget(self.stack, 1)
        return box

    def _side_panel(self) -> QWidget:
        box = QFrame()
        box.setObjectName("notesSide")
        box.setMinimumWidth(190)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(14, 18, 12, 12)
        lay.setSpacing(6)
        lay.addWidget(_label("LINKED FROM", "section"))
        self.back_list = QListWidget()
        self.back_list.setItemDelegate(NoteDelegate(self.ctrl, self.back_list))
        self.back_list.setMouseTracking(True)
        self.back_list.itemClicked.connect(lambda item: self.open_path(Path(item.data(PATH_ROLE))))
        lay.addWidget(self.back_list, 3)
        lay.addWidget(_label("LINKS TO", "section"))
        self.out_list = QListWidget()
        self.out_list.setItemDelegate(NoteDelegate(self.ctrl, self.out_list))
        self.out_list.setMouseTracking(True)
        self.out_list.itemClicked.connect(self._open_out)
        lay.addWidget(self.out_list, 2)
        self.tags_label = _label("", "muted", wrap=True)
        lay.addWidget(self.tags_label)
        self.help = _label("[[ links a note, # tags it.\nCtrl+click a link to open it.\nCtrl+Enter ticks a "
                           "checkbox.\nCtrl+B bold, Ctrl+I italic,\nCtrl+Shift+X strike.", "muted", wrap=True)
        lay.addWidget(self.help)
        return box

    # ------------------------------------------------------------- look
    def retheme(self) -> None:
        t = self.ctrl.theme
        accent = mode_color(t, self.ctrl.settings, "focus")
        self.setStyleSheet(f"""
            QFrame#notesSidebar, QFrame#notesSide {{ background: {t.page}; }}
            QFrame#notesCenter {{ background: {t.surface}; }}
            QSplitter::handle {{ background: {t.grid}; }}
            QListWidget {{ background: transparent; border: none; outline: none; color: {t.ink}; }}
            QListWidget#tags::item {{ padding: 3px 8px; border-radius: 6px; color: {t.ink_2}; }}
            QListWidget#tags::item:hover {{ background: {t.hover}; }}
            QListWidget#tags::item:selected {{ background: {t.raised}; color: {t.ink}; }}
            QPlainTextEdit#editor {{ background: {t.surface}; border: none; color: {t.ink};
                selection-background-color: {accent.name()}; selection-color: {t.on_accent}; }}
            QLineEdit#noteTitle {{ background: transparent; border: none; font-size: 26px; font-weight: 600;
                padding: 0; color: {t.ink}; }}
        """)
        popup = self.editor.completer.popup()
        popup.setStyleSheet(f"QListView {{ background: {t.surface}; color: {t.ink}; border: 1px solid {t.border_qss};"
                            f" border-radius: 8px; padding: 4px; outline: none; }} QListView::item {{ padding: 4px 8px;"
                            f" border-radius: 6px; }} QListView::item:selected {{ background: {t.hover}; color: {t.ink}; }}")
        self.highlighter.set_theme(t, accent)
        set_dark_title_bar(self, t.dark)
        self.graph.update()
        self.list.viewport().update()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        set_dark_title_bar(self, self.ctrl.theme.dark)
        self.refresh()

    # ------------------------------------------------------------- the vault's contents
    def schedule_refresh(self) -> None:
        self.refresh_timer.start()

    def refresh(self) -> None:
        self.refresh_timer.stop()
        self.index = self.vault.index()
        self.highlighter.known = {p.stem.lower() for p in self.index.notes}
        self.highlighter.rehighlight()
        self.editor.set_titles([p.stem for p in self.index.notes])
        self.folder_label.setText(self.vault.root.name)
        self.folder_label.setToolTip(str(self.vault.root))
        self._fill_list()
        self._fill_tags()
        self._fill_side()
        if self.stack.currentWidget() is self.graph:
            self._fill_graph()

    def _fill_list(self) -> None:
        query = self.search.text().strip()
        self.list.clear()
        if query:
            rows = [(path, snip or self._place(path)) for path, snip in self.vault.search(query)]
        else:
            ordered = sorted(self.index.notes, key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
            rows = [(path, self._place(path)) for path in ordered]
        for path, sub in rows:
            item = QListWidgetItem(path.stem)
            item.setData(PATH_ROLE, str(path))
            item.setData(SUB_ROLE, sub)
            item.setToolTip(self.vault.rel(path))
            self.list.addItem(item)
            if path == self.current:
                item.setSelected(True)
                self.list.setCurrentItem(item)

    def _place(self, path: Path) -> str:
        folder = self.vault.folder(path)
        when = _when(path)
        return f"{folder} · {when}" if folder else when

    def _fill_tags(self) -> None:
        self.tag_list.clear()
        for key in sorted(self.index.tags, key=lambda k: (-len(self.index.tags[k]), k)):
            name = self.index.tag_names[key]
            item = QListWidgetItem(f"#{name}  ({len(self.index.tags[key])})")
            item.setData(PATH_ROLE, name)
            item.setSizeHint(QSize(0, 26))
            self.tag_list.addItem(item)
        if not self.index.tags:
            item = QListWidgetItem("Tag a note with #word")
            item.setFlags(Qt.NoItemFlags)
            item.setSizeHint(QSize(0, 26))
            self.tag_list.addItem(item)

    def _fill_side(self) -> None:
        self.back_list.clear()
        self.out_list.clear()
        if self.current is None:
            self.tags_label.setText("")
            return
        for path in self.index.backlinks.get(self.current, []):
            text = self.vault.read(path)
            line = next((ln for ln in text.split("\n") if f"[[{self.current.stem.lower()}" in ln.lower()), "")
            item = QListWidgetItem(path.stem)
            item.setData(PATH_ROLE, str(path))
            item.setData(SUB_ROLE, notes.snippet(line, self.current.stem, 60) if line else self._place(path))
            self.back_list.addItem(item)
        if not self.index.backlinks.get(self.current):
            item = QListWidgetItem("No links here yet")
            item.setData(SUB_ROLE, f"Write [[{self.current.stem}]] in another note")
            item.setFlags(Qt.NoItemFlags)
            self.back_list.addItem(item)
        for path in self.index.outgoing.get(self.current, []):
            item = QListWidgetItem(path.stem)
            item.setData(PATH_ROLE, str(path))
            item.setData(SUB_ROLE, self._place(path))
            self.out_list.addItem(item)
        for target in self.index.unresolved.get(self.current, []):
            item = QListWidgetItem(target)
            item.setData(PATH_ROLE, "")
            item.setData(SUB_ROLE, "Not written yet: click to start it")
            self.out_list.addItem(item)
        tags = self.index.note_tags.get(self.current, [])
        self.tags_label.setText("Tags: " + "  ".join(f"#{t}" for t in tags) if tags else "")
        links = len(self.index.outgoing.get(self.current, [])) + len(self.index.unresolved.get(self.current, []))
        back = len(self.index.backlinks.get(self.current, []))
        bits = [self.vault.folder(self.current) or "Notes", _when(self.current)]
        if links:
            bits.append(f"{links} link{'s' if links != 1 else ''}")
        if back:
            bits.append(f"linked from {back}")
        self.meta.setText(" · ".join(b for b in bits if b))

    def _fill_graph(self) -> None:
        nodes, edges = [], []
        keep_daily, keep_ghosts = self.daily_graph.isChecked(), self.ghost_graph.isChecked()
        shown = set()
        for path in self.index.notes:
            kind = self.vault.kind(path)
            if kind == "daily" and not keep_daily:
                continue
            shown.add(path)
            nodes.append({"key": str(path), "title": path.stem, "kind": kind})
        ghosts = {}
        for path in shown:
            for target in self.index.outgoing.get(path, []):
                if target in shown:
                    edges.append((str(path), str(target)))
            if keep_ghosts:
                for target in self.index.unresolved.get(path, []):
                    key = "?" + target.lower()
                    ghosts.setdefault(key, target)
                    edges.append((str(path), key))
        nodes += [{"key": key, "title": title, "kind": "ghost"} for key, title in ghosts.items()]
        self.graph.set_graph(nodes, edges, str(self.current) if self.current else "")

    # ------------------------------------------------------------- opening notes
    def open_path(self, path: Path) -> None:
        self.save()
        if not path.exists():
            self.refresh()
            return
        if self.current is not None and str(self.current) in self.watcher.files():
            self.watcher.removePath(str(self.current))
        self.current = path
        self._loading = True
        self.editor.setPlainText(self.vault.read(path))
        self._loading = False
        self.dirty = False
        self.title_edit.setText(path.stem)
        self.watcher.addPath(str(path))
        self.show_graph(False)
        self.stack.setCurrentIndex(0)
        self.delete_btn.setEnabled(True)
        self._select_in_list()
        self._fill_side()
        self.graph.set_current(str(path))
        self.editor.setFocus()

    def open_link(self, target: str) -> None:
        """Follow a [[link]]: open its note, or start it when it isn't written yet (as Obsidian does)."""
        path = self.vault.resolve(target) or self.vault.create(target)
        self.refresh()
        self.open_path(path)

    def _open_out(self, item: QListWidgetItem) -> None:
        if item.data(PATH_ROLE):
            self.open_path(Path(item.data(PATH_ROLE)))
        elif item.flags() & Qt.ItemIsEnabled:
            self.open_link(item.text())

    def _open_from_graph(self, key: str) -> None:
        if key.startswith("?"):
            node = next((n for n in self.graph.nodes if n["key"] == key), None)
            if node is not None:
                self.open_link(node["title"])
        else:
            self.open_path(Path(key))

    def _open_first_result(self) -> None:
        item = self.list.item(0)
        if item is not None:
            self.open_path(Path(item.data(PATH_ROLE)))
        elif self.search.text().strip() and not self.search.text().startswith("#"):
            self.open_link(self.search.text().strip())  # nothing found: Enter starts a note with that name

    def _select_in_list(self) -> None:
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.data(PATH_ROLE) == str(self.current):
                self.list.setCurrentItem(item)
                return
        self.list.clearSelection()

    def is_open(self, path: Path) -> bool:
        return self.current is not None and self.current == path

    def edit_open_note(self, change) -> None:
        """Apply change(text) -> text to the open note in place (the daily note getting today's sessions)."""
        self.editor.replace_text(change(self.editor.toPlainText()))
        self.save()

    def _show_empty(self) -> None:
        self.current = None
        self.title_edit.clear()
        self.meta.clear()
        self.delete_btn.setEnabled(False)
        self.empty.setText("<b>No note open.</b><br><br>Pick one on the left, or click <b>New</b>. Notes are "
                           f"Markdown files in <i>{self.vault.root}</i>.")
        self.empty.setTextFormat(Qt.RichText)
        self.stack.setCurrentWidget(self.empty)
        self._fill_side()

    def show_graph(self, on: bool) -> None:
        self.note_btn.setChecked(not on)
        self.graph_btn.setChecked(on)
        for w in (self.daily_graph, self.ghost_graph):
            w.setVisible(on)
        self.delete_btn.setVisible(not on)
        if on:
            self.save()
            self._fill_graph()
            self.stack.setCurrentWidget(self.graph)
        elif self.current is not None:
            self.stack.setCurrentIndex(0)
        else:
            self._show_empty()

    def _focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()

    # ------------------------------------------------------------- changing notes
    def _edited(self) -> None:
        if self._loading or self.current is None:
            return
        self.dirty = True
        self.save_timer.start()

    def save(self) -> None:
        self.save_timer.stop()
        if not self.dirty or self.current is None:
            return
        try:
            self.vault.write(self.current, self.editor.toPlainText())
            self.dirty = False
            if str(self.current) not in self.watcher.files():
                self.watcher.addPath(str(self.current))
        except OSError as exc:
            self.meta.setText(f"Couldn't save: {exc}")
            return
        self.schedule_refresh()

    def _changed_on_disk(self, name: str) -> None:
        """Another app (Obsidian, say) changed the open note: take its version unless you're mid-edit."""
        path = Path(name)
        if path.exists() and name not in self.watcher.files():
            self.watcher.addPath(name)  # saving by replacing the file drops the watch
        if self.current is None or path != self.current or self.dirty:
            return
        text = self.vault.read(path)
        if text != self.editor.toPlainText():
            self._loading = True
            self.editor.replace_text(text)
            self._loading = False
        self.schedule_refresh()

    def new_note(self) -> None:
        self.save()
        path = self.vault.create("Untitled")
        self.search.clear()
        self.refresh()
        self.open_path(path)
        self.title_edit.setFocus()
        self.title_edit.selectAll()

    def _rename(self) -> None:
        if self.current is None:
            return
        title = self.title_edit.text().strip()
        if not title or title == self.current.stem:
            self.title_edit.setText(self.current.stem)
            return
        self.save()
        old = self.current
        try:
            if str(old) in self.watcher.files():
                self.watcher.removePath(str(old))
            new, fixed = self.vault.rename(old, title)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Rename note", str(exc))
            self.title_edit.setText(old.stem)
            self.watcher.addPath(str(old))
            return
        self.current = None
        self.refresh()
        self.open_path(new)
        if fixed:
            self.meta.setText(f"Renamed, and updated the links in {fixed} note{'s' if fixed != 1 else ''}.")

    def delete_note(self) -> None:
        if self.current is None:
            return
        box = QMessageBox(QMessageBox.Question, "Delete note", f"Delete “{self.current.stem}”?",
                          QMessageBox.Yes | QMessageBox.No, self)
        box.setInformativeText("It moves to the .trash folder inside your notes folder, so you can get it back.")
        box.setDefaultButton(QMessageBox.No)
        if box.exec() != QMessageBox.Yes:
            return
        self.save()
        if str(self.current) in self.watcher.files():
            self.watcher.removePath(str(self.current))
        try:
            self.vault.trash(self.current)
        except OSError as exc:
            QMessageBox.warning(self, "Delete note", str(exc))
            return
        self.current = None
        self.refresh()
        self._open_recent()

    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Keep notes in this folder", str(self.vault.root))
        if not folder or Path(folder) == self.vault.root:
            return
        self.save()
        if self.current is not None and str(self.current) in self.watcher.files():
            self.watcher.removePath(str(self.current))
        self.ctrl.settings.notes_dir = folder
        self.ctrl.save_settings()
        self.vault = self.ctrl.notes_vault()
        self.current = None
        self.search.clear()
        self.refresh()
        self._open_recent()

    def _set_daily(self, on: bool) -> None:
        self.ctrl.settings.notes_daily = on
        self.ctrl.save_settings()
        if on:
            self.ctrl.refresh_daily_note()

    def closeEvent(self, event) -> None:
        self.save()
        super().closeEvent(event)
