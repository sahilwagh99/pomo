"""Sticky notes: little notes in your theme's colors that you put anywhere on your screen.

Each one is its own small window: drag it by its top strip, resize it from the corner, pick its color,
pin it above other windows, and write with bullets, tick-boxes, ~~strikethrough~~ and bold. A note can
remind you at a time you pick: it jumps to the front and bounces, and Pomo hops and tells you. They're
saved (as Markdown, with where they sit and their reminders) in stickies.json in Pomo's data folder, and
come back when Pomo starts; a reminder that came due while the PC was off goes off then.
"""
from __future__ import annotations

import json
import math
import os
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QDate, QDateTime, QObject, QPoint, QPointF, QRect, QRectF, Qt, QTime, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QGuiApplication,
    QIcon,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QShortcut,
    QSyntaxHighlighter,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextListFormat,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QSizeGrip,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

SHADOW = 10
RADIUS = 12
HEADER = 30
DEFAULT_SIZE = (280, 260)
MIN_SIZE = (190, 150)
MAX_TEXT = 20000
SAVE_DELAY_MS = 300  # short, so shutting the PC down right after typing still keeps it
REMIND_CHECK_MS = 15000
REBOUNCE_S = 60      # a reminder you haven't looked at bounces again a minute later...
REBOUNCES = 2        # ...twice
ON_TOP_S = 12        # how long a ringing note stays above other windows (unless it's pinned there)
# Sticky colors come from the theme, so they change with it: its three mode colors, or plain paper
COLORS = ("focus", "short", "long", "paper")
COLOR_NAMES = {"focus": "Focus color", "short": "Short break color", "long": "Long break color", "paper": "Paper"}
MARKDOWN = QTextDocument.MarkdownFeature.MarkdownDialectGitHub
UNCHECKED = QTextBlockFormat.MarkerType.Unchecked
CHECKED = QTextBlockFormat.MarkerType.Checked
NO_MARKER = QTextBlockFormat.MarkerType.NoMarker


# ------------------------------------------------------------------ saving
def parse_when(text) -> datetime | None:
    try:
        return datetime.fromisoformat(text) if isinstance(text, str) and text else None
    except ValueError:
        return None


def clean(raw) -> list[dict]:
    """Tidy sticky notes from whatever was saved."""
    out = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue

        def num(key, default, lo, hi):
            value = item.get(key)
            return max(lo, min(hi, value)) if isinstance(value, int) and not isinstance(value, bool) else default

        when = parse_when(item.get("remind_at"))
        out.append({
            "id": str(item.get("id") or uuid.uuid4().hex)[:40],
            "text": str(item.get("text") or "")[:MAX_TEXT],
            "x": num("x", 200, -100000, 100000),
            "y": num("y", 200, -100000, 100000),
            "w": num("w", DEFAULT_SIZE[0], MIN_SIZE[0], 4000),
            "h": num("h", DEFAULT_SIZE[1], MIN_SIZE[1], 4000),
            "color": item.get("color") if item.get("color") in COLORS else COLORS[0],
            "on_top": item.get("on_top") is True,
            "open": item.get("open") is not False,
            "remind_at": when.isoformat(timespec="minutes") if when else "",
            "rang": str(item.get("rang") or "")[:40] if parse_when(item.get("rang")) else "",
        })
    return out


