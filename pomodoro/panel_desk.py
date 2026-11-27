"""The panel's desk tabs: Clipboard, Shelf, Launcher and Docs, the small tools you reach for between tasks.

Clipboard remembers what you copy, in memory only (never on disk), and skips anything a password manager
marks private. Shelf keeps the files you're working with a drag away. Launcher is a little dock of apps you
pin. Docs lists what you opened lately, read from Windows' own Recent folder, so Pomo tracks nothing itself.
"""
from __future__ import annotations

import ctypes
import hashlib
import itertools
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QFileInfo, QMimeData, QObject, QPointF, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QDrag,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QIcon,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QTextLayout,
    QTextOption,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QFileDialog,
    QFileIconProvider,
    QFrame,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QStyle,
    QStyledItemDelegate,
    QWidget,
)

from .icons import draw_glyph
from .panel import Card, Page, label, row
from .panel_pages import chip
from .theme import FONT_FAMILY, mode_color

MAX_TEXTS = 30
MAX_IMAGES = 8
MAX_TEXT_CHARS = 100_000             # a whole log file copied is too big to keep around
IMAGE_BUDGET = 160 * 1024 * 1024     # bytes of full images at most (eight 4K screenshots would be 265 MB)
THUMB = QSize(120, 80)               # thumbnails are drawn at about half this, so they stay sharp on HiDPI
SHELF_MAX = 40
LAUNCHER_MAX = 24
APP_EXTS = (".exe", ".lnk", ".url")
RECENT_SCAN = 200                    # newest shortcuts looked at in the Recent folder
OWN_FORMAT = "application/x-pomo-clip"  # marks what copy() put back, so it isn't added a second time
# Formats the copying app sets to ask clipboard tools to look away (password managers do). On Windows Qt
# names them like 'application/x-qt-windows-mime;value="Clipboard Viewer Ignore"'.
PRIVATE_FORMATS = ("excludeclipboardcontentfrommonitorprocessing", "clipboard viewer ignore")
HISTORY_FORMAT = "canincludeinclipboardhistory"  # a DWORD; 0 means keep it out of any history

KEY = Qt.UserRole          # what a tile stands for: a path, or a clipboard entry's id
SUB = Qt.UserRole + 1      # the muted second line
CORNER = Qt.UserRole + 2   # muted text at the top right (a time)
PINNED = Qt.UserRole + 3
FLASH = Qt.UserRole + 4    # just copied: tinted for a moment


# ------------------------------------------------------------------ small helpers
def _font(px: int, bold: bool = False) -> QFont:
    font = QFont(FONT_FAMILY)
    font.setPixelSize(px)
    if bold:
        font.setWeight(QFont.DemiBold)
    return font


def _menu(parent: QWidget) -> QMenu:
    """Frameless and translucent, so the app stylesheet's rounded corners render cleanly."""
    menu = QMenu(parent)
    menu.setWindowFlags(menu.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
    menu.setAttribute(Qt.WA_TranslucentBackground)
    return menu


def _key(path: str) -> str:
    """The same file however it's spelled (case, slashes)."""
    return os.path.normcase(os.path.normpath(path))


def dedupe(paths: list[str], cap: int) -> list[str]:
    """paths without repeats (the first one wins) or blanks, at most cap of them."""
    seen: set[str] = set()
    out: list[str] = []
    for path in paths:
        if not isinstance(path, str) or not path.strip() or _key(path) in seen:
            continue
        seen.add(_key(path))
        out.append(os.path.normpath(path))
    return out[:cap]


def file_mime(paths: list[str]) -> QMimeData:
    """What other apps expect when files are dragged in: their URLs (Windows turns them into a file drop)."""
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
    return mime


def _panel_aside() -> None:
    """Something is opening in its own window: the panel steps aside, or the window would open behind it."""
    from .panel import Panel

    for widget in QApplication.topLevelWidgets():
        if isinstance(widget, Panel) and widget.isVisible():
            widget.close_panel(animate=False)


def open_path(path: str) -> bool:
    """Open a file, folder, app or shortcut the way double-clicking it in Explorer would. False if it can't."""
    try:
        if path.lower().endswith(".exe"):
            os.startfile(path, cwd=os.path.dirname(path) or None)  # some apps look for files next to them
        else:
            os.startfile(path)
    except OSError:
        return False
    _panel_aside()
    return True


def show_in_folder(path: str) -> None:
    """Open Explorer at path's folder with it selected."""
    subprocess.Popen(f'explorer /select,"{os.path.normpath(path)}"')
    _panel_aside()


def two_lines(text: str, font: QFont, width: float) -> list[str]:
    """text in at most two lines of width (runs of whitespace fold to one space), the second one elided."""
    flat = " ".join(text[:800].split())
    if not flat:
        return []
    metrics = QFontMetricsF(font)
    if metrics.horizontalAdvance(flat) <= width:
        return [flat]
    layout = QTextLayout(flat, font)
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(option)
    layout.beginLayout()
    line = layout.createLine()
    line.setLineWidth(max(1.0, width))
    cut = max(1, line.textLength())
    layout.endLayout()
    first, rest = flat[:cut].rstrip(), flat[cut:].lstrip()
    return [first, metrics.elidedText(rest, Qt.ElideRight, width)] if rest else [first]


class _Note(QLabel):
    """A short muted message in a card's header that clears itself."""

    def __init__(self):
        super().__init__("")
        self.setObjectName("muted")
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.clear)

    def say(self, text: str, ms: int = 2500) -> None:
        self.setText(text)
        self._timer.start(ms)


