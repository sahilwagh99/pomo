"""Pop-up card shown beside the orb when a session ends, with quick actions."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QPropertyAnimation, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QToolButton, QVBoxLayout, QWidget

SHADOW = 14
WIDTH = 300
GAP = 10


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
        self._buttons = QHBoxLayout()
        self._buttons.setSpacing(8)
        outer.addLayout(head)
        outer.addWidget(self._body)
        outer.addSpacing(8)
        outer.addLayout(self._buttons)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(180)
        self._fade.finished.connect(self._after_fade)

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
    ) -> None:
        """tag identifies what is shown (e.g. an agent approval); on_dismiss runs if the card
        closes without one of its buttons being clicked (timeout, close button, replaced)."""
        self._fire_dismiss()
        self.tag = tag
        self._on_dismiss = on_dismiss
        self._accent = QColor(accent)
        self._title.setText(title)
        self._body.setText(body)
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
        self.adjustSize()
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