def load(path: Path) -> list[dict]:
    try:
        return clean(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return []


def save(path: Path, items: list[dict]) -> None:
    """Write the notes so a sudden shutdown can't lose or garble them: to disk first, then swapped in."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(items, indent=2))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def colors(theme, key: str) -> tuple[QColor, QColor, QColor]:
    """Background, header strip and ink for a sticky of this color in this theme."""
    if key == "paper":
        return QColor(theme.surface), QColor(theme.raised), QColor(theme.ink)
    bg = QColor(getattr(theme, key))
    return bg, bg.darker(108), QColor(theme.on_accent)


# ------------------------------------------------------------------ reminder times
def clock(moment: datetime) -> str:
    """6:00 PM."""
    return moment.strftime("%I:%M %p").lstrip("0")


def when_text(moment: datetime, now: datetime) -> str:
    """'6:00 PM', 'tomorrow 9:00 AM' or 'Fri 28 Nov, 9:00 AM'."""
    days = (moment.date() - now.date()).days
    if days == 0:
        return clock(moment)
    if days == 1:
        return f"tomorrow {clock(moment)}"
    if days == -1:
        return f"yesterday {clock(moment)}"
    return f"{moment:%a} {moment.day} {moment:%b}, {clock(moment)}"


def quick_times(now: datetime) -> list[tuple[str, datetime]]:
    """The reminder menu's ready-made choices."""
    now = now.replace(second=0, microsecond=0)
    out = [("In 15 minutes", now + timedelta(minutes=15)), ("In 1 hour", now + timedelta(hours=1))]
    evening = now.replace(hour=18, minute=0)
    if evening - now >= timedelta(minutes=30):
        out.append(("This evening", evening))
    morning = (now + timedelta(days=1)).replace(hour=9, minute=0)
    out.append(("Tomorrow morning", morning))
    return out


# ------------------------------------------------------------------ the little buttons
class Glyph(QAbstractButton):
    """A small round button with a drawn glyph, in the sticky's ink."""

    def __init__(self, glyph: str, tip: str, parent=None, checkable: bool = False):
        super().__init__(parent)
        self.glyph = glyph
        self.ink = QColor("#3f2a26")
        self.setToolTip(tip)
        self.setCheckable(checkable)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedSize(24, 24)
        self.swatch: QColor | None = None
        self.lit = False  # the bell of a note with a reminder set

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        ink = QColor(self.ink)
        if self.underMouse() or self.isChecked():
            wash = QColor(ink)
            wash.setAlpha(40 if self.isChecked() else 24)
            p.setPen(Qt.NoPen)
            p.setBrush(wash)
            p.drawRoundedRect(QRectF(1, 1, self.width() - 2, self.height() - 2), 7, 7)
        ink.setAlpha(230 if self.underMouse() or self.isChecked() or self.lit else 165)
        pen = QPen(ink, 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        g, x, y = self.glyph, c.x(), c.y()
        if g == "plus":
            p.drawLine(QPointF(x - 5, y), QPointF(x + 5, y))
            p.drawLine(QPointF(x, y - 5), QPointF(x, y + 5))
        elif g == "close":
            p.drawLine(QPointF(x - 4, y - 4), QPointF(x + 4, y + 4))
            p.drawLine(QPointF(x + 4, y - 4), QPointF(x - 4, y + 4))
        elif g == "color":
            p.setBrush(self.swatch or ink)
            p.drawEllipse(c, 5.5, 5.5)
        elif g == "pin":
            p.drawEllipse(QPointF(x, y - 3), 3.2, 3.2)
            p.drawLine(QPointF(x, y + 0.2), QPointF(x, y + 6))
            if self.isChecked():
                p.setBrush(ink)
                p.drawEllipse(QPointF(x, y - 3), 3.2, 3.2)
        elif g == "trash":
            p.drawLine(QPointF(x - 5, y - 4), QPointF(x + 5, y - 4))
            p.drawLine(QPointF(x - 1.5, y - 6), QPointF(x + 1.5, y - 6))
            path = QPainterPath(QPointF(x - 4, y - 4))
            path.lineTo(x - 3.2, y + 5.5)
            path.lineTo(x + 3.2, y + 5.5)
            path.lineTo(x + 4, y - 4)
            p.drawPath(path)
        elif g == "bell":
            path = QPainterPath(QPointF(x - 5.5, y + 3.5))
            path.cubicTo(x - 4, y + 2, x - 4.5, y - 6, x, y - 6)
            path.cubicTo(x + 4.5, y - 6, x + 4, y + 2, x + 5.5, y + 3.5)
            path.closeSubpath()
            if self.lit:
                p.setBrush(ink)
            p.drawPath(path)
            p.drawLine(QPointF(x - 1.6, y + 6), QPointF(x + 1.6, y + 6))
        elif g == "check":  # a tick-box with a tick, beside a line
            box = QRectF(x - 6.5, y - 4.5, 9, 9)
            p.drawRoundedRect(box, 2.2, 2.2)
            p.drawPolyline([QPointF(box.left() + 2, box.center().y()), QPointF(box.left() + 4, box.bottom() - 2.2),
                            QPointF(box.right() - 1.6, box.top() + 2.2)])
            p.drawLine(QPointF(x + 4.5, y - 1), QPointF(x + 7, y - 1))
        elif g == "bullets":
            p.setBrush(ink)
            for dy in (-4, 0, 4):
                p.setPen(Qt.NoPen)
                p.drawEllipse(QPointF(x - 5, y + dy), 1.3, 1.3)
                p.setPen(pen)
                p.drawLine(QPointF(x - 2, y + dy), QPointF(x + 6, y + dy))
        elif g in ("strike", "bold"):
            font = QFont(self.font())
            font.setPixelSize(13)
            font.setBold(g == "bold")
            font.setStrikeOut(g == "strike")
            p.setFont(font)
            p.drawText(self.rect(), Qt.AlignCenter, "S" if g == "strike" else "B")
        p.end()


class _Chip(QAbstractButton):
    """The reminder's time at the bottom of a note: a bell and '6:00 PM'. Click it to change it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ink = QColor("#3f2a26")
        self.ringing = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedHeight(22)

    def set_text(self, text: str) -> None:
        self.setText(text)
        font = QFont(self.font())
        font.setPixelSize(12)
        font.setWeight(QFont.DemiBold)
        self.setFont(font)
        self.setFixedWidth(int(self.fontMetrics().horizontalAdvance(text)) + 30)
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        wash = QColor(self.ink)
        wash.setAlpha(70 if self.ringing else 32 if self.underMouse() else 20)
        p.setPen(Qt.NoPen)
        p.setBrush(wash)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 11, 11)
        p.setPen(QPen(self.ink, 1.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        x, y = 12.0, self.height() / 2
        bell = QPainterPath(QPointF(x - 4, y + 2.5))
        bell.cubicTo(x - 3, y + 1.5, x - 3.3, y - 4.5, x, y - 4.5)
        bell.cubicTo(x + 3.3, y - 4.5, x + 3, y + 1.5, x + 4, y + 2.5)
        bell.closeSubpath()
        p.setBrush(self.ink if self.ringing else Qt.NoBrush)
        p.drawPath(bell)
        p.drawLine(QPointF(x - 1.2, y + 4.4), QPointF(x + 1.2, y + 4.4))
        p.setFont(self.font())
        p.drawText(QRectF(22, 0, self.width() - 26, self.height()), Qt.AlignVCenter | Qt.AlignLeft, self.text())
        p.end()


class _Handle(QWidget):
    """The top strip: drag it to move the note."""

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.window().windowHandle() is not None:
            self.window().windowHandle().startSystemMove()
        super().mousePressEvent(event)


class _DoneHighlighter(QSyntaxHighlighter):
    """Ticked items look done (struck through and faded) without that being saved into the text."""

    def __init__(self, document):
        super().__init__(document)
        self.done = QTextCharFormat()

    def set_ink(self, ink: QColor) -> None:
        faded = QColor(ink)
        faded.setAlpha(120)
        self.done = QTextCharFormat()
        self.done.setForeground(faded)
        self.done.setFontStrikeOut(True)
        self.rehighlight()

    def highlightBlock(self, text: str) -> None:
        if self.currentBlock().blockFormat().marker() == CHECKED:
            self.setFormat(0, len(text), self.done)


class _Body(QTextEdit):
    """The note's text: "- " starts bullets and "[] " a checklist, with the usual formatting shortcuts."""

    BOX = 13.0

    def __init__(self, note: "StickyNote"):
        super().__init__(note)
        self.note = note
        self.ink = QColor("#3f2a26")
        self.bg = QColor("#fffdf8")
        self._box_press = False
        self.setAcceptRichText(False)  # pasting brings text, not another app's fonts and colors
        self.setFrameShape(QTextEdit.NoFrame)
        self.setTabChangesFocus(False)
        self.setPlaceholderText("Write something…")
        self.document().setDocumentMargin(2)
        self.highlighter = _DoneHighlighter(self.document())

    # ------------------------------------------------------------- tick-boxes
    def _box_rect(self, block) -> QRectF:
        """Where a checklist item's box goes: just left of its text (over Qt's plain one)."""
        r = self.cursorRect(QTextCursor(block))
        return QRectF(r.left() - self.BOX - 5, r.center().y() - self.BOX / 2, self.BOX, self.BOX)

    def _box_at(self, pos) -> QTextCursor | None:
        cursor = self.cursorForPosition(pos)
        block = cursor.block()
        if block.blockFormat().marker() == NO_MARKER:
            return None
        return QTextCursor(block) if self._box_rect(block).adjusted(-4, -4, 4, 4).contains(QPointF(pos)) else None

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.Antialiasing)
        block = self.cursorForPosition(QPoint(0, 0)).block()
        bottom = self.viewport().height()
        while block.isValid():
            r = self.cursorRect(QTextCursor(block))
            if r.top() > bottom:
                break
            marker = block.blockFormat().marker()
            if marker != NO_MARKER and block.isVisible():
                box = self._box_rect(block)
                p.setPen(Qt.NoPen)
                p.setBrush(self.bg)  # hide Qt's own box...
                p.drawRect(QRectF(box.left() - 4, r.top(), r.left() - box.left() + 3, r.height()))
                p.setPen(QPen(self.ink, 1.5))  # ...and draw a softer one
                p.setBrush(self.ink if marker == CHECKED else Qt.NoBrush)
                p.drawRoundedRect(box, 3.5, 3.5)
                if marker == CHECKED:
                    p.setPen(QPen(self.bg, 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                    p.drawPolyline([QPointF(box.left() + 3, box.center().y() + 0.2),
                                    QPointF(box.left() + 5.6, box.bottom() - 3.2),
                                    QPointF(box.right() - 2.8, box.top() + 3.4)])
            block = block.next()
        p.end()

    def mousePressEvent(self, event) -> None:
        hit = self._box_at(event.position().toPoint()) if event.button() == Qt.LeftButton else None
        if hit is not None:
            self._box_press = True
            self.toggle_box(hit)
            return
        self._box_press = False
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._box_press:
            self._box_press = False
            return
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event) -> None:
        super().mouseMoveEvent(event)
        over = self._box_at(event.position().toPoint()) is not None
        self.viewport().setCursor(Qt.PointingHandCursor if over else Qt.IBeamCursor)

    def toggle_box(self, cursor: QTextCursor) -> None:
        fmt = QTextBlockFormat(cursor.blockFormat())
        fmt.setMarker(UNCHECKED if fmt.marker() == CHECKED else CHECKED)
        cursor.setBlockFormat(fmt)
        self.highlighter.rehighlightBlock(cursor.block())

    @staticmethod
    def make_checklist(cursor: QTextCursor) -> None:
        if cursor.currentList() is None:
            cursor.createList(QTextListFormat.ListDisc)
        fmt = QTextBlockFormat(cursor.blockFormat())
        fmt.setMarker(UNCHECKED)
        cursor.setBlockFormat(fmt)

    # ------------------------------------------------------------- keys
    def keyPressEvent(self, event) -> None:
        cursor = self.textCursor()
        block = cursor.block()
        key, mods = event.key(), event.modifiers()
        ctrl = bool(mods & Qt.ControlModifier)
        shift = bool(mods & Qt.ShiftModifier)
        if ctrl and not shift and key == Qt.Key_B:
            self.note.toggle_bold()
            return
        if ctrl and key == Qt.Key_T:  # as in Windows' Sticky Notes
            self.note.toggle_strike()
            return
        if ctrl and shift and key == Qt.Key_L:
            self.note.toggle_bullets()
            return
        if ctrl and shift and key == Qt.Key_C:
            self.note.toggle_checklist()
            return
        if ctrl and key in (Qt.Key_Return, Qt.Key_Enter) and block.blockFormat().marker() != NO_MARKER:
            self.toggle_box(cursor)
            return
        if ctrl and key == Qt.Key_I:
            fmt = QTextCharFormat()
            fmt.setFontItalic(not self.currentCharFormat().fontItalic())
            self._merge(fmt)
            return
        before = block.text()[:cursor.positionInBlock()]
        if key == Qt.Key_Space and not mods and before == block.text():
            if before in ("-", "*") and cursor.currentList() is None:
                self._replace_line(cursor, lambda c: c.createList(QTextListFormat.ListDisc))  # "- " starts bullets
                return
            if before in ("[]", "[ ]", "- []", "- [ ]") and block.blockFormat().marker() == NO_MARKER:
                self._replace_line(cursor, self.make_checklist)  # "[] " starts a checklist
                return
        if key in (Qt.Key_Return, Qt.Key_Enter) and cursor.currentList() is not None and not block.text().strip():
            self._unlist(cursor)  # Enter on an empty item ends the list
            return
        if key == Qt.Key_Backspace and cursor.currentList() is not None and cursor.atBlockStart() \
                and not cursor.hasSelection():
            self._unlist(cursor)
            return
        if key == Qt.Key_Tab and cursor.currentList() is not None:
            self._indent(cursor, 1)
            return
        if key == Qt.Key_Backtab and cursor.currentList() is not None:
            self._indent(cursor, -1)
            return
        super().keyPressEvent(event)
        if key in (Qt.Key_Return, Qt.Key_Enter):
            fresh = self.textCursor()
            if fresh.blockFormat().marker() == CHECKED:  # the next item of a list starts unticked
                fmt = QTextBlockFormat(fresh.blockFormat())
                fmt.setMarker(UNCHECKED)
                fresh.setBlockFormat(fmt)

    @staticmethod
    def _replace_line(cursor: QTextCursor, then) -> None:
        cursor.beginEditBlock()
        cursor.movePosition(QTextCursor.StartOfBlock)
        cursor.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
        cursor.removeSelectedText()
        then(cursor)
        cursor.endEditBlock()

    def _merge(self, fmt: QTextCharFormat) -> None:
        cursor = self.textCursor()
        if cursor.hasSelection():
            cursor.mergeCharFormat(fmt)
        self.mergeCurrentCharFormat(fmt)

    @staticmethod
    def _unlist(cursor: QTextCursor) -> None:
        lst = cursor.currentList()
        if lst is None:
            return
        lst.remove(cursor.block())
        fmt = QTextBlockFormat(cursor.blockFormat())
        fmt.setIndent(0)
        fmt.setMarker(NO_MARKER)
        cursor.setBlockFormat(fmt)

    @staticmethod
    def _indent(cursor: QTextCursor, step: int) -> None:
        lst = cursor.currentList()
        fmt = QTextListFormat(lst.format())
        level = max(1, min(4, fmt.indent() + step))
        if level == fmt.indent():
            return
        fmt.setIndent(level)
        fmt.setStyle((QTextListFormat.ListDisc, QTextListFormat.ListCircle, QTextListFormat.ListSquare)[(level - 1) % 3])
        cursor.beginEditBlock()
        lst.remove(cursor.block())
        cursor.createList(fmt)
        cursor.endEditBlock()


# ------------------------------------------------------------------ one sticky note
class StickyNote(QWidget):
    changed = Signal()

    def __init__(self, board: "StickyBoard", data: dict):
        super().__init__(None)
        self.board, self.data = board, data
        self._bounce_t0: float | None = None
        self._bounce_base = QPoint()
        self._bounces_left = 0
        self._rang_at = 0.0
        self._apply_flags()
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("Sticky note")
        self.setMinimumSize(*MIN_SIZE)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SHADOW + 8, SHADOW + 3, SHADOW + 8, SHADOW + 4)
        outer.setSpacing(2)
        self.handle = _Handle(self)
        self.handle.setFixedHeight(HEADER - 4)
        self.handle.setCursor(Qt.SizeAllCursor)
        top = QHBoxLayout(self.handle)
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(2)
        self.add_btn = Glyph("plus", "New sticky note  (Ctrl+N)")
        self.color_btn = Glyph("color", "Color")
        self.pin_btn = Glyph("pin", "Keep on top of other windows", checkable=True)
        self.bell_btn = Glyph("bell", "Remind me…")
        self.trash_btn = Glyph("trash", "Delete this note  (Ctrl+D)")
        self.close_btn = Glyph("close", "Hide it (it's kept: bring it back from Pomo's menu)")
        for b in (self.add_btn, self.color_btn, self.pin_btn, self.bell_btn):
            top.addWidget(b)
        top.addStretch(1)
        top.addWidget(self.trash_btn)
        top.addWidget(self.close_btn)
        outer.addWidget(self.handle)

        self.body = _Body(self)
        outer.addWidget(self.body, 1)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(2)
        self.bullets_btn = Glyph("bullets", "Bullets  (Ctrl+Shift+L, or type - and a space)", checkable=True)
        self.check_btn = Glyph("check", "Checklist  (Ctrl+Shift+C, or type [] and a space)\n"
                                        "Click a box, or press Ctrl+Enter, to tick it", checkable=True)
        self.strike_btn = Glyph("strike", "Strikethrough  (Ctrl+T)", checkable=True)
        self.bold_btn = Glyph("bold", "Bold  (Ctrl+B)", checkable=True)
        for b in (self.bullets_btn, self.check_btn, self.strike_btn, self.bold_btn):
            bottom.addWidget(b)
        bottom.addStretch(1)
        self.chip = _Chip(self)
        self.chip.setToolTip("Change or remove the reminder")
        bottom.addWidget(self.chip, 0, Qt.AlignVCenter)
        self.grip = QSizeGrip(self)
        self.grip.setFixedSize(16, 16)
        bottom.addWidget(self.grip, 0, Qt.AlignBottom | Qt.AlignRight)
        outer.addLayout(bottom)

        self.add_btn.clicked.connect(lambda: self.board.new_note(near=self))
        self.color_btn.clicked.connect(self._color_menu)
        self.pin_btn.setChecked(data["on_top"])
        self.pin_btn.toggled.connect(self.set_on_top)
        self.bell_btn.clicked.connect(lambda: self.reminder_menu(self.bell_btn))
        self.chip.clicked.connect(lambda: self.reminder_menu(self.chip))
        self.trash_btn.clicked.connect(self.delete)
        self.close_btn.clicked.connect(self.hide_note)
        self.bullets_btn.clicked.connect(self.toggle_bullets)
        self.check_btn.clicked.connect(self.toggle_checklist)
        self.strike_btn.clicked.connect(self.toggle_strike)
        self.bold_btn.clicked.connect(self.toggle_bold)
        for keys, fn in (("Ctrl+N", lambda: self.board.new_note(near=self)), ("Ctrl+D", self.delete)):
            QShortcut(QKeySequence(keys), self, fn, context=Qt.WindowShortcut)

        self.bounce_timer = QTimer(self)
        self.bounce_timer.setInterval(16)
        self.bounce_timer.timeout.connect(self._bounce_step)
        self.rebounce_timer = QTimer(self)
        self.rebounce_timer.setInterval(REBOUNCE_S * 1000)
        self.rebounce_timer.timeout.connect(self._rebounce)
        self.top_timer = QTimer(self)
        self.top_timer.setSingleShot(True)
        self.top_timer.setInterval(ON_TOP_S * 1000)
        self.top_timer.timeout.connect(lambda: self._float(False))

        self.body.document().setMarkdown(data["text"], MARKDOWN)
        self.body.textChanged.connect(self._text_changed)
        self.body.cursorPositionChanged.connect(self._sync_buttons)
        self.body.currentCharFormatChanged.connect(lambda _f: self._sync_buttons())
        self.setGeometry(data["x"], data["y"], data["w"], data["h"])
        self.retheme()
        self._sync_reminder()

    # ------------------------------------------------------------- look
    def _apply_flags(self) -> None:
        flags = Qt.FramelessWindowHint | Qt.Tool | Qt.NoDropShadowWindowHint
        if self.data["on_top"]:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)

    def retheme(self) -> None:
        theme = self.board.ctrl.theme
        self.bg, self.strip, self.ink = colors(theme, self.data["color"])
        for b in (self.add_btn, self.color_btn, self.pin_btn, self.bell_btn, self.trash_btn, self.close_btn,
                  self.bullets_btn, self.check_btn, self.strike_btn, self.bold_btn):
            b.ink = self.ink
            b.update()
        self.chip.ink = self.ink
        self.chip.update()
        self.color_btn.swatch = self.strip.darker(115)
        self.body.ink, self.body.bg = self.ink, self.bg
        self.body.highlighter.set_ink(self.ink)
        sel = QColor(self.ink)
        self.body.setStyleSheet(
            f"QTextEdit {{ background: transparent; border: none; color: {self.ink.name()}; font-size: 14px; "
            f"selection-background-color: rgba({sel.red()},{sel.green()},{sel.blue()},60); "
            f"selection-color: {self.ink.name()}; }}")
        self.update()

    def paintEvent(self, _event) -> None:
        theme = self.board.ctrl.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        card = QRectF(self.rect()).adjusted(SHADOW, SHADOW, -SHADOW, -SHADOW)
        p.setPen(Qt.NoPen)
        for i in range(SHADOW, 0, -1):
            p.setBrush(theme.shadow(int(theme.shadow_alpha * 0.3 * (1 - i / SHADOW) ** 2)))
            p.drawRoundedRect(card.adjusted(-i, -i + 3, i, i + 3), RADIUS + i, RADIUS + i)
        shape = QPainterPath()
        shape.addRoundedRect(card, RADIUS, RADIUS)
        p.fillPath(shape, self.bg)
        p.save()
        p.setClipPath(shape)
        p.fillRect(QRectF(card.left(), card.top(), card.width(), HEADER), self.strip)
        p.restore()
        edge = QColor(self.ink)
        ringing = bool(self.data["rang"])
        edge.setAlpha(150 if ringing else 28)
        p.setPen(QPen(edge, 2.2 if ringing else 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(card.adjusted(1, 1, -1, -1) if ringing else card.adjusted(0.5, 0.5, -0.5, -0.5),
                          RADIUS, RADIUS)
        p.end()

    def _color_menu(self) -> None:
        menu = _menu(self)
        theme = self.board.ctrl.theme
        for key in COLORS:
            dot = QPixmap(16, 16)
            dot.fill(Qt.transparent)
            painter = QPainter(dot)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(QPen(theme.border, 1))
            painter.setBrush(colors(theme, key)[0])
            painter.drawEllipse(QRectF(1.5, 1.5, 13, 13))
            painter.end()
            act = menu.addAction(QIcon(dot), COLOR_NAMES[key])
            act.setCheckable(True)
            act.setChecked(key == self.data["color"])
            act.triggered.connect(lambda _c=False, k=key: self.set_color(k))
        menu.exec(self.color_btn.mapToGlobal(QPoint(0, self.color_btn.height())))
        menu.deleteLater()

    # ------------------------------------------------------------- reminders
    def reminder(self) -> datetime | None:
        return parse_when(self.data["remind_at"])

    def reminder_menu(self, anchor: QWidget) -> None:
        now = datetime.now()
        menu = _menu(self)
        for label, moment in quick_times(now):
            act = menu.addAction(f"{label}  ({when_text(moment, now)})")
            act.triggered.connect(lambda _c=False, m=moment: self.set_reminder(m))
        menu.addAction("Pick a day and time…").triggered.connect(self._pick_time)
        if self.reminder() is not None or self.data["rang"]:
            menu.addSeparator()
            menu.addAction("No reminder").triggered.connect(lambda: self.set_reminder(None))
        menu.exec(anchor.mapToGlobal(QPoint(0, anchor.height())))
        menu.deleteLater()

    def _pick_time(self) -> None:
        start = self.reminder() or (datetime.now() + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        dialog = QDialog(self)
        dialog.setWindowTitle("Remind me")
        lay = QVBoxLayout(dialog)
        lay.addWidget(QLabel("When should this note remind you?"))
        picker = QDateTimeEdit(QDateTime(QDate(start.year, start.month, start.day), QTime(start.hour, start.minute)))
        picker.setCalendarPopup(True)
        picker.setDisplayFormat("ddd d MMM yyyy   h:mm AP")
        picker.setMinimumDateTime(QDateTime.currentDateTime())
        t = self.board.ctrl.theme
        picker.setStyleSheet(f"QDateTimeEdit {{ background: {t.raised}; color: {t.ink}; border: 1px solid "
                             f"{t.border_qss}; border-radius: 7px; padding: 5px 8px; }}")
        lay.addWidget(picker)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Remind me")
        buttons.button(QDialogButtonBox.Ok).setObjectName("primary")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        lay.addWidget(buttons)
        if dialog.exec() == QDialog.Accepted:
            self.set_reminder(picker.dateTime().toPython().replace(second=0, microsecond=0))
        dialog.deleteLater()

    def set_reminder(self, moment: datetime | None) -> None:
        self.data["remind_at"] = moment.isoformat(timespec="minutes") if moment else ""
        self.acknowledge()
        self._sync_reminder()
        self.changed.emit()
        if moment is not None and moment <= datetime.now():
            self.board.check_reminders()

    def _sync_reminder(self) -> None:
        when, now = self.reminder(), datetime.now()
        rang = parse_when(self.data["rang"])
        self.bell_btn.lit = when is not None
        self.bell_btn.setToolTip(f"Reminds you {when_text(when, now)}" if when else "Remind me…")
        self.bell_btn.update()
        if rang is not None:
            late = now - rang > timedelta(minutes=5)
            self.chip.set_text(f"{'Missed' if late else 'Now'}: {when_text(rang, now)}")
            self.chip.setToolTip("Click the note to put the reminder to rest, or set another")
        elif when is not None:
            self.chip.set_text(when_text(when, now))
            self.chip.setToolTip("Change or remove the reminder")
        self.chip.ringing = rang is not None
        self.chip.setVisible(rang is not None or when is not None)
        self.update()

    def ring(self, due: datetime) -> None:
        """The reminder's time came: to the front, a bounce, and again in a minute if you don't look."""
        self.data["remind_at"] = ""
        self.data["rang"] = due.isoformat(timespec="minutes")
        self._rang_at = time.monotonic()
        self._sync_reminder()
        self.show_note()
        self._float(True)
        self.bounce()
        self._bounces_left = REBOUNCES
        self.rebounce_timer.start()
        self.changed.emit()

    def acknowledge(self) -> None:
        """You've seen it: no more bouncing."""
        self.rebounce_timer.stop()
        if self.data["rang"]:
            self.data["rang"] = ""
            self._sync_reminder()
            self.changed.emit()

    def _rebounce(self) -> None:
        if self._bounces_left <= 0 or not self.data["rang"]:
            self.rebounce_timer.stop()
            return
        self._bounces_left -= 1
        self._rang_at = time.monotonic()
        self.show_note()
        self._float(True)
        self.bounce()

    def _float(self, on: bool) -> None:
        """Above every window for a moment while it rings (a pinned note always is)."""
        handle = self.windowHandle()
        if handle is None or self.data["on_top"]:
            return
        flags = handle.flags()
        handle.setFlags(flags | Qt.WindowStaysOnTopHint if on else flags & ~Qt.WindowStaysOnTopHint)
        if on:
            self.raise_()
            self.top_timer.start()

    def bounce(self) -> None:
        if self._bounce_t0 is None:
            self._bounce_base = self.pos()
        self._bounce_t0 = time.monotonic()
        self.bounce_timer.start()

    def _bounce_step(self) -> None:
        t = time.monotonic() - (self._bounce_t0 or 0.0)
        span = 1.5
        if t >= span:
            self.bounce_timer.stop()
            self._bounce_t0 = None
            self.move(self._bounce_base)
            return
        hop = abs(math.sin(t / span * 3 * math.pi)) * 22 * (1 - t / span)  # three hops, each a little lower
        self.move(self._bounce_base.x(), self._bounce_base.y() - round(hop))

    # ------------------------------------------------------------- changes
    def set_color(self, key: str) -> None:
        self.data["color"] = key
        self.retheme()
        self.changed.emit()

    def set_on_top(self, on: bool) -> None:
        self.data["on_top"] = on
        geometry, visible = self.geometry(), self.isVisible()
        self._apply_flags()  # changing flags hides the window: put it back where it was
        self.setGeometry(geometry)
        if visible:
            self.show()
        self.changed.emit()

    def _text_changed(self) -> None:
        self.data["text"] = self.body.document().toMarkdown(MARKDOWN).strip()[:MAX_TEXT]
        self.changed.emit()

    def _sync_buttons(self) -> None:
        fmt = self.body.currentCharFormat()
        cursor = self.body.textCursor()
        checklist = cursor.blockFormat().marker() != NO_MARKER
        for button, on in ((self.bullets_btn, cursor.currentList() is not None and not checklist),
                           (self.check_btn, checklist),
                           (self.strike_btn, fmt.fontStrikeOut()),
                           (self.bold_btn, fmt.fontWeight() >= QFont.DemiBold)):
            button.blockSignals(True)
            button.setChecked(on)
            button.blockSignals(False)

    def _selected_blocks(self):
        cursor = self.body.textCursor()
        start, end = cursor.selectionStart(), cursor.selectionEnd()
        block = self.body.document().findBlock(start)
        while block.isValid() and block.position() <= end:
            yield block
            block = block.next()

    def toggle_bullets(self) -> None:
        cursor = self.body.textCursor()
        cursor.beginEditBlock()
        listed = cursor.currentList() is not None and cursor.blockFormat().marker() == NO_MARKER
        if listed:
            for block in list(self._selected_blocks()):
                if block.textList() is not None:
                    block.textList().remove(block)
                    fix = QTextCursor(block)
                    fmt = QTextBlockFormat(fix.blockFormat())
                    fmt.setIndent(0)
                    fix.setBlockFormat(fmt)
        else:
            for block in list(self._selected_blocks()):  # checklist items become plain bullets
                fix = QTextCursor(block)
                fmt = QTextBlockFormat(fix.blockFormat())
                fmt.setMarker(NO_MARKER)
                fix.setBlockFormat(fmt)
            if cursor.currentList() is None:
                cursor.createList(QTextListFormat.ListDisc)
        cursor.endEditBlock()
        self.body.highlighter.rehighlight()
        self.body.setFocus()
        self._sync_buttons()

    def toggle_checklist(self) -> None:
        cursor = self.body.textCursor()
        blocks = list(self._selected_blocks())
        on = any(b.blockFormat().marker() == NO_MARKER for b in blocks)
        cursor.beginEditBlock()
        if on and cursor.currentList() is None:
            cursor.createList(QTextListFormat.ListDisc)
            blocks = list(self._selected_blocks())
        for block in blocks:
            fix = QTextCursor(block)
            fmt = QTextBlockFormat(fix.blockFormat())
            if on:
                if fmt.marker() == NO_MARKER:
                    fmt.setMarker(UNCHECKED)
            else:
                fmt.setMarker(NO_MARKER)
            fix.setBlockFormat(fmt)
        cursor.endEditBlock()
        self.body.highlighter.rehighlight()
        self.body.setFocus()
        self._sync_buttons()

    def toggle_strike(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontStrikeOut(not self.body.currentCharFormat().fontStrikeOut())
        self.body._merge(fmt)
        self.body.setFocus()
        self._sync_buttons()

    def toggle_bold(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontWeight(QFont.Normal if self.body.currentCharFormat().fontWeight() >= QFont.DemiBold else QFont.Bold)
        self.body._merge(fmt)
        self.body.setFocus()
        self._sync_buttons()

    def hide_note(self) -> None:
        self.acknowledge()
        self.data["open"] = False
        self.hide()
        self.changed.emit()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == event.Type.ActivationChange:
            if self.isActiveWindow():
                if time.monotonic() - self._rang_at > 1.0:  # not the jump to the front itself
                    self.acknowledge()  # you're looking at it
            else:
                self.board.save_now()  # clicked away: saved right now, not in a moment

    def delete(self) -> None:
        if self.body.toPlainText().strip():
            box = QMessageBox(QMessageBox.Question, "Delete sticky note", "Delete this sticky note?",
                              QMessageBox.Yes | QMessageBox.No, self)
            box.setDefaultButton(QMessageBox.No)
            box.setInformativeText("To just put it away, use × instead: hidden notes come back from Pomo's menu.")
            if box.exec() != QMessageBox.Yes:
                return
        self.board.remove(self)

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        if self._bounce_t0 is None:  # a bounce isn't a move
            self._remember_geometry()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._remember_geometry()

    def _remember_geometry(self) -> None:
        g = self.geometry()
        new = {"x": g.x(), "y": g.y(), "w": g.width(), "h": g.height()}
        if any(self.data[k] != v for k, v in new.items()):
            self.data.update(new)
            self.changed.emit()

    def show_note(self) -> None:
        if not self.data["open"]:
            self.data["open"] = True
            self.changed.emit()
        keep_on_screen(self)
        self.show()
        self.raise_()

    def first_line(self) -> str:
        line = next((ln.strip() for ln in self.body.toPlainText().splitlines() if ln.strip()), "")
        return line if len(line) <= 60 else line[:59] + "…"


def _menu(parent: QWidget) -> QMenu:
    menu = QMenu(parent)
    menu.setWindowFlags(menu.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
    menu.setAttribute(Qt.WA_TranslucentBackground)
    return menu


def keep_on_screen(widget: QWidget) -> None:
    """Back onto a screen if the one it was on is gone (a monitor unplugged)."""
    g = widget.geometry()
    for screen in QGuiApplication.screens():
        if screen.availableGeometry().intersects(QRect(g.x() + 20, g.y() + 20, 60, 40)):
            return
    area = QGuiApplication.primaryScreen().availableGeometry()
    widget.move(area.center().x() - g.width() // 2, area.center().y() - g.height() // 2)


# ------------------------------------------------------------------ all of them
class StickyBoard(QObject):
    """Every sticky note: makes, shows, hides and saves them, and rings their reminders."""

    created = Signal()  # you made a new one

    def __init__(self, ctrl, path: Path):
        super().__init__()
        self.ctrl, self.path = ctrl, path
        self.notes: list[StickyNote] = []
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(SAVE_DELAY_MS)
        self._save_timer.timeout.connect(self.save_now)
        self._remind_timer = QTimer(self)
        self._remind_timer.setInterval(REMIND_CHECK_MS)
        self._remind_timer.timeout.connect(self.check_reminders)
        self._remind_timer.start()
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.save_now)
            app.commitDataRequest.connect(lambda _manager: self.save_now())  # Windows is shutting down

    def restore(self) -> None:
        for data in load(self.path):
            note = self._make(data)
            if data["open"] or data["rang"]:
                keep_on_screen(note)
                note.show()
        QTimer.singleShot(2500, self.check_reminders)  # what came due while Pomo (or the PC) was off

    def _make(self, data: dict) -> StickyNote:
        note = StickyNote(self, data)
        note.changed.connect(self._save_timer.start)
        self.notes.append(note)
        return note

    def new_note(self, near: QWidget | None = None) -> StickyNote:
        """A fresh note: next to the one you clicked + on, else by the mouse. Each takes the next color."""
        color = COLORS[len(self.notes) % 3]
        w, h = DEFAULT_SIZE
        if near is not None:
            pos = near.pos() + QPoint(28, 28)
        else:
            cursor = QCursor.pos()
            pos = QPoint(cursor.x() - w // 2, cursor.y() + 16)
        screen = QGuiApplication.screenAt(pos + QPoint(w // 2, h // 2)) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        x = max(area.left(), min(pos.x(), area.right() - w))
        y = max(area.top(), min(pos.y(), area.bottom() - h))
        data = clean([{"x": x, "y": y, "w": w, "h": h, "color": color}])[0]
        note = self._make(data)
        note.show()
        note.raise_()
        note.activateWindow()
        note.body.setFocus()
        self.save_now()
        self.created.emit()
        return note

    def check_reminders(self, now: datetime | None = None) -> list[StickyNote]:
        """Ring every note whose time has come. Returns them."""
        now = now or datetime.now()
        rung = []
        for note in list(self.notes):
            due = note.reminder()
            if due is not None and due <= now:
                note.ring(due)
                rung.append(note)
                announce = getattr(self.ctrl, "sticky_reminder", None)
                if announce is not None:
                    announce(note, late=now - due > timedelta(minutes=5))
        if rung:
            self.save_now()
        return rung

    def upcoming(self) -> list[tuple[datetime, StickyNote]]:
        return sorted(((n.reminder(), n) for n in self.notes if n.reminder() is not None), key=lambda x: x[0])

    def remove(self, note: StickyNote) -> None:
        if note in self.notes:
            self.notes.remove(note)
        note.hide()
        note.deleteLater()
        self.save_now()

    def show_all(self) -> None:
        for note in self.notes:
            note.show_note()

    def hide_all(self) -> None:
        for note in self.notes:
            if note.isVisible():
                note.hide_note()

    def count(self) -> int:
        return len(self.notes)

    def shown(self) -> int:
        return sum(1 for n in self.notes if n.isVisible())

    def retheme(self) -> None:
        for note in self.notes:
            note.retheme()

    def save_now(self) -> None:
        self._save_timer.stop()
        try:
            save(self.path, [n.data for n in self.notes])
        except OSError:
            pass
