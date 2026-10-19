"""The "Ask Pomo" chat window."""
from __future__ import annotations

import time

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QTextDocument
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .ai import QUICK_ACTIONS, ChatWorker, build_context, quick_prompt
from .config import AI_MODELS
from .pomo import BODY_RY, Pomo
from .winutil import set_dark_title_bar

GREETING = ("Hi, I'm Pomo! Ask me to plan your work into pomodoros, look at how your focus is going, "
            "or catch you up on what your coding agents did today.")
NO_KEY = "To chat, add your Anthropic API key in **Settings > AI**."


class PomoAvatar(QWidget):
    def __init__(self, size: int = 64, parent: QWidget | None = None):
        super().__init__(parent)
        self.pomo = Pomo()
        self.setFixedSize(size + 16, size + 10)
        self._last = time.monotonic()
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._last = time.monotonic()
        self._timer.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._timer.stop()

    def _tick(self) -> None:
        now = time.monotonic()
        dt, self._last = now - self._last, now
        anchor, size = self._anchor()
        cursor = QPointF(self.mapFromGlobal(self.cursor().pos())) - anchor
        self.pomo.update(dt, (cursor.x() / size, cursor.y() / size))
        self.update()

    def _anchor(self) -> tuple[QPointF, float]:
        size = self.width() - 16
        return QPointF(self.width() / 2, self.height() - size * BODY_RY - 6), float(size)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        anchor, size = self._anchor()
        self.pomo.paint(p, anchor, size)
        p.end()


class Bubble(QFrame):
    PAD_X, PAD_Y = 12, 9

    def __init__(self, text: str, mine: bool, theme, max_width: int = 340):
        super().__init__()
        self.mine = mine
        self.max_width = max_width
        self.text = ""
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel()
        self.label.setTextFormat(Qt.MarkdownText)
        self.label.setWordWrap(True)
        self.label.setOpenExternalLinks(True)
        self.label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        tint = QColor(theme.short if mine else theme.focus)
        tint.setAlpha(60 if mine else 40)
        rgba = f"rgba({tint.red()},{tint.green()},{tint.blue()},{tint.alpha()})"
        self.label.setStyleSheet(
            f"QLabel {{ background: {rgba}; color: {theme.ink}; border: 1px solid {theme.border_qss};"
            f" border-radius: 14px; padding: {self.PAD_Y}px {self.PAD_X}px; }}"
        )
        if mine:
            lay.addStretch(1)
            lay.addWidget(self.label)
        else:
            lay.addWidget(self.label)
            lay.addStretch(1)
        self.set_text(text)

    def set_text(self, text: str) -> None:
        self.text = text or "…"
        self.label.setText(self.text)
        self._fit()

    def set_max_width(self, width: int) -> None:
        self.max_width = max(160, width)
        self._fit()

    def _fit(self) -> None:
        # Word-wrapped labels in a scroll area get their height wrong, so measure the text ourselves.
        doc = QTextDocument()
        doc.setDefaultFont(self.label.font())
        doc.setDocumentMargin(0)
        doc.setMarkdown(self.text)
        chrome_w, chrome_h = 2 * self.PAD_X + 2, 2 * self.PAD_Y + 2
        slack = 10  # the label wraps a hair differently from QTextDocument; extra width keeps the line count
        inner = min(self.max_width - chrome_w - slack, doc.idealWidth() + 2)
        doc.setTextWidth(inner)
        self.label.setFixedSize(int(inner + chrome_w + slack), int(doc.size().height() + chrome_h + 4))