class _Icons:
    """File icons from Windows, looked up once per path (the shell call isn't free)."""

    def __init__(self):
        self._provider: QFileIconProvider | None = None
        self._cache: dict[str, QIcon] = {}

    def get(self, path: str) -> QIcon:
        if path not in self._cache:
            if self._provider is None:
                self._provider = QFileIconProvider()
            self._cache[path] = self._provider.icon(QFileInfo(path))
        return self._cache[path]


# ------------------------------------------------------------------ the tile grid every desk tab uses
class TileList(QListWidget):
    """A grid of tiles that fills its card's width (as many columns as fit), with a hint while it's empty.

    Rows show an icon or thumbnail, a title (one line, or two for clipboard text) and a muted line under it;
    stacked tiles (the launcher) put the icon over the name, like a dock.
    """

    def __init__(self, ctrl, min_w: int, tile_h: int, icon: int = 0, lines: int = 1, stacked: bool = False,
                 hint: str = "", glyph: str = "", drag_out: bool = False):
        super().__init__()
        self.ctrl = ctrl
        self.min_w, self.tile_h, self.icon_px, self.lines, self.stacked = min_w, tile_h, icon, lines, stacked
        self.hint, self.glyph = hint, glyph
        self.tile_w = min_w
        self.setFrameShape(QFrame.NoFrame)
        self.setViewMode(QListView.ListMode)
        self.setFlow(QListView.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setSpacing(0)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setFocusPolicy(Qt.NoFocus)
        self.setMouseTracking(True)
        self.viewport().setAttribute(Qt.WA_Hover, True)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.setItemDelegate(TileDelegate(self))
        if drag_out:
            self.setDragEnabled(True)
            self.setDragDropMode(QAbstractItemView.DragOnly)
            self.setDefaultDropAction(Qt.CopyAction)

    def _fit(self) -> None:
        # A left-to-right list keeps room for its scroll bar whether it shows or not (Qt's own layout does
        # the same), so a full row never wraps one column early.
        extent = self.style().pixelMetric(QStyle.PM_ScrollBarExtent, None, self.verticalScrollBar())
        width = max(1, self.maximumViewportSize().width() - extent - 2)
        self.tile_w = width // max(1, width // self.min_w)

    def resizeEvent(self, event) -> None:
        self._fit()  # before the list lays itself out again for the new width
        super().resizeEvent(event)

    def startDrag(self, _actions) -> None:
        """Drag a file out to another app. Copy or link only: a drop must never move your file away."""
        item = self.currentItem()
        path = item.data(KEY) if item is not None else None
        if not isinstance(path, str):
            return
        drag = QDrag(self)
        drag.setMimeData(file_mime([path]))
        icon = item.data(Qt.DecorationRole)
        if isinstance(icon, QIcon):
            drag.setPixmap(icon.pixmap(32, 32))
        drag.exec(Qt.CopyAction | Qt.LinkAction, Qt.CopyAction)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self.count() or not self.hint:
            return
        t = self.ctrl.theme
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.Antialiasing)
        area = QRectF(self.viewport().rect()).adjusted(24, 0, -24, 0)
        text_top = area.center().y() - 10
        if self.glyph:
            draw_glyph(p, self.glyph, QPointF(area.center().x(), area.center().y() - 22), 30, QColor(t.muted))
            text_top += 14
        p.setFont(_font(13))
        p.setPen(QColor(t.muted))
        p.drawText(QRectF(area.left(), text_top, area.width(), area.bottom() - text_top),
                   Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap, self.hint)
        p.end()


