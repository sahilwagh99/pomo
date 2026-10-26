"""Pop-up card shown beside the orb when a session ends, with quick actions."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QPointF, QPropertyAnimation, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFontMetrics, QGuiApplication, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QToolButton, QVBoxLayout, QWidget

SHADOW = 14
WIDTH = 300
GAP = 10
BODY_WIDTH = WIDTH - 28  # inside the card's side margins
RATINGS = ((1, "ouch", "Tough"), (2, "idle", "Okay"), (3, "happy", "Great"))  # rating, Pomo's face, label
_face_icons: dict[str, QIcon] = {}


def face_icon(face: str, size: int = 30) -> QIcon:
    """Pomo making a face, as a small button icon."""
    if face not in _face_icons:
        import random

        from .pomo import BODY_RY, Pomo

        pm = QPixmap(size * 2, size * 2)
        pm.setDevicePixelRatio(2.0)
        pm.fill(Qt.transparent)
        pomo = Pomo(random.Random(0))
        pomo.outfit = "none"
        pomo._next_blink = 99
        if face != "idle":
            pomo._react(face, 99)
        painter = QPainter(pm)
        body = size * 0.9
        pomo.paint(painter, QPointF(size / 2, size - body * BODY_RY - 2), body, ground_shadow=False)
        painter.end()
        _face_icons[face] = QIcon(pm)
    return _face_icons[face]


class ToastCard(QWidget):
    def __init__(self, ctrl):
        super().__init__(None)
        self.ctrl = ctrl
        self._accent = QColor("#95c283")
        self._closing = False
        self.tag: object = None
        self._on_dismiss: Callable[[], None] | None = None
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedWidth(WIDTH + 2 * SHADOW)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SHADOW + 16, SHADOW + 12, SHADOW + 12, SHADOW + 14)
        outer.setSpacing(4)
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addSpacing(14)  # room for the accent dot painted beside the title
        self._title = QLabel()
        self._title.setProperty("role", "title")
        close = QToolButton()
        close.setObjectName("close")
        close.setText("✕")
        close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(self.dismiss)
        head.addWidget(self._title, 1)
        head.addWidget(close, 0, Qt.AlignTop)
        self._body = QLabel()
        self._body.setWordWrap(True)
        self._body.setProperty("role", "secondary")
        self._body.setFixedWidth(BODY_WIDTH)  # a known width, so its height can be measured (see _fit_body)
        self._buttons = QHBoxLayout()
        self._buttons.setSpacing(8)
        outer.addLayout(head)
        outer.addWidget(self._body)
        outer.addSpacing(8)
        outer.addWidget(self._build_rate_row())
        self._chips_box = QWidget()
        self._chips = QHBoxLayout(self._chips_box)
        self._chips.setContentsMargins(0, 0, 0, 4)
        self._chips.setSpacing(6)
        self._chips_box.hide()
        outer.addWidget(self._chips_box)
        outer.addLayout(self._buttons)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(180)
        self._fade.finished.connect(self._after_fade)

    @property
    def showing(self) -> bool:
        """On screen and not on its way out."""
        return self.isVisible() and not self._closing

    def _build_rate_row(self) -> QWidget:
        """'How was that?' with three Pomo faces: tough, okay, great."""
        self._rate_box = box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 6)
        lay.setSpacing(4)
        self._rate_label = QLabel("How was that?")
        self._rate_label.setProperty("role", "muted")
        lay.addWidget(self._rate_label)
        lay.addSpacing(4)
        self._rate_buttons = []
        for rating, face, label in RATINGS:
            button = QToolButton()
            button.setIcon(face_icon(face))
            button.setIconSize(QSize(30, 30))
            button.setToolTip(label)
            button.setAutoRaise(True)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _=False, r=rating: self._rated(r))
            lay.addWidget(button)
            self._rate_buttons.append(button)
        lay.addStretch(1)
        box.hide()
        self._on_rate: Callable[[int], None] | None = None
        return box

    def _fit_body(self) -> None:
        # word-wrapped labels misreport their height (too little, or the last text's), so measure the text
        self._body.ensurePolished()
        bounds = QFontMetrics(self._body.font()).boundingRect(QRect(0, 0, BODY_WIDTH, 100_000), Qt.TextWordWrap,
                                                              self._body.text())
        self._body.setFixedHeight(max(1, bounds.height() + 2))

    def _rated(self, rating: int) -> None:
        callback, self._on_rate = self._on_rate, None
        for button, (value, _face, _label) in zip(self._rate_buttons, RATINGS):
            button.setVisible(value == rating)
            button.setEnabled(False)
        self._rate_label.setText("Thanks! Pomo is learning.")
        if callback is not None:
            callback(rating)

    def show_message(
        self,
        title: str,
        body: str,
        accent: QColor,
        actions: list[tuple[str, Callable[[], None], bool]],
        anchor: QRect,
        timeout_ms: int = 20000,
        tag: object = None,
        on_dismiss: Callable[[], None] | None = None,
        rate: Callable[[int], None] | None = None,
        extras: list[tuple[str, Callable[[], None]]] | None = None,
    ) -> None:
        """tag identifies what is shown (e.g. an agent approval); on_dismiss runs if the card
        closes without one of its buttons being clicked (timeout, close button, replaced).
        rate adds "How was that?" with three faces; extras adds a row of small chips (Breathe, Play...)."""
        self._fire_dismiss()
        self._on_rate = rate
        self._rate_box.setVisible(rate is not None)
        self._rate_label.setText("How was that?")
        for button in self._rate_buttons:
            button.setVisible(True)
            button.setEnabled(True)
        while self._chips.count():
            item = self._chips.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        for text, fn in extras or []:
            chip = QPushButton(text)
            chip.setCursor(Qt.PointingHandCursor)
            chip.setStyleSheet("QPushButton { padding: 3px 10px; border-radius: 11px; font-size: 12px; }")
            chip.clicked.connect(lambda _=False, f=fn: self._act(f))
            self._chips.addWidget(chip)
        self._chips.addStretch(1)
        self._chips_box.setVisible(bool(extras))
        self.tag = tag
        self._on_dismiss = on_dismiss
        self._accent = QColor(accent)
        self._title.setText(title)
        self._body.setText(body)
        self._fit_body()
        while self._buttons.count():
            item = self._buttons.takeAt(0)
            if item.widget():
                item.widget().hide()  # gone now, not when deleteLater gets to it
                item.widget().deleteLater()
        for text, fn, primary in actions:
            button = QPushButton(text)
            button.setCursor(Qt.PointingHandCursor)
            if primary:
                button.setObjectName("primary")
                button.setStyleSheet(
                    f"QPushButton#primary {{ background: {accent.name()}; }}"
                    f" QPushButton#primary:hover {{ background: {accent.lighter(112).name()}; }}"
                )
            else:
                button.setObjectName("ghost")
            button.clicked.connect(lambda _=False, f=fn: self._act(f))
            self._buttons.addWidget(button)
        self._buttons.addStretch(1)
        self.layout().invalidate()  # drop the last card's size: rows may have been hidden since
        self.layout().activate()
        self.resize(self.width(), self.layout().sizeHint().height())
        self._position(anchor)
        self._closing = False
        if not self.isVisible():
            self.setWindowOpacity(0.0)
            self.show()
        self._fade.stop()
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(1.0)
        self._fade.start()
        if tag is None:
            self._timer.start(timeout_ms)
        else:
            self._timer.stop()  # tagged cards (approvals) are closed by whoever owns them
        self.update()

    def _act(self, fn: Callable[[], None]) -> None:
        self._on_dismiss = None  # answered with a button
        self.dismiss()
        fn()

    def _fire_dismiss(self) -> None:
        callback, self._on_dismiss = self._on_dismiss, None
        if callback is not None:
            callback()

    def dismiss_silently(self) -> None:
        """Close without running on_dismiss (the thing it asked about was settled elsewhere)."""
        self._on_dismiss = None
        self.dismiss()

    def dismiss(self) -> None:
        if not self.isVisible() or self._closing:
            return
        self._fire_dismiss()
        self.tag = None
        self._closing = True
        self._timer.stop()
        self._fade.stop()
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _after_fade(self) -> None:
        if self._closing:
            self._closing = False
            self.hide()

    def _position(self, anchor: QRect) -> None:
        screen = QGuiApplication.screenAt(anchor.center()) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        w, h = self.width(), self.height()
        if anchor.top() - geo.top() < 60:
            # the island hangs from the top edge: drop the card below it, centered
            x = anchor.center().x() - w // 2
            y = anchor.bottom() + GAP - SHADOW
        else:
            # beside the orb: prefer its left, bottom edges aligned; flip right if there is no room
            x = anchor.left() - GAP - (w - SHADOW)
            if x + SHADOW < geo.left():
                x = anchor.right() + GAP - SHADOW
            y = anchor.bottom() - (h - SHADOW)
        x = max(geo.left() - SHADOW, min(x, geo.right() + 1 - w + SHADOW))
        y = max(geo.top() - SHADOW, min(y, geo.bottom() + 1 - h + SHADOW))
        self.move(x, y)

    def enterEvent(self, event) -> None:
        self._timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if self.isVisible() and not self._closing:
            if self.tag is None:
                self._timer.start(6000)
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        t = self.ctrl.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        card = QRectF(self.rect()).adjusted(SHADOW, SHADOW, -SHADOW, -SHADOW)
        p.setPen(Qt.NoPen)
        for i in range(SHADOW, 0, -1):
            alpha = int(t.shadow_alpha * 0.22 * (1 - i / SHADOW) ** 2)
            p.setBrush(t.shadow(alpha))
            p.drawRoundedRect(card.adjusted(-i, -i + 4, i, i + 4), 14 + i, 14 + i)
        p.setBrush(QColor(t.surface))
        p.setPen(QPen(t.border, 1))
        p.drawRoundedRect(card.adjusted(0.5, 0.5, -0.5, -0.5), 14, 14)
        title = self._title.geometry()
        p.setPen(Qt.NoPen)
        p.setBrush(self._accent)
        p.drawEllipse(QRectF(SHADOW + 16, title.center().y() - 4.5, 9, 9))
        p.end()
