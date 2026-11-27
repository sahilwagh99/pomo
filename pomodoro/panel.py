"""Pomo's panel: the big expanded view, with a row of tabs and cards of things you reach for often.

On the island it drops down from the top of the screen, like a notch opening up; beside the orb it opens
next to it. Each tab is a page of cards (see panel_pages.py and friends). The panel opens without taking
the keyboard from whatever you're doing, and closes when the mouse leaves it, unless you pin it, you're
typing in it, or a menu is open.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from PySide6.QtCore import QEasingCurve, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QSizePolicy,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .icons import draw_glyph
from .theme import FONT_FAMILY, mode_color

SHADOW = 16
PANEL_W = 1200        # at most; narrower screens get a narrower panel
PANEL_H = 336
HEADER_H = 58
CLOSE_AFTER_S = 0.45  # how long the mouse may be away before the panel closes
RADIUS = 26
FLARE = 12            # the concave shoulders where the dropped-down panel meets the top of the screen
TAB_W = 60
GROW_MS = 380     # the panel growing out of the timer
SHRINK_MS = 190   # ...and tucking back into it
CLOCK_SAMPLE = "Wed, 30 Sep   12:00:00 AM"  # the widest the header's clock gets


@dataclass(frozen=True)
class PageSpec:
    key: str
    label: str
    glyph: str
    make: Callable  # (ctrl) -> QWidget; it may have refresh() (on show) and tick() (each second while shown)


PAGES: list[PageSpec] = []


def register(key: str, label: str, glyph: str, make: Callable) -> None:
    PAGES[:] = [p for p in PAGES if p.key != key] + [PageSpec(key, label, glyph, make)]


def _alpha(color, alpha: int) -> QColor:
    c = QColor(color)
    c.setAlpha(alpha)
    return c


# ------------------------------------------------------------------ building blocks for pages
class IconButton(QAbstractButton):
    """A round button with one of Pomo's glyphs. `lit` fills it with the accent (a toggle that's on)."""

    def __init__(self, ctrl, glyph: str, tip: str = "", size: int = 30, parent=None, checkable: bool = False):
        super().__init__(parent)
        self.ctrl, self.glyph = ctrl, glyph
        self.lit = False
        self.setToolTip(tip)
        self.setCheckable(checkable)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedSize(size, size)

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        t = self.ctrl.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        on = self.lit or self.isChecked()
        accent = mode_color(t, self.ctrl.settings, "focus")
        if on:
            p.setPen(Qt.NoPen)
            p.setBrush(accent)
            p.drawEllipse(rect)
        elif self.underMouse() and self.isEnabled():
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(t.hover))
            p.drawEllipse(rect)
        ink = QColor(t.on_accent if on else (t.ink if self.underMouse() else t.ink_2))
        if not self.isEnabled():
            ink = QColor(t.muted)
        draw_glyph(p, self.glyph, rect.center(), rect.width() * 0.62, ink)
        p.end()


class TabButton(QAbstractButton):
    """A tab: its glyph over its name, on a soft pill while it's the open one."""

    def __init__(self, ctrl, spec: PageSpec, parent=None):
        super().__init__(parent)
        self.ctrl, self.spec = ctrl, spec
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setToolTip(spec.label)
        self.setFixedSize(QSize(TAB_W, 50))

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        t = self.ctrl.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        accent = mode_color(t, self.ctrl.settings, "focus")
        if self.isChecked():
            p.setPen(QPen(_alpha(accent, 150), 1))
            p.setBrush(_alpha(accent, 70))
            p.drawRoundedRect(rect, 12, 12)
        elif self.underMouse():
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(t.hover))
            p.drawRoundedRect(rect, 12, 12)
        ink = QColor(t.ink if self.isChecked() or self.underMouse() else t.ink_2)
        draw_glyph(p, self.spec.glyph, QPointF(rect.center().x(), rect.top() + 16), 19, ink)
        font = QFont(FONT_FAMILY)
        font.setPixelSize(11)
        if self.isChecked():
            font.setWeight(QFont.DemiBold)
        p.setFont(font)
        p.setPen(ink)
        label = QFontMetricsF(font).elidedText(self.spec.label, Qt.ElideRight, rect.width() - 2)
        p.drawText(QRectF(rect.left(), rect.top() + 28, rect.width(), 16), Qt.AlignCenter, label)
        p.end()


