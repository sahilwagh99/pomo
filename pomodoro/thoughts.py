"""The thought parking lot: jot down what pops into your head during focus, deal with it on your break."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .stats import plural
from .winutil import bring_to_front, set_dark_title_bar

SHADOW = 14
WIDTH = 340
GAP = 10


class ParkCard(QWidget):
    """A tiny input that pops up over whatever you're doing (from the hotkey): type, Enter, back to work."""

    parked = Signal(object)  # Thought

    def __init__(self, ctrl):
        super().__init__(None)
        self.ctrl = ctrl
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("Park a thought")
        self.setFixedWidth(WIDTH + 2 * SHADOW)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SHADOW + 16, SHADOW + 12, SHADOW + 16, SHADOW + 12)
        lay.setSpacing(6)
        head = QHBoxLayout()
        title = QLabel("Park a thought")
        title.setProperty("role", "title")
        self.count = QLabel()
        self.count.setProperty("role", "muted")
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.count)
        self.input = QLineEdit()
        self.input.setPlaceholderText("What popped into your head?")
        self.input.setMaxLength(300)
        self.input.returnPressed.connect(self._park)
        self.hint = QLabel("Enter parks it for your break  ·  Esc closes")
        self.hint.setProperty("role", "muted")
        lay.addLayout(head)
        lay.addWidget(self.input)
        lay.addWidget(self.hint)
        self._close_timer = QTimer(self)
        self._close_timer.setSingleShot(True)
        self._close_timer.timeout.connect(self.hide)

    def popup(self, anchor: QRect) -> None:
        self._close_timer.stop()
        waiting = len(self.ctrl.storage.thoughts())
        self.count.setText(f"{waiting} waiting" if waiting else "")
        self.hint.setText("Enter parks it for your break  ·  Esc closes")
        self.input.clear()
        self.input.setEnabled(True)
        self.adjustSize()
        screen = QGuiApplication.screenAt(anchor.center()) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        if anchor.top() - geo.top() < 60:  # under the island
            x, y = anchor.center().x() - self.width() // 2, anchor.bottom() + GAP - SHADOW
        else:  # above the orb
            x, y = anchor.center().x() - self.width() // 2, anchor.top() - GAP - self.height() + SHADOW
        self.move(max(geo.left() - SHADOW, min(x, geo.right() + 1 - self.width() + SHADOW)),
                  max(geo.top() - SHADOW, min(y, geo.bottom() + 1 - self.height() + SHADOW)))
        self.show()
        bring_to_front(self)
        self.input.setFocus()

    def _park(self) -> None:
        text = self.input.text().strip()
        if not text:
            self.hide()
            return
        thought = self.ctrl.storage.add_thought(text)
        self.input.clear()
        self.input.setEnabled(False)
        self.hint.setText("Parked. It'll be waiting on your break.")
        self.parked.emit(thought)
        self._close_timer.start(900)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(event)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.ActivationChange and not self.isActiveWindow() and self.isVisible():
            QTimer.singleShot(150, lambda: None if self.isActiveWindow() else self.hide())

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
        p.end()


class ThoughtsWindow(QWidget):
    """Everything you parked: tick off what's handled, or let Ask Pomo turn it into a plan."""

    def __init__(self, ctrl):
        super().__init__(None, Qt.Window)
        self.ctrl = ctrl
        self.setWindowTitle("Parked thoughts")
        self.setWindowIcon(ctrl.icon)
        self.resize(400, 460)
        page = QWidget()
        page.setObjectName("page")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(page)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(10)
        title = QLabel("Parked thoughts")
        title.setProperty("role", "h1")
        sub = QLabel("What popped up while you focused. Tick things off as you handle them.")
        sub.setProperty("role", "muted")
        sub.setWordWrap(True)
        self.list = QListWidget()
        self.list.itemChanged.connect(self._toggled)
        add_row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Add a thought")
        self.input.returnPressed.connect(self._add)
        add_row.addWidget(self.input, 1)
        buttons = QHBoxLayout()
        plan = QPushButton("Plan these with Ask Pomo")
        plan.setObjectName("primary")
        plan.clicked.connect(self._plan)
        clear = QPushButton("Clear done")
        clear.setObjectName("ghost")
        clear.clicked.connect(self._clear_done)
        buttons.addWidget(plan)
        buttons.addStretch(1)
        buttons.addWidget(clear)
        lay.addWidget(title)
        lay.addWidget(sub)
        lay.addWidget(self.list, 1)
        lay.addLayout(add_row)
        lay.addLayout(buttons)
        self.refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        set_dark_title_bar(self, self.ctrl.theme.dark)
        self.refresh()

    def refresh(self) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for thought in self.ctrl.storage.thoughts():
            item = QListWidgetItem(thought.text)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            item.setData(Qt.UserRole, thought.id)
            item.setToolTip(f"Parked {thought.created:%a %H:%M}")
            self.list.addItem(item)
        if self.list.count() == 0:
            empty = QListWidgetItem("Nothing parked. During focus, press the park hotkey (or use the menu) "
                                    "to jot down a stray thought without leaving your work.")
            empty.setFlags(Qt.NoItemFlags)
            self.list.addItem(empty)
        self.list.blockSignals(False)

    def _toggled(self, item: QListWidgetItem) -> None:
        thought_id = item.data(Qt.UserRole)
        if thought_id is None:
            return
        done = item.checkState() == Qt.Checked
        self.ctrl.storage.set_thought_done(thought_id, done)
        font = item.font()
        font.setStrikeOut(done)
        item.setFont(font)
        self.ctrl.thoughts_changed()

    def _add(self) -> None:
        text = self.input.text().strip()
        if text:
            self.ctrl.storage.add_thought(text)
            self.input.clear()
            self.refresh()
            self.ctrl.thoughts_changed()

    def _clear_done(self) -> None:
        self.ctrl.storage.clear_done_thoughts()
        self.refresh()

    def _plan(self) -> None:
        if not self.ctrl.storage.thoughts():
            return
        self.ctrl.ask_pomo("Turn my parked thoughts into a short plan: which to do on this break, "
                           "which deserve a focus session, and which can wait.")


def thoughts_line(count: int) -> str:
    return f"You parked {plural(count, 'thought')} while you focused." if count else ""
