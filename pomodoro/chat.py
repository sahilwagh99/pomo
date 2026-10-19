"""The "Ask Pomo" chat window."""
from __future__ import annotations

import time

from PySide6.QtCore import QPointF, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QTextDocument
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import keystore
from .ai import KEYS_URL, QUICK_ACTIONS, ChatWorker, KeyCheckWorker, build_context, quick_prompt
from .config import AI_MODELS
from .pomo import BODY_RY, Pomo
from .winutil import set_dark_title_bar

GREETING = ("Hi, I'm Pomo! Ask me to plan your work into pomodoros, look at how your focus is going, "
            "or catch you up on what your coding agents did today.")
KEY_TITLE = "Bring your own key"
KEY_TEXT = ("Pomo chats with Claude using your own Anthropic API key, so you only pay Anthropic for what you "
            "use. The key is stored encrypted for your Windows account and is only ever sent to Anthropic.")


class PomoAvatar(QWidget):
    def __init__(self, size: int = 64, parent: QWidget | None = None):
        super().__init__(parent)
        self.pomo = Pomo()
        self.outfit_source = None  # callable returning the current outfit
        self.setFixedSize(size + 16, size + 22)
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
        if self.outfit_source is not None:
            self.pomo.outfit = self.outfit_source()
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
        self.avatar.outfit_source = lambda: self.ctrl.settings.pomo_outfit
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
        key_button = QPushButton("Key")
        key_button.setObjectName("ghost")
        key_button.setToolTip("Your Anthropic API key")
        key_button.clicked.connect(lambda: self._show_key_panel(not self.key_panel.isVisible()))
        head.addWidget(key_button, 0, Qt.AlignTop)
        clear = QPushButton("New chat")
        clear.setObjectName("ghost")
        clear.clicked.connect(self.new_chat)
        head.addWidget(clear, 0, Qt.AlignTop)
        root.addLayout(head)
        root.addWidget(self._build_key_panel())

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
        self._show_key_panel(not self.ctrl.has_ai_key())

    # -------------------------------------------------------------- your key
    def _build_key_panel(self) -> QFrame:
        self.key_panel = panel = QFrame()
        panel.setObjectName("card")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)
        title = QLabel(KEY_TITLE)
        title.setProperty("role", "title")
        text = QLabel(KEY_TEXT)
        text.setProperty("role", "secondary")
        text.setWordWrap(True)
        row = QHBoxLayout()
        self.key_input = QLineEdit()
        self.key_input.setEchoMode(QLineEdit.Password)
        self.key_input.setPlaceholderText("Paste your key (sk-ant-\u2026)")
        self.key_input.returnPressed.connect(self._save_key)
        self.key_save = QPushButton("Save key")
        self.key_save.setObjectName("primary")
        self.key_save.clicked.connect(self._save_key)
        row.addWidget(self.key_input, 1)
        row.addWidget(self.key_save)
        links = QHBoxLayout()
        get_key = QPushButton("Get a key")
        get_key.setObjectName("ghost")
        get_key.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(KEYS_URL)))
        self.key_remove = QPushButton("Remove saved key")
        self.key_remove.setObjectName("ghost")
        self.key_remove.clicked.connect(self._remove_key)
        links.addWidget(get_key)
        links.addWidget(self.key_remove)
        links.addStretch(1)
        self.key_status = QLabel()
        self.key_status.setProperty("role", "muted")
        self.key_status.setWordWrap(True)
        lay.addWidget(title)
        lay.addWidget(text)
        lay.addLayout(row)
        lay.addLayout(links)
        lay.addWidget(self.key_status)
        self._key_check: KeyCheckWorker | None = None
        return panel

    def _show_key_panel(self, show: bool) -> None:
        saved = self.ctrl.ai_key()
        self.key_remove.setVisible(bool(saved))
        if saved:
            self._set_key_status(f"Using your saved key ({keystore.mask(saved)}). Paste a new one to replace it.")
        elif self.ctrl.has_ai_key():
            self._set_key_status("Using the key from your environment (ANTHROPIC_API_KEY).")
        else:
            self._set_key_status("")
        self.key_panel.setVisible(show)
        ready = self.ctrl.has_ai_key()
        self.input.setEnabled(ready)
        self.send_button.setEnabled(ready)
        for chip in self.chip_buttons:
            chip.setEnabled(ready and self.worker is None)
        if show:
            self.key_input.setFocus()

    def refresh_key(self) -> None:
        """The key changed elsewhere (Settings): ask for one only if there is none."""
        self._show_key_panel(not self.ctrl.has_ai_key())

    def _set_key_status(self, text: str, tone: str | None = None) -> None:
        color = {"good": self.theme.success, "bad": self.theme.danger}.get(tone)
        self.key_status.setStyleSheet(f"color: {color};" if color else "")
        self.key_status.setText(text)
        self.key_status.setVisible(bool(text))

    def _save_key(self) -> None:
        key = self.key_input.text().strip()
        if not key or self._key_check is not None:
            return
        try:
            keystore.save_key(self.ctrl.ai_key_path, key)
        except OSError as exc:
            self._set_key_status(str(exc), "bad")
            return
        self.key_input.clear()
        self.key_save.setEnabled(False)
        self._set_key_status("Checking your key with Anthropic\u2026")
        self._key_check = KeyCheckWorker(key, self.ctrl.settings.ai_model, self)
        self._key_check.result.connect(self._on_key_checked)
        self._key_check.finished.connect(self._key_check_done)
        self._key_check.start()

    def _on_key_checked(self, status: str, message: str) -> None:
        if status == "bad":
            keystore.delete_key(self.ctrl.ai_key_path)  # don't keep a key Anthropic rejected
        self._show_key_panel(True)
        self._set_key_status(message, "good" if status in ("ok", "warn") else "bad")
        if status in ("ok", "warn"):
            self.avatar.pomo.celebrate()
            self._add_bubble("Yay, thank you! Ask me anything, or tap one of the ideas below.", mine=False)
            QTimer.singleShot(1800, lambda: self._show_key_panel(False))

    def _key_check_done(self) -> None:
        self._key_check = None
        self.key_save.setEnabled(True)

    def _remove_key(self) -> None:
        keystore.delete_key(self.ctrl.ai_key_path)
        self._show_key_panel(True)

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
                item.widget().hide()  # gone now, not when deleteLater gets to it
                item.widget().deleteLater()
        self._add_bubble(GREETING, mine=False)

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
            chip.setEnabled(not busy and self.ctrl.has_ai_key())

    def shutdown(self) -> None:
        if self.worker is not None:
            self.worker.cancel()
            self.worker.wait(2000)
        if self._key_check is not None:
            self._key_check.wait(2000)