class ChatWindow(QWidget):
    def __init__(self, ctrl):
        super().__init__(None, Qt.Window)
        self.ctrl = ctrl
        self.theme = ctrl.theme
        self.history: list = []  # messages sent to Claude, append-only
        self.worker: ChatWorker | None = None
        self._reply: Bubble | None = None
        self._reply_text = ""
        self._pending: list = []
        self.setWindowTitle("Ask Pomo")
        self.setWindowIcon(ctrl.icon)
        self.resize(460, 640)
        self.setMinimumSize(380, 480)

        page = QWidget()
        page.setObjectName("page")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(page)
        root = QVBoxLayout(page)
        root.setContentsMargins(18, 14, 18, 16)
        root.setSpacing(10)

        head = QHBoxLayout()
        self.avatar = PomoAvatar(58)
        head.addWidget(self.avatar)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        title = QLabel("Ask Pomo")
        title.setProperty("role", "h1")
        self.subtitle = QLabel()
        self.subtitle.setProperty("role", "muted")
        titles.addStretch(1)
        titles.addWidget(title)
        titles.addWidget(self.subtitle)
        titles.addStretch(1)
        head.addLayout(titles, 1)
        clear = QPushButton("New chat")
        clear.setObjectName("ghost")
        clear.clicked.connect(self.new_chat)
        head.addWidget(clear, 0, Qt.AlignTop)
        root.addLayout(head)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        holder = QWidget()
        holder.setObjectName("page")
        self.thread = QVBoxLayout(holder)
        self.thread.setContentsMargins(2, 4, 8, 4)
        self.thread.setSpacing(10)
        self.thread.addStretch(1)
        self.scroll.setWidget(holder)
        root.addWidget(self.scroll, 1)

        chips = QHBoxLayout()
        chips.setSpacing(6)
        self.chip_buttons = []
        for key, label in QUICK_ACTIONS:
            chip = QPushButton(label)
            chip.setCursor(Qt.PointingHandCursor)
            chip.setStyleSheet("QPushButton { padding: 4px 10px; border-radius: 12px; font-size: 12px; }")
            chip.clicked.connect(lambda _=False, k=key: self.send(quick_prompt(k, self.ctrl.engine.task)))
            chips.addWidget(chip)
            self.chip_buttons.append(chip)
        chips.addStretch(1)
        root.addLayout(chips)

        row = QHBoxLayout()
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText("Ask Pomo anything about your focus…")
        self.input.setFixedHeight(64)
        self.input.installEventFilter(self)
        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("primary")
        self.send_button.setCursor(Qt.PointingHandCursor)
        self.send_button.clicked.connect(self._send_clicked)
        row.addWidget(self.input, 1)
        row.addWidget(self.send_button, 0, Qt.AlignBottom)
        root.addLayout(row)
        self.privacy = QLabel("Your question and a short summary of your focus stats, tasks and agent activity "
                              "are sent to Anthropic.")
        self.privacy.setProperty("role", "muted")
        self.privacy.setWordWrap(True)
        root.addWidget(self.privacy)

        self.new_chat()

    # ----------------------------------------------------------------- state
    def showEvent(self, event) -> None:
        super().showEvent(event)
        set_dark_title_bar(self, self.theme.dark)
        self.subtitle.setText(AI_MODELS.get(self.ctrl.settings.ai_model, self.ctrl.settings.ai_model).replace(" (default)", ""))
        self.input.setFocus()

    def new_chat(self) -> None:
        if self.worker is not None:
            self.worker.cancel()  # its late signals find no reply bubble and no pending turn
        self._reply = None
        self._pending = []
        self.history.clear()
        while self.thread.count() > 1:
            item = self.thread.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._add_bubble(GREETING, mine=False)
        if not self.ctrl.has_ai_key():
            self._add_bubble(NO_KEY, mine=False)

    def _bubble_width(self) -> int:
        return min(380, int(self.scroll.viewport().width() * 0.82))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        width = self._bubble_width()
        for i in range(self.thread.count()):
            widget = self.thread.itemAt(i).widget()
            if isinstance(widget, Bubble):
                widget.set_max_width(width)

    def _add_bubble(self, text: str, mine: bool) -> Bubble:
        bubble = Bubble(text, mine, self.theme, self._bubble_width())
        self.thread.insertWidget(self.thread.count() - 1, bubble)
        QTimer.singleShot(0, self._scroll_to_end)
        return bubble

    def _scroll_to_end(self) -> None:
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def eventFilter(self, obj, event) -> bool:
        if obj is self.input and event.type() == event.Type.KeyPress:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not event.modifiers() & Qt.ShiftModifier:
                self._send_clicked()
                return True
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------ chat
    def _send_clicked(self) -> None:
        if self.worker is not None:
            self._stop()
            return
        text = self.input.toPlainText().strip()
        if text:
            self.input.clear()
            self.send(text)

    def send(self, text: str) -> None:
        if self.worker is not None:
            return
        self._add_bubble(text, mine=True)
        user = {"role": "user", "content": text}
        context = {"role": "system", "content": build_context(self.ctrl)}
        self._pending = [user, context]
        self._reply = self._add_bubble("", mine=False)
        self._reply_text = ""
        self.avatar.pomo.signal = "busy"
        self.worker = ChatWorker(self.ctrl.ai_key(), self.ctrl.settings.ai_model, self.history + self._pending, self)
        self.worker.text.connect(self._on_text)
        self.worker.completed.connect(self._on_completed)
        self.worker.refused.connect(lambda: self._end("Sorry, I can't help with that one. Try asking another way?"))
        self.worker.failed.connect(lambda msg: self._end(msg))
        self.worker.finished.connect(self._on_thread_done)
        self._set_busy(True)
        self.worker.start()

    def _on_text(self, chunk: str) -> None:
        self._reply_text += chunk
        if self._reply is not None:
            self._reply.set_text(self._reply_text)
            self._scroll_to_end()

    def _on_completed(self, final) -> None:
        if not self._pending:  # the chat was reset while this reply streamed
            return
        # append-only history: the user turn, its context, and the reply exactly as returned
        self.history += self._pending + [{"role": "assistant", "content": final.content}]
        self._pending = []
        self.avatar.pomo.hello()

    def _end(self, message: str) -> None:
        if self._reply is not None:
            self._reply.set_text(message)
        self._pending = []

    def _stop(self) -> None:
        if self.worker is not None:
            self.worker.cancel()
            if self._reply is not None:
                self._reply.set_text((self._reply_text or "") + "\n\n*(stopped)*")
            self._pending = []

    def _on_thread_done(self) -> None:
        self.worker = None
        self.avatar.pomo.signal = None
        self._set_busy(False)

    def _set_busy(self, busy: bool) -> None:
        self.send_button.setText("Stop" if busy else "Send")
        for chip in self.chip_buttons:
            chip.setEnabled(not busy)

    def shutdown(self) -> None:
        if self.worker is not None:
            self.worker.cancel()
            self.worker.wait(2000)