class TileDelegate(QStyledItemDelegate):
    def __init__(self, view: TileList):
        super().__init__(view)
        self.view = view

    def sizeHint(self, _option, _index) -> QSize:
        return QSize(self.view.tile_w, self.view.tile_h)

    def paint(self, p: QPainter, option, index) -> None:
        v = self.view
        t = v.ctrl.theme
        accent = mode_color(t, v.ctrl.settings, "focus")
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(option.rect).adjusted(3, 3, -3, -3)
        if index.data(FLASH):
            tint = QColor(accent)
            tint.setAlpha(70)
            p.setPen(QPen(accent, 1.4))
            p.setBrush(tint)
        elif option.state & QStyle.State_MouseOver:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(t.hover))
        elif v.stacked:
            p.setPen(Qt.NoPen)
            p.setBrush(Qt.NoBrush)
        else:
            p.setPen(QPen(t.border, 1))
            p.setBrush(QColor(t.surface))
        p.drawRoundedRect(r, 12, 12)
        if v.stacked:
            self._stacked(p, r, index)
        else:
            self._row(p, r, index)
        p.restore()

    def _picture(self, p: QPainter, box: QRectF, deco) -> None:
        if isinstance(deco, QIcon) and deco.isNull():  # Windows has no icon for it (moved or uninstalled)
            draw_glyph(p, "doc", box.center(), box.width() * 0.8, QColor(self.view.ctrl.theme.muted))
        elif isinstance(deco, QIcon):
            deco.paint(p, box.toRect())
        elif isinstance(deco, QPixmap) and not deco.isNull():
            size = QSize(deco.width(), deco.height()).scaled(box.size().toSize(), Qt.KeepAspectRatio)
            fit = QRectF(0, 0, size.width(), size.height())
            fit.moveCenter(box.center())
            clip = QPainterPath()
            clip.addRoundedRect(fit, 6, 6)
            p.save()
            p.setClipPath(clip)
            p.drawPixmap(fit, deco, QRectF(deco.rect()))
            p.restore()
            p.setPen(QPen(self.view.ctrl.theme.border, 1))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(fit, 6, 6)

    def _row(self, p: QPainter, r: QRectF, index) -> None:
        v, t = self.view, self.view.ctrl.theme
        pad = 10.0
        x, right = r.left() + pad, r.right() - pad
        deco = index.data(Qt.DecorationRole)
        if isinstance(deco, QPixmap):  # a thumbnail: as tall as the tile allows
            h = r.height() - 14
            box = QRectF(x, r.center().y() - h / 2, h * 1.5, h)
            self._picture(p, box, deco)
            x = box.right() + 10
        elif v.icon_px and isinstance(deco, QIcon):
            box = QRectF(x, r.center().y() - v.icon_px / 2, v.icon_px, v.icon_px)
            self._picture(p, box, deco)
            x = box.right() + 10
        if index.data(PINNED):
            draw_glyph(p, "pin_on", QPointF(right - 5, r.top() + 13), 14, QColor(t.ink_2))
            right -= 16
        width = max(10.0, right - x)
        title, sub, corner = index.data(Qt.DisplayRole) or "", index.data(SUB) or "", index.data(CORNER) or ""
        title_font, small = _font(13), _font(12)
        lines = two_lines(title, title_font, width) if v.lines == 2 and not sub else [title]
        line_h, sub_h = 18.0, 16.0
        y = r.center().y() - (len(lines) * line_h + (sub_h if sub else 0)) / 2
        if corner:
            corner_w = QFontMetricsF(small).horizontalAdvance(corner)
            p.setFont(small)
            p.setPen(QColor(t.muted))
            p.drawText(QRectF(right - corner_w, y, corner_w, line_h), Qt.AlignRight | Qt.AlignVCenter, corner)
        p.setFont(title_font)
        p.setPen(QColor(t.ink))
        metrics = QFontMetricsF(title_font)
        for i, line in enumerate(lines):
            w = width - (corner_w + 10 if corner and i == 0 else 0)
            if len(lines) == 1:
                line = metrics.elidedText(line, Qt.ElideRight, w)
            p.drawText(QRectF(x, y, w, line_h), Qt.AlignLeft | Qt.AlignVCenter, line)
            y += line_h
        if sub:
            p.setFont(small)
            p.setPen(QColor(t.muted))
            p.drawText(QRectF(x, y, width, sub_h), Qt.AlignLeft | Qt.AlignVCenter,
                       QFontMetricsF(small).elidedText(sub, Qt.ElideMiddle, width))

    def _stacked(self, p: QPainter, r: QRectF, index) -> None:
        v, t = self.view, self.view.ctrl.theme
        size = v.icon_px
        box = QRectF(r.center().x() - size / 2, r.top() + 8, size, size)
        self._picture(p, box, index.data(Qt.DecorationRole))
        font = _font(12)
        p.setFont(font)
        p.setPen(QColor(t.ink))
        y = box.bottom() + 5
        for line in two_lines(index.data(Qt.DisplayRole) or "", font, r.width() - 8):
            p.drawText(QRectF(r.left() + 4, y, r.width() - 8, 16), Qt.AlignHCenter | Qt.AlignVCenter, line)
            y += 16


# ------------------------------------------------------------------ Clipboard
def _native_name(fmt: str) -> str:
    """'application/x-qt-windows-mime;value="Name"' (how Qt shows a Windows-only format) -> 'name'."""
    found = re.search(r'value="([^"]*)"', fmt)
    return (found.group(1) if found else fmt).strip().lower()