class Card(QFrame):
    """A rounded card on a page, with an optional small title and action buttons along its top."""

    def __init__(self, ctrl, title: str = "", width: int | None = None, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.setObjectName("panelCard")
        if width:
            self.setFixedWidth(width)
        self.setSizePolicy(QSizePolicy.Preferred if width else QSizePolicy.Expanding, QSizePolicy.Expanding)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(8)
        self.head = QHBoxLayout()
        self.head.setSpacing(4)
        self.title = QLabel(title.upper())
        self.title.setObjectName("cardTitle")
        self.head.addWidget(self.title, 1)
        if title:
            outer.addLayout(self.head)
        self.body = QVBoxLayout()
        self.body.setSpacing(6)
        outer.addLayout(self.body, 1)

    def action(self, glyph: str, tip: str, fn, checkable: bool = False) -> IconButton:
        button = IconButton(self.ctrl, glyph, tip, 26, self, checkable)
        button.clicked.connect(lambda _c=False: fn())
        self.head.addWidget(button)
        return button


def label(text: str = "", role: str = "", wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    if role:
        lab.setObjectName(role)
    lab.setWordWrap(wrap)
    return lab


def row(*widgets, spacing: int = 6, stretch_at: int | None = None) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(0, 0, 0, 0)
    for i, w in enumerate(widgets):
        if w is None:
            lay.addStretch(1)
        elif isinstance(w, QHBoxLayout | QVBoxLayout):
            lay.addLayout(w)
        else:
            lay.addWidget(w, 1 if stretch_at == i else 0)
    return lay


class Page(QWidget):
    """A tab's page: a row of cards. Subclasses add cards and may refresh() and tick()."""

    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        self.cards = QHBoxLayout(self)
        self.cards.setContentsMargins(0, 0, 0, 0)
        self.cards.setSpacing(12)

    def add(self, card: QWidget, stretch: int = 0) -> QWidget:
        self.cards.addWidget(card, stretch)
        return card

    def refresh(self) -> None:
        pass

    def tick(self) -> None:
        pass


# ------------------------------------------------------------------ the panel window
class Panel(QWidget):
    closed = Signal()

    def __init__(self, ctrl):
        super().__init__(None)
        self.ctrl = ctrl
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle("Pomo panel")
        self.setAcceptDrops(True)         # files dragged onto the panel go on the Shelf
        self.attached = "island"          # or "orb": where it opens, and so its shape
        self.anchor = QRect()             # the timer, in screen coordinates: the mouse may rest there too
        self.pinned = False
        self._outside_since: float | None = None
        self._pages: dict[str, QWidget] = {}
        self._current = ""

        self.root = QVBoxLayout(self)
        self.root.setSpacing(0)
        # everything inside, in one widget, so it can fade in as the panel grows out of the timer
        self.content = QWidget()
        self.content.setAttribute(Qt.WA_TranslucentBackground)
        inner = QVBoxLayout(self.content)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(10)
        self.root.addWidget(self.content)
        self._still = None                 # a picture of the cards, while the panel grows or shrinks
        self._morph = 1.0                  # 0: the size of the timer it grows from, 1: open
        self._closing = False
        self._grow = QVariantAnimation(self)
        self._grow.valueChanged.connect(self._set_morph)
        self._grow.finished.connect(self._grown)
        head = QHBoxLayout()
        head.setSpacing(2)
        self.tab_group = QButtonGroup(self)
        self.tab_group.setExclusive(True)
        self.tabs: list[TabButton] = []
        for spec in PAGES:
            tab = TabButton(ctrl, spec)
            tab.clicked.connect(lambda _c=False, k=spec.key: self.show_page(k))
            self.tab_group.addButton(tab)
            self.tabs.append(tab)
            head.addWidget(tab)
        self.more = TabButton(ctrl, PageSpec("more", "More", "dots", lambda c: None))
        self.more.setCheckable(False)
        self.more.clicked.connect(self._more_menu)
        head.addWidget(self.more)
        head.addStretch(1)
        self.clock = QLabel()
        self.clock.setObjectName("panelClock")
        head.addWidget(self.clock)
        head.addSpacing(10)
        self.actions = QFrame()
        self.actions.setObjectName("panelActions")
        act = QHBoxLayout(self.actions)
        act.setContentsMargins(6, 3, 6, 3)
        act.setSpacing(2)
        self.ultra_btn = IconButton(ctrl, "bolt", "Ultra focus", 32)
        self.ultra_btn.clicked.connect(lambda: (self.close_panel(animate=False), ctrl.toggle_ultra()))
        sticky = IconButton(ctrl, "sticky", "New sticky note", 32)
        sticky.clicked.connect(lambda: (self.close_panel(animate=False), ctrl.stickies.new_note()))
        self.pin_btn = IconButton(ctrl, "pin", "Keep the panel open", 32)
        self.pin_btn.clicked.connect(self.toggle_pin)
        dash = IconButton(ctrl, "chart", "Dashboard", 32)
        dash.clicked.connect(lambda: (self.close_panel(), ctrl.open_dashboard()))
        gear = IconButton(ctrl, "gear", "Settings", 32)
        gear.clicked.connect(lambda: (self.close_panel(), ctrl.open_settings()))
        for b in (self.ultra_btn, sticky, self.pin_btn, dash, gear):
            act.addWidget(b)
        head.addWidget(self.actions)
        inner.addLayout(head)
        self.stack = QStackedWidget()
        inner.addWidget(self.stack, 1)

        self._poll = QTimer(self)
        self._poll.setInterval(140)
        self._poll.timeout.connect(self._check_pointer)
        self._second = QTimer(self)
        self._second.setInterval(1000)
        self._second.timeout.connect(self._tick)
        self.retheme()

    # ------------------------------------------------------------- look
    def retheme(self) -> None:
        t = self.ctrl.theme
        accent = mode_color(t, self.ctrl.settings, "focus")
        self.setStyleSheet(f"""
            QLabel {{ color: {t.ink}; background: transparent; }}
            QLabel#panelClock {{ color: {t.ink_2}; font-size: 13px; }}
            QFrame#panelActions {{ background: {t.raised}; border: 1px solid {t.border_qss}; border-radius: 20px; }}
            QFrame#panelCard {{ background: {t.raised}; border: 1px solid {t.border_qss}; border-radius: 18px; }}
            QLabel#cardTitle {{ color: {t.muted}; font-size: 11px; font-weight: 600; letter-spacing: 1px; }}
            QLabel#big {{ font-size: 28px; font-weight: 600; }}
            QLabel#title {{ font-size: 14px; font-weight: 600; }}
            QLabel#secondary {{ color: {t.ink_2}; }}
            QLabel#muted {{ color: {t.muted}; font-size: 12px; }}
            QListWidget {{ background: transparent; border: none; outline: none; color: {t.ink}; }}
            QListWidget::item {{ padding: 5px 6px; border-radius: 8px; }}
            QListWidget::item:hover {{ background: {t.hover}; }}
            QListWidget::item:selected {{ background: {t.hover}; color: {t.ink}; }}
            QPlainTextEdit#jot, QLineEdit#jot {{ background: {t.surface}; color: {t.ink}; border: 1px solid {t.border_qss};
                border-radius: 12px; padding: 8px; selection-background-color: {accent.name()};
                selection-color: {t.on_accent}; }}
            QPushButton#chip {{ background: {t.surface}; border: 1px solid {t.border_qss}; border-radius: 14px;
                padding: 5px 12px; color: {t.ink}; }}
            QPushButton#chip:hover {{ background: {t.hover}; }}
            QPushButton#chip:checked {{ background: {accent.name()}; color: {t.on_accent};
                border: 1px solid {accent.name()}; }}
        """)
        for page in self._pages.values():
            if hasattr(page, "retheme"):
                page.retheme()
        self.update()

    def _geometry_for(self, anchor: QRect, attached: str) -> QRect:
        screen = QGuiApplication.screenAt(anchor.center()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        full = screen.geometry()
        w = min(PANEL_W, area.width() - 32) + 2 * SHADOW
        if attached == "island":
            h = PANEL_H + SHADOW
            x = anchor.center().x() - w // 2
            return QRect(max(area.left(), min(x, area.right() - w + 1)), full.top(), w, h)
        h = PANEL_H + 2 * SHADOW
        gap = 10
        if anchor.left() - area.left() >= w + gap or anchor.left() - area.left() > area.right() - anchor.right():
            x = anchor.left() - gap - w + SHADOW  # to the left of the orb
        else:
            x = anchor.right() + gap - SHADOW
        y = anchor.bottom() - h + SHADOW
        return QRect(max(area.left(), min(x, area.right() - w + 1)), max(area.top(), min(y, area.bottom() - h + 1)), w, h)

    def _card_rect(self) -> QRectF:
        r = QRectF(self.rect())
        if self.attached == "island":
            return r.adjusted(SHADOW, 0, -SHADOW, -SHADOW)
        return r.adjusted(SHADOW, SHADOW, -SHADOW, -SHADOW)

    def _shape(self) -> QPainterPath:
        card, radius = self._grown_rect()
        path = QPainterPath()
        if self.attached != "island":
            path.addRoundedRect(card, radius, radius)
            return path
        f = FLARE * min(1.0, max(0.0, self._morph)) + 8 * (1 - min(1.0, max(0.0, self._morph)))
        r = min(radius, (card.height() - f) / 2, card.width() / 2)
        x0, x1, top, bottom = card.left() + f, card.right() - f, card.top(), card.bottom()
        path.moveTo(x0 - f, top)
        path.quadTo(x0, top, x0, top + f)
        path.lineTo(x0, bottom - r)
        path.arcTo(QRectF(x0, bottom - 2 * r, 2 * r, 2 * r), 180, 90)
        path.lineTo(x1 - r, bottom)
        path.arcTo(QRectF(x1 - 2 * r, bottom - 2 * r, 2 * r, 2 * r), 270, 90)
        path.lineTo(x1, top + f)
        path.quadTo(x1, top, x1 + f, top)
        path.closeSubpath()
        return path

    def _grown_rect(self) -> tuple[QRectF, float]:
        """The panel's outline part way between the timer it grows from (morph 0) and fully open (1).

        The spring overshoots a little past 1, so the panel swells a touch before settling.
        """
        full = self._card_rect()
        start = QRectF(self.mapFromGlobal(self.anchor.topLeft()), self.anchor.size()) if self.anchor.isValid() \
            else QRectF(full.center().x() - 100, full.top(), 200, 38)
        if self.attached == "island":
            start.moveTop(full.top())  # the pill hangs from the top edge, like the panel
        m = max(0.0, min(1.06, self._morph))

        def mix(a: float, b: float) -> float:
            return a + (b - a) * m

        rect = QRectF(QPointF(mix(start.left(), full.left()), mix(start.top(), full.top())),
                      QPointF(mix(start.right(), full.right()), mix(start.bottom(), full.bottom())))
        start_radius = min(start.width(), start.height()) / 2
        return rect, mix(start_radius, RADIUS)

    def _set_morph(self, value) -> None:
        self._morph = float(value)
        self.update()

    def _freeze(self) -> None:
        """While it grows or shrinks, the cards are a picture drawn inside the outline (never past it)."""
        self.root.activate()
        self._still = self.content.grab()
        self.content.hide()

    def _thaw(self) -> None:
        self._still = None
        self.content.show()

    def _grown(self) -> None:
        if self._closing:
            self._closing = False
            self.hide()
            self._morph = 1.0
            self._thaw()
            self.closed.emit()
        else:
            self._thaw()  # open: the real cards again

    def paintEvent(self, _event) -> None:
        t = self.ctrl.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        shape = self._shape()
        lift = max(0.0, min(1.0, self._morph))
        for i in range(SHADOW, 0, -2):
            p.setPen(QPen(t.shadow(int(t.shadow_alpha * 0.16 * lift * (1 - i / SHADOW) ** 2)), i))
            p.setBrush(Qt.NoBrush)
            p.drawPath(shape.translated(0, 4 * lift))
        p.setPen(QPen(t.border, 1))
        p.setBrush(QColor(t.surface))
        p.drawPath(shape)
        still = getattr(self, "_still", None)
        if still is not None:
            # the cards fade in once there's room for them, clipped to the outline as it grows
            p.setClipPath(shape)
            p.setOpacity(max(0.0, min(1.0, (self._morph - 0.45) / 0.45)))
            p.drawPixmap(self.content.pos(), still)
        p.end()

    def _apply_margins(self) -> None:
        card = self._card_rect()
        inset = FLARE if self.attached == "island" else 0
        self.root.setContentsMargins(int(card.left()) + inset + 16, int(card.top()) + 8,
                                     self.width() - int(card.right()) + inset + 16, self.height() - int(card.bottom()) + 14)

    def _fit_tabs(self) -> None:
        """Show as many tabs as fit; the rest go under More."""
        font = QFont(FONT_FAMILY)
        font.setPixelSize(13)  # the clock's size in the stylesheet (the label may not be styled yet)
        clock = QFontMetricsF(font).horizontalAdvance(CLOCK_SAMPLE)
        room = self._card_rect().width() - 2 * FLARE - 32 - max(self.clock.sizeHint().width(), clock) - 10 \
            - self.actions.sizeHint().width() - 30
        fit = max(3, int(room // (TAB_W + 2)))
        hide = len(self.tabs) > fit
        shown = set(range(fit - 1 if hide else len(self.tabs)))
        current = next((i for i, t in enumerate(self.tabs) if t.spec.key == self._current), None)
        if current is not None and current not in shown and shown:
            shown.discard(max(shown))  # the open tab always shows: it takes the last place before More
            shown.add(current)
        for i, tab in enumerate(self.tabs):
            tab.setVisible(i in shown)
        self.more.setVisible(hide)
        self._overflow = [t.spec for i, t in enumerate(self.tabs) if i not in shown]

    def _more_menu(self) -> None:
        menu = QMenu(self)
        menu.setWindowFlags(menu.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        menu.setAttribute(Qt.WA_TranslucentBackground)
        for spec in getattr(self, "_overflow", []):
            act = menu.addAction(spec.label)
            act.setCheckable(True)
            act.setChecked(spec.key == self._current)
            act.triggered.connect(lambda _c=False, k=spec.key: self.show_page(k))
        menu.exec(self.more.mapToGlobal(QPoint(0, self.more.height())))
        menu.deleteLater()

    # ------------------------------------------------------------- pages
    def show_page(self, key: str) -> None:
        spec = next((p for p in PAGES if p.key == key), None) or (PAGES[0] if PAGES else None)
        if spec is None:
            return
        page = self._pages.get(spec.key)
        if page is None:
            page = spec.make(self.ctrl)
            self._pages[spec.key] = page
            self.stack.addWidget(page)
        self._current = spec.key
        self.stack.setCurrentWidget(page)
        if self.isVisible() or self.width() > 100:
            self._fit_tabs()  # a tab picked from More moves into the row
        for tab in self.tabs:
            tab.setChecked(tab.spec.key == spec.key)
        self.more.setChecked(False)
        if getattr(self.ctrl.settings, "panel_tab", None) != spec.key and spec.key != "gaming":
            self.ctrl.settings.panel_tab = spec.key
            self.ctrl.save_settings()
        if hasattr(page, "refresh"):
            page.refresh()

    def page(self, key: str):
        return self._pages.get(key)

    # ------------------------------------------------------------- opening and closing
    def open_at(self, anchor: QRect, attached: str, tab: str | None = None) -> None:
        self.attached, self.anchor = attached, QRect(anchor)
        self.setGeometry(self._geometry_for(anchor, attached))
        self._apply_margins()
        self._tick()  # the clock's text first: the tabs get the room that's left
        self._fit_tabs()
        self._outside_since = None
        self.show_page(tab or getattr(self.ctrl.settings, "panel_tab", "") or "home")
        self._tick()
        self.ultra_btn.lit = bool(self.ctrl.settings.ultra_on)
        self.ultra_btn.update()
        if not self.isVisible() or self._closing:
            # grow out of the timer: a quick spring that swells a touch past full size, then settles
            self._closing = False
            self._grow.stop()
            start = self._morph if self.isVisible() else 0.0
            self._set_morph(start)
            self._grow.setStartValue(start)
            self._grow.setEndValue(1.0)
            self._grow.setDuration(GROW_MS)
            curve = QEasingCurve(QEasingCurve.OutBack)
            curve.setOvershoot(1.05)
            self._grow.setEasingCurve(curve)
            if self._still is None:
                self._freeze()
            self.show()
            self._grow.start()
        self.raise_()
        self._poll.start()
        self._second.start()

    def close_panel(self, animate: bool = True) -> None:
        """Shrink back into the timer (or vanish at once, for a window opening where the panel was)."""
        if not self.isVisible():
            return
        self._poll.stop()
        self._second.stop()
        self._grow.stop()
        if not animate:
            self._closing = False
            self.hide()
            self._set_morph(1.0)
            self._thaw()
            self.closed.emit()
            return
        if self._closing:
            return
        self._closing = True
        if self._still is None:
            self._freeze()
        self._grow.setStartValue(self._morph)
        self._grow.setEndValue(0.0)
        self._grow.setDuration(SHRINK_MS)
        self._grow.setEasingCurve(QEasingCurve(QEasingCurve.InCubic))
        self._grow.start()

    def toggle_pin(self) -> None:
        self.pinned = not self.pinned
        self.pin_btn.glyph = "pin_on" if self.pinned else "pin"
        self.pin_btn.lit = self.pinned
        self.pin_btn.setToolTip("Let the panel close again" if self.pinned else "Keep the panel open")
        self.pin_btn.update()

    def _typing(self) -> bool:
        focus = QApplication.focusWidget()
        return (self.isActiveWindow() and focus is not None and self.isAncestorOf(focus)
                and isinstance(focus, QLineEdit | QPlainTextEdit | QTextEdit))

    # ------------------------------------------------------------- files dragged in
    def dragEnterEvent(self, event) -> None:
        if event.mimeData() is not None and event.mimeData().hasUrls():
            if self._current not in ("shelf", "launcher") and any(p.key == "shelf" for p in PAGES):
                self.show_page("shelf")  # dragging files in: they're going on the Shelf
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        shelf = self._pages.get("shelf")
        if shelf is not None and hasattr(shelf, "dropEvent"):
            shelf.dropEvent(event)  # dropped on the panel but not on the Shelf's own card
        else:
            event.ignore()

    def _check_pointer(self) -> None:
        pos = QCursor.pos()
        if self.geometry().contains(pos) or self.anchor.contains(pos):
            self._outside_since = None
            return
        if QApplication.mouseButtons() != Qt.NoButton:  # mid-drag (a file out of the Shelf, say): stay
            self._outside_since = None
            return
        if self.pinned or self._typing() or QApplication.activePopupWidget() or QApplication.activeModalWidget():
            self._outside_since = None
            return
        now = time.monotonic()
        if self._outside_since is None:
            self._outside_since = now
        elif now - self._outside_since >= CLOSE_AFTER_S:
            self.close_panel()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == event.Type.ActivationChange and not self.isActiveWindow() and not self.pinned:
            if not self.geometry().contains(QCursor.pos()) and not QApplication.activePopupWidget() \
                    and not QApplication.activeModalWidget():
                self.close_panel()  # clicked somewhere else

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.close_panel()
        else:
            super().keyPressEvent(event)

    def _tick(self) -> None:
        now = datetime.now()
        self.clock.setText(f"{now:%a}, {now.day} {now:%b}   {now.strftime('%I:%M:%S %p').lstrip('0')}")
        page = self.stack.currentWidget()
        if page is not None and hasattr(page, "tick"):
            page.tick()