def is_private(mime: QMimeData) -> bool:
    """True when the app that copied this asked clipboard tools not to keep it (password managers do)."""
    for fmt in mime.formats():
        name = _native_name(fmt)
        if name in PRIVATE_FORMATS:
            return True
        if name == HISTORY_FORMAT and not any(bytes(mime.data(fmt))):
            return True  # set to 0 (or unreadable, so to be safe)
    return False


_ids = itertools.count(1)


@dataclass(eq=False)
class ClipEntry:
    """Something you copied: text, or an image with a small thumbnail for the list."""

    key: str                    # what makes two copies the same
    text: str = ""
    image: QImage | None = None
    thumb: QImage | None = None
    pinned: bool = False
    id: int = field(default_factory=lambda: next(_ids))

    @property
    def is_image(self) -> bool:
        return self.image is not None


class ClipboardHistory(QObject):
    """What you copied lately, newest first: up to 30 texts and 8 images, kept in memory only.

    Nothing is ever written to disk. Whatever the copying app marks private is skipped, and while
    Settings.clipboard_on is off it doesn't look at the clipboard at all. Copying an entry again moves it
    to the top rather than adding it twice; pinned entries stay above the rest and are never trimmed.
    """

    changed = Signal()

    def __init__(self, ctrl, parent: QObject | None = None):
        super().__init__(parent)
        self.ctrl = ctrl
        self._items: list[ClipEntry] = []
        self._read = QTimer(self)
        self._read.setSingleShot(True)
        self._read.setInterval(60)  # some apps update the clipboard a few times in a row: read it once
        self._read.timeout.connect(self._read_clipboard)
        QGuiApplication.clipboard().dataChanged.connect(self._clipboard_changed)

    @property
    def entries(self) -> list[ClipEntry]:
        """Pinned ones first, then the rest, newest first."""
        return [e for e in self._items if e.pinned] + [e for e in self._items if not e.pinned]

    def find(self, entry_id) -> ClipEntry | None:
        return next((e for e in self._items if e.id == entry_id), None)

    def _clipboard_changed(self) -> None:
        if self.ctrl.settings.clipboard_on:
            self._read.start()

    def _read_clipboard(self) -> None:
        if self.ctrl.settings.clipboard_on:
            self.take(QGuiApplication.clipboard().mimeData())

    def _content(self, mime: QMimeData) -> tuple[str, str, QImage | None] | None:
        """(key, text, image) for what mime holds, or None when there's nothing worth keeping."""
        if mime.hasUrls() and all(url.isLocalFile() for url in mime.urls()):
            return None  # files copied in Explorer: paths aren't what you'd paste back
        if mime.hasText() and mime.text().strip():
            text = mime.text()
            return ("t:" + text, text, None) if len(text) <= MAX_TEXT_CHARS else None
        if mime.hasImage():
            image = mime.imageData()
            if isinstance(image, QPixmap):
                image = image.toImage()
            if isinstance(image, QImage) and not image.isNull():
                image = image.convertToFormat(QImage.Format_ARGB32)  # one format, so equal images hash equal
                return "i:" + hashlib.blake2b(image.constBits(), digest_size=16).hexdigest(), "", image
        return None

    def take(self, mime: QMimeData | None) -> ClipEntry | None:
        """Remember what mime holds (the clipboard's new contents). Its entry, or None when it's skipped."""
        if mime is None or not self.ctrl.settings.clipboard_on or is_private(mime):
            return None
        if mime.hasFormat(OWN_FORMAT):  # put back by copy(), which already moved it to the top
            try:
                return self.find(int(bytes(mime.data(OWN_FORMAT)).decode()))
            except ValueError:
                return None
        content = self._content(mime)
        if content is None:
            return None
        key, text, image = content
        same = next((e for e in self._items if e.key == key), None)
        if same is not None:
            self._to_top(same)
            self.changed.emit()
            return same
        thumb = None
        if image is not None:
            small = image.width() <= THUMB.width() and image.height() <= THUMB.height()
            thumb = image if small else image.scaled(THUMB, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        entry = ClipEntry(key, text, image, thumb)
        self._items.insert(0, entry)
        self._trim()
        self.changed.emit()
        return entry

    def _to_top(self, entry: ClipEntry) -> None:
        self._items.remove(entry)
        self._items.insert(0, entry)

    def _trim(self) -> None:
        """Forget the oldest unpinned ones past the caps (and past the memory budget for images)."""
        texts = [e for e in self._items if not e.pinned and not e.is_image]
        images = [e for e in self._items if not e.pinned and e.is_image]
        drop = {e.id for e in texts[MAX_TEXTS:]}
        used = 0
        for i, entry in enumerate(images):
            used += entry.image.sizeInBytes()
            if i >= MAX_IMAGES or (i > 0 and used > IMAGE_BUDGET):  # the newest always stays
                drop.add(entry.id)
        self._items = [e for e in self._items if e.id not in drop]

    def copy(self, entry: ClipEntry) -> None:
        """Put entry back on the clipboard. It moves to the top, and isn't added a second time."""
        mime = QMimeData()
        if entry.is_image:
            mime.setImageData(entry.image)
        else:
            mime.setText(entry.text)
        mime.setData(OWN_FORMAT, str(entry.id).encode())
        if entry in self._items:
            self._to_top(entry)
        QGuiApplication.clipboard().setMimeData(mime)
        self.changed.emit()

    def pin(self, entry: ClipEntry, pinned: bool = True) -> None:
        """Keep entry at the top (and out of trimming), or let it go back among the rest."""
        if entry not in self._items:
            return
        entry.pinned = pinned
        if pinned:
            self._to_top(entry)
        self._trim()
        self.changed.emit()

    def remove(self, entry: ClipEntry) -> None:
        if entry in self._items:
            self._items.remove(entry)
            self.changed.emit()

    def clear(self, keep_pinned: bool = True) -> None:
        self._items = [e for e in self._items if keep_pinned and e.pinned]
        self.changed.emit()


class ClipboardPage(Page):
    """What you copied lately: click one to copy it again. Right-click to pin it to the top or remove it."""

    def __init__(self, ctrl, history: ClipboardHistory):
        super().__init__(ctrl)
        self.history = history
        self._flash = 0  # the entry just copied, tinted for a moment
        card = Card(ctrl, "Clipboard")
        self.note = _Note()
        card.head.insertWidget(1, self.note)
        card.action("trash", "Clear (pinned ones stay)", self.clear)
        self.list = TileList(ctrl, 250, 62, lines=2, glyph="clipboard")
        self.list.viewport().setCursor(Qt.PointingHandCursor)
        self.list.itemClicked.connect(self._clicked)
        self.list.customContextMenuRequested.connect(self._menu)
        card.body.addWidget(self.list, 1)
        self.remember = QCheckBox("Remember what I copy")
        self.remember.setCursor(Qt.PointingHandCursor)
        self.remember.setFocusPolicy(Qt.NoFocus)
        self.remember.setChecked(bool(ctrl.settings.clipboard_on))
        self.remember.toggled.connect(self._set_on)
        card.body.addLayout(row(label("Kept in memory only, never saved.", "muted"), None, self.remember))
        self.add(card, 1)
        self._unflash = QTimer(self)
        self._unflash.setSingleShot(True)
        self._unflash.setInterval(1200)
        self._unflash.timeout.connect(self._end_flash)
        history.changed.connect(self._changed)

    def _changed(self) -> None:
        if self.isVisible():
            self._fill()

    def refresh(self) -> None:
        self.remember.blockSignals(True)
        self.remember.setChecked(bool(self.ctrl.settings.clipboard_on))
        self.remember.blockSignals(False)
        self._fill()

    def _fill(self) -> None:
        on = bool(self.ctrl.settings.clipboard_on)
        self.list.hint = ("Copy some text or an image and it shows up here." if on
                          else "Paused. Turn on Remember what I copy to pick up again.")
        self.list.clear()
        for entry in self.history.entries:
            item = QListWidgetItem()
            if entry.is_image:
                item.setData(Qt.DisplayRole, "Image")
                item.setData(SUB, f"{entry.image.width()} × {entry.image.height()}")
                item.setData(Qt.DecorationRole, QPixmap.fromImage(entry.thumb))
            else:
                item.setData(Qt.DisplayRole, entry.text[:800])
            item.setData(KEY, entry.id)
            item.setData(PINNED, entry.pinned)
            item.setData(FLASH, entry.id == self._flash)
            item.setToolTip("Click to copy. Right-click to pin or remove.")
            self.list.addItem(item)
        self.list.viewport().update()

    def _clicked(self, item: QListWidgetItem) -> None:
        entry = self.history.find(item.data(KEY))
        if entry is not None:
            self.copy(entry)

    def copy(self, entry: ClipEntry) -> None:
        self.history.copy(entry)
        self._flash = entry.id
        self.note.say("Copied", 1500)
        self._unflash.start()
        self._fill()

    def _end_flash(self) -> None:
        self._flash = 0
        self._fill()

    def _set_on(self, on: bool) -> None:
        self.ctrl.settings.clipboard_on = bool(on)
        self.ctrl.save_settings()
        self.note.say("Remembering again" if on else "Paused")
        self._fill()

    def clear(self) -> None:
        self.history.clear()
        self._fill()

    def _menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        entry = self.history.find(item.data(KEY)) if item is not None else None
        if entry is None:
            return
        menu = _menu(self)
        menu.addAction("Copy", lambda: self.copy(entry))
        menu.addAction("Unpin" if entry.pinned else "Pin to the top", lambda: self.history.pin(entry, not entry.pinned))
        menu.addAction("Remove", lambda: self.history.remove(entry))
        menu.exec(self.list.viewport().mapToGlobal(pos))
        menu.deleteLater()
        self._fill()


# ------------------------------------------------------------------ pages you drop files on
class _DropPage(Page):
    """A page that takes files dropped anywhere on it (its card included); the card lights up meanwhile.

    A drop is always taken as a copy (or a link), never a move, so Explorer leaves your file where it is.
    """

    def __init__(self, ctrl):
        super().__init__(ctrl)
        self.setAcceptDrops(True)
        self.drop_card: Card | None = None
        self.icons = _Icons()

    def wants(self, path: str) -> bool:
        return True

    def take(self, paths: list[str]) -> None:
        raise NotImplementedError

    def _dropped(self, event) -> list[str]:
        source = event.source()
        if isinstance(source, QWidget) and (source is self or self.isAncestorOf(source)):
            return []  # dragged out of this very page
        mime = event.mimeData()
        if mime is None or not mime.hasUrls():
            return []
        paths = [os.path.normpath(url.toLocalFile()) for url in mime.urls() if url.isLocalFile()]
        return [p for p in paths if self.wants(p)]

    @staticmethod
    def _action(event):
        for action in (Qt.CopyAction, Qt.LinkAction):
            if event.possibleActions() & action:
                return action
        return None

    def _light(self, on: bool) -> None:
        if self.drop_card is None:
            return
        if on:
            accent = mode_color(self.ctrl.theme, self.ctrl.settings, "focus").name()
            self.drop_card.setStyleSheet(f"QFrame#panelCard {{ border: 2px solid {accent}; }}")
        else:
            self.drop_card.setStyleSheet("")

    def dragEnterEvent(self, event) -> None:
        action = self._action(event)
        if action is not None and self._dropped(event):
            event.setDropAction(action)
            event.accept()
            self._light(True)
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        action = self._action(event)
        if action is not None and self._dropped(event):
            event.setDropAction(action)
            event.accept()
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._light(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        self._light(False)
        action, paths = self._action(event), self._dropped(event)
        if action is None or not paths:
            event.ignore()
            return
        event.setDropAction(action)
        event.accept()
        self.take(paths)


# ------------------------------------------------------------------ Shelf
class ShelfPage(_DropPage):
    """Files and folders you're working with, kept handy: drag them back out to any app, or open them."""

    def __init__(self, ctrl):
        super().__init__(ctrl)
        card = Card(ctrl, "Shelf")
        self.note = _Note()
        card.head.insertWidget(1, self.note)
        card.action("trash", "Clear the shelf (your files stay where they are)", self.clear)
        self.list = TileList(ctrl, 230, 56, icon=32, glyph="shelf", drag_out=True,
                             hint="Drop files and folders here to keep them handy.")
        self.list.itemDoubleClicked.connect(lambda item: self.open(item.data(KEY)))
        self.list.customContextMenuRequested.connect(self._menu)
        card.body.addWidget(self.list, 1)
        card.body.addWidget(label("Drag them back out into any app. Double-click to open.", "muted"))
        self.add(card, 1)
        self.drop_card = card

    @property
    def paths(self) -> list[str]:
        return list(self.ctrl.settings.shelf_items)

    def _save(self, paths: list[str]) -> None:
        self.ctrl.settings.shelf_items = dedupe(paths, SHELF_MAX)
        self.ctrl.save_settings()
        self._fill()

    def take(self, paths: list[str]) -> None:
        """Newest on the front of the shelf; past 40, the oldest ones fall off the end."""
        self._save(list(paths) + self.paths)

    def remove(self, path: str) -> None:
        self._save([p for p in self.paths if _key(p) != _key(path)])

    def clear(self) -> None:
        self._save([])

    def open(self, path) -> None:
        if isinstance(path, str) and not open_path(path):
            self.note.say("Couldn't open that. It may have moved.", 4000)

    def refresh(self) -> None:
        """Let go of whatever was moved or deleted since."""
        kept = [p for p in dedupe(self.paths, SHELF_MAX) if os.path.exists(p)]
        if kept != self.paths:
            self.ctrl.settings.shelf_items = kept
            self.ctrl.save_settings()
        self._fill()

    def _fill(self) -> None:
        self.list.clear()
        for path in self.paths:
            item = QListWidgetItem(os.path.basename(path.rstrip("\\/")) or path)
            item.setData(SUB, os.path.dirname(path.rstrip("\\/")) or path)
            item.setData(Qt.DecorationRole, self.icons.get(path))
            item.setData(KEY, path)
            item.setToolTip(path)
            self.list.addItem(item)
        self.list.viewport().update()

    def _menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if item is None:
            return
        path = item.data(KEY)
        menu = _menu(self)
        menu.addAction("Open", lambda: self.open(path))
        menu.addAction("Show in folder", lambda: show_in_folder(path))
        menu.addSeparator()
        menu.addAction("Remove from the shelf", lambda: self.remove(path))
        menu.exec(self.list.viewport().mapToGlobal(pos))
        menu.deleteLater()


# ------------------------------------------------------------------ Launcher
def app_name(path: str) -> str:
    """The name to show for an app: its file name without .exe, .lnk or .url."""
    return Path(path).stem or path


class LauncherPage(_DropPage):
    """Apps you pin, one click away. Drop programs or shortcuts on it, or pick them with Add app."""

    def __init__(self, ctrl):
        super().__init__(ctrl)
        card = Card(ctrl, "Launcher")
        self.note = _Note()
        card.head.insertWidget(1, self.note)
        self.list = TileList(ctrl, 92, 92, icon=36, stacked=True, glyph="grid",
                             hint="Drop apps or shortcuts here, or click Add app.")
        self.list.viewport().setCursor(Qt.PointingHandCursor)
        self.list.itemClicked.connect(lambda item: self.launch(item.data(KEY)))
        self.list.customContextMenuRequested.connect(self._menu)
        card.body.addWidget(self.list, 1)
        card.body.addLayout(row(chip("Add app", self.pick), None, label("Right-click an app to remove it.", "muted")))
        self.add(card, 1)
        self.drop_card = card

    @property
    def paths(self) -> list[str]:
        return list(self.ctrl.settings.launcher_apps)

    def wants(self, path: str) -> bool:
        return path.lower().endswith(APP_EXTS)

    def take(self, paths: list[str]) -> None:
        """New apps go on the end; once there are 24, more don't fit."""
        before = dedupe(self.paths, LAUNCHER_MAX)
        known = {_key(p) for p in before}
        new = [p for p in dedupe([p for p in paths if self.wants(p)], LAUNCHER_MAX) if _key(p) not in known]
        if len(before) + len(new) > LAUNCHER_MAX:
            self.note.say(f"The launcher holds up to {LAUNCHER_MAX} apps.", 4000)
        self._save(before + new)

    def _save(self, paths: list[str]) -> None:
        self.ctrl.settings.launcher_apps = dedupe(paths, LAUNCHER_MAX)
        self.ctrl.save_settings()
        self._fill()

    def remove(self, path: str) -> None:
        self._save([p for p in self.paths if _key(p) != _key(path)])

    def pick(self) -> None:
        start = Path(os.environ.get("ProgramData", "C:/ProgramData")) / "Microsoft/Windows/Start Menu/Programs"
        # Without DontResolveSymlinks the dialog hands back a shortcut's target, losing its name and icon.
        paths, _ = QFileDialog.getOpenFileNames(self, "Add apps", str(start),
                                                "Programs and shortcuts (*.exe *.lnk *.url)",
                                                options=QFileDialog.DontResolveSymlinks)
        if paths:
            self.take([os.path.normpath(p) for p in paths])

    def launch(self, path) -> None:
        if isinstance(path, str) and not open_path(path):
            self.note.say("Couldn't open that. It may have moved or been uninstalled.", 4000)

    def refresh(self) -> None:
        self._fill()

    def _fill(self) -> None:
        self.list.clear()
        for path in self.paths:
            item = QListWidgetItem(app_name(path))
            item.setData(Qt.DecorationRole, self.icons.get(path))
            item.setData(KEY, path)
            item.setToolTip(app_name(path))
            self.list.addItem(item)
        self.list.viewport().update()

    def _menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if item is None:
            return
        path = item.data(KEY)
        menu = _menu(self)
        menu.addAction("Open", lambda: self.launch(path))
        menu.addAction("Open file location", lambda: show_in_folder(path))
        menu.addSeparator()
        menu.addAction("Remove", lambda: self.remove(path))
        menu.exec(self.list.viewport().mapToGlobal(pos))
        menu.deleteLater()


# ------------------------------------------------------------------ Docs
@dataclass(frozen=True)
class RecentDoc:
    path: str
    name: str
    folder: str
    when: datetime  # when you last opened it


def recent_folder() -> Path:
    """Where Windows keeps a shortcut to each file you open (the same list as File Explorer's Recent)."""
    return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Recent"


_TARGETS: dict[str, tuple[float, str]] = {}  # shortcut -> (its modified time, its target): reading one is a shell call
_REMOTE: dict[str, bool] = {}


def _link_target(link: str, mtime: float) -> str:
    cached = _TARGETS.get(link)
    if cached is None or cached[0] != mtime:
        target = QFileInfo(link).symLinkTarget()
        cached = (mtime, os.path.normpath(target) if target else "")
        _TARGETS[link] = cached
    return cached[1]


def _remote(path: str) -> bool:
    """On a network share. Looking those up can hang for seconds while the share is offline."""
    if path.startswith(("\\\\", "//")):
        return True
    drive = os.path.splitdrive(path)[0].upper()
    if not drive:
        return False
    if drive not in _REMOTE:
        try:
            _REMOTE[drive] = ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") == 4  # DRIVE_REMOTE
        except (AttributeError, OSError):
            _REMOTE[drive] = False
    return _REMOTE[drive]


def recent_docs(folder: Path | None = None, limit: int = 60) -> list[RecentDoc]:
    """Files you opened lately, newest first, from the Recent folder. Folders and missing files are left out."""
    folder = recent_folder() if folder is None else Path(folder)
    links: list[tuple[float, str]] = []
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                if entry.name.lower().endswith(".lnk"):
                    try:
                        if entry.is_file():
                            links.append((entry.stat().st_mtime, entry.path))
                    except OSError:
                        pass
    except OSError:
        return []
    links.sort(reverse=True)
    docs: list[RecentDoc] = []
    seen: set[str] = set()
    for mtime, link in links[:RECENT_SCAN]:
        target = _link_target(link, mtime)
        if not target or _key(target) in seen:
            continue
        if _remote(target):
            if not os.path.splitext(target)[1]:
                continue  # most likely a folder, and checking could hang
        elif not os.path.isfile(target):
            continue
        seen.add(_key(target))
        docs.append(RecentDoc(target, os.path.basename(target), os.path.dirname(target), datetime.fromtimestamp(mtime)))
        if len(docs) >= limit:
            break
    return docs


def when_text(when: datetime, now: datetime) -> str:
    """'today 14:05', 'yesterday' or 'Nov 20' (with the year when it isn't this one)."""
    if when.date() == now.date():
        return f"today {when:%H:%M}"
    if when.date() == now.date() - timedelta(days=1):
        return "yesterday"
    return f"{when:%b} {when.day}" + ("" if when.year == now.year else f", {when.year}")


def matches(doc: RecentDoc, query: str) -> bool:
    """Every word of query is somewhere in the name or folder."""
    hay = f"{doc.name}\n{doc.folder}".lower()
    return all(word in hay for word in query.lower().split())


class DocsPage(Page):
    """Documents you opened lately, newest first, with a filter. Click one to open it again."""

    def __init__(self, ctrl, folder: Path | None = None):
        super().__init__(ctrl)
        self.folder = folder  # None: your own Recent folder (tests pass another)
        self.docs: list[RecentDoc] = []
        card = Card(ctrl, "Recent documents")
        self.note = _Note()
        self.filter = QLineEdit()
        self.filter.setObjectName("jot")
        self.filter.setStyleSheet("padding: 4px 10px;")  # header-sized; the panel styles the rest
        self.filter.setPlaceholderText("Filter")
        self.filter.setClearButtonEnabled(True)
        self.filter.setFixedWidth(240)
        self.filter.textChanged.connect(lambda _text: self._fill())
        card.head.insertWidget(1, self.note)
        card.head.insertWidget(2, self.filter)
        card.action("refresh", "Refresh", self.refresh)
        self.list = TileList(ctrl, 300, 56, icon=28, glyph="doc")
        self.list.viewport().setCursor(Qt.PointingHandCursor)
        self.list.itemClicked.connect(lambda item: self.open(item.data(KEY)))
        self.list.customContextMenuRequested.connect(self._menu)
        card.body.addWidget(self.list, 1)
        self.add(card, 1)
        self.icons = _Icons()

    def refresh(self) -> None:
        self.docs = recent_docs(self.folder)
        self._fill()

    def shown(self) -> list[RecentDoc]:
        query = self.filter.text().strip()
        return [d for d in self.docs if matches(d, query)] if query else list(self.docs)

    def _fill(self) -> None:
        query = self.filter.text().strip()
        self.list.hint = "Nothing matches that." if query and self.docs else \
            "Nothing yet. Files you open in any app show up here."
        self.list.clear()
        now = datetime.now()
        for doc in self.shown():
            item = QListWidgetItem(doc.name)
            item.setData(SUB, doc.folder)
            item.setData(CORNER, when_text(doc.when, now))
            item.setData(Qt.DecorationRole, self.icons.get(doc.path))
            item.setData(KEY, doc.path)
            item.setToolTip(doc.path)
            self.list.addItem(item)
        self.list.viewport().update()

    def open(self, path) -> None:
        if isinstance(path, str) and not open_path(path):
            self.note.say("Couldn't open that. It may have moved.", 4000)

    def _menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if item is None:
            return
        path = item.data(KEY)
        menu = _menu(self)
        menu.addAction("Open", lambda: self.open(path))
        menu.addAction("Show in folder", lambda: show_in_folder(path))
        menu.exec(self.list.viewport().mapToGlobal(pos))
        menu.deleteLater()


def register_pages() -> None:
    """Add the desk tabs to the panel. The Clipboard tab uses the app's ClipboardHistory at ctrl.clipboard."""
    from .panel import register
    register("clipboard", "Clipboard", "clipboard", lambda c: ClipboardPage(c, c.clipboard))
    register("shelf", "Shelf", "shelf", ShelfPage)
    register("launcher", "Launcher", "grid", LauncherPage)
    register("docs", "Docs", "doc", DocsPage)
