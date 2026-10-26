"""The "Ask Pomo" chat window."""
from __future__ import annotations

import time

from PySide6.QtCore import QPointF, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPainter, QTextDocument
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import keystore
from .ai import QUICK_ACTIONS, KeyCheckWorker, build_context, make_worker, quick_prompt, tool_results_messages
from .planner import MAX_TOOL_ROUNDS, ToolError, run_tool
from .providers import PROVIDERS, model_for, model_label, provider, set_model
from .pomo import BODY_RY, Pomo
from .winutil import set_dark_title_bar

GREETING = ("Hi, I'm Pomo! Ask me to plan your work into pomodoros, look at how your focus is going, "
            "or catch you up on what your coding agents did today.")
KEY_TITLE = "Bring your own key"


def key_text(provider_key: str) -> str:
    p = provider(provider_key)
    who = p.short if p.key != "anthropic" else "Anthropic"
    return (f"Pomo chats through {p.label} using your own API key, so you only pay {who} for what you use. "
            f"The key is stored encrypted for your Windows account and is only ever sent to {who}.")


class PomoAvatar(QWidget):
    def __init__(self, size: int = 64, parent: QWidget | None = None):
        super().__init__(parent)
        self.pomo = Pomo()
        self.outfit_source = None  # callable returning the current outfit
        self.listening_source = None  # callable: is music playing for Pomo to listen to
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
        if self.listening_source is not None:
            self.pomo.listening = self.listening_source()
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
        self._continue: list | None = None  # tool results to send back once the current reply's thread ends
        self._history_provider = ctrl.settings.ai_provider
        self._rounds = 0
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
        self.avatar.outfit_source = self.ctrl.current_outfit
        self.avatar.listening_source = self.ctrl.pomo_listening
        head.addWidget(self.avatar)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        title = QLabel("Ask Pomo")
        title.setProperty("role", "h1")
        # the model in use; click it to switch provider or model
        self.subtitle = QToolButton()
        self.subtitle.setAutoRaise(True)
        self.subtitle.setPopupMode(QToolButton.InstantPopup)
        self.subtitle.setCursor(Qt.PointingHandCursor)
        self.subtitle.setToolTip("Choose the AI provider and model")
        self.subtitle.setStyleSheet(f"QToolButton {{ color: {self.theme.muted}; font-size: 12px; border: none;"
                                    " padding: 0; text-align: left; } QToolButton::menu-indicator { image: none; }")
        self.model_menu = QMenu(self.subtitle)
        self.model_menu.aboutToShow.connect(self._fill_model_menu)
        self.subtitle.setMenu(self.model_menu)
        titles.addStretch(1)
        titles.addWidget(title)
        titles.addWidget(self.subtitle, 0, Qt.AlignLeft)
        titles.addStretch(1)
        head.addLayout(titles, 1)
        key_button = QPushButton("Key")
        key_button.setObjectName("ghost")
        key_button.setToolTip("Your API key")
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
        self.key_text = text = QLabel()
        text.setProperty("role", "secondary")
        text.setWordWrap(True)
        row = QHBoxLayout()
        self.key_input = QLineEdit()
        self.key_input.setEchoMode(QLineEdit.Password)
        self.key_input.setPlaceholderText("Paste your key")
        self.key_input.returnPressed.connect(self._save_key)
        self.key_save = QPushButton("Save key")
        self.key_save.setObjectName("primary")
        self.key_save.clicked.connect(self._save_key)
        row.addWidget(self.key_input, 1)
        row.addWidget(self.key_save)
        links = QHBoxLayout()
        get_key = QPushButton("Get a key")
        get_key.setObjectName("ghost")
        get_key.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(provider(self.ctrl.settings.ai_provider).keys_url)))
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
        p = provider(self.ctrl.settings.ai_provider)
        self.key_text.setText(key_text(p.key))
        self.key_input.setPlaceholderText(f"Paste your {p.short} key ({p.key_hint})")
        saved = self.ctrl.ai_key()
        self.key_remove.setVisible(bool(saved))
        if saved:
            self._set_key_status(f"Using your saved key ({keystore.mask(saved)}). Paste a new one to replace it.")
        elif self.ctrl.has_ai_key():
            self._set_key_status(f"Using the key from your environment ({' or '.join(p.env)}).")
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
        """The key or provider changed elsewhere (Settings): ask for a key only if there is none."""
        self._update_subtitle()
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
        s = self.ctrl.settings
        self._set_key_status(f"Checking your key with {provider(s.ai_provider).short}\u2026")
        self._key_check = KeyCheckWorker(s.ai_provider, key, model_for(s), self)
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
        self._update_subtitle()
        self.input.setFocus()

    def _update_subtitle(self) -> None:
        s = self.ctrl.settings
        p = provider(s.ai_provider)
        self.privacy.setText("Your question and a short summary of your focus stats, tasks and agent activity "
                             f"are sent to {p.label}.")
        label = model_label(p.key, model_for(s))
        self.subtitle.setText(f"{label}  ·  {p.short}  ▾" if p.key != "anthropic" else f"{label}  ▾")

    def _fill_model_menu(self) -> None:
        menu, s = self.model_menu, self.ctrl.settings
        menu.clear()
        heading = QFont(self.font())
        heading.setBold(True)
        for i, p in enumerate(PROVIDERS.values()):
            if i:
                menu.addSeparator()
            title = menu.addAction(p.label)  # a heading: menu sections don't draw their text in every style
            title.setEnabled(False)
            title.setFont(heading)
            current = model_for(s, p.key)
            models = list(p.models)
            if current not in dict(models):
                models.append((current, current))  # a model typed in Settings
            for model, label in models:
                act = menu.addAction(label)
                act.setCheckable(True)
                act.setChecked(p.key == s.ai_provider and model == model_for(s))
                act.triggered.connect(lambda _=False, k=p.key, m=model: self.choose_model(k, m))
        menu.addSeparator()
        menu.addAction("More models and keys in Settings…").triggered.connect(lambda: self.ctrl.open_settings("AI"))

    def choose_model(self, provider_key: str, model: str) -> None:
        s = self.ctrl.settings
        switching = provider_key != s.ai_provider
        s.ai_provider = provider_key
        set_model(s, provider_key, model)
        self.ctrl.save_settings()
        self._update_subtitle()
        if switching and self.history and self.worker is None:
            self.new_chat()  # each provider keeps the conversation in its own format
        self.refresh_key()

    def new_chat(self) -> None:
        if self.worker is not None:
            self.worker.cancel()  # its late signals find no reply bubble and no pending turn
        self._reply = None
        self._pending = []
        self._continue = None
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

    def _add_note(self, text: str) -> None:
        """A small line for something Pomo did (started a session, planned tasks)."""
        note = QLabel(f"✓  {text}")
        note.setProperty("role", "muted")
        note.setWordWrap(True)
        note.setAlignment(Qt.AlignCenter)
        self.thread.insertWidget(self.thread.count() - 1, note)
        QTimer.singleShot(0, self._scroll_to_end)

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
        self._rounds = 0
        if self.history and self._history_provider != self.ctrl.settings.ai_provider:
            self.history.clear()  # it was in another provider's format; this question starts afresh
        self._history_provider = self.ctrl.settings.ai_provider
        self._request([user, context])

    def _request(self, pending: list) -> None:
        """Ask Claude to reply to the history plus these new messages (a question, or tool results)."""
        self._pending = pending
        self._reply = self._add_bubble("", mine=False)
        self._reply_text = ""
        self.avatar.pomo.signal = "busy"
        s = self.ctrl.settings
        self.worker = make_worker(s.ai_provider, self.ctrl.ai_key() or self.ctrl.env_ai_key(), model_for(s),
                                  self.history + self._pending, self)
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

    def _on_completed(self, reply) -> None:
        if not self._pending:  # the chat was reset while this reply streamed
            return
        # append-only history: the user turn, its context, and the reply exactly as returned
        self.history += self._pending + [reply.assistant_message]
        self._pending = []
        if not reply.tool_calls:
            self.avatar.pomo.hello()
            return
        results, notes = [], []
        cut_off = not reply.complete  # e.g. out of tokens: the input may be incomplete
        for call in reply.tool_calls:
            if cut_off:
                results.append((call.id, "Not run: the reply was cut off before the call was complete.", True))
                continue
            try:
                if call.input is None:
                    raise ToolError(f"the arguments weren't valid JSON: {call.raw[:200]}")
                result, note = run_tool(self.ctrl, call.name, call.input)
            except ToolError as exc:
                results.append((call.id, str(exc), True))
                continue
            results.append((call.id, result, False))
            if note:
                notes.append(note)
        if notes:
            self._add_note("  ·  ".join(notes))
            self.avatar.pomo.hello()
        self._rounds += 1
        messages = tool_results_messages(self.ctrl.settings.ai_provider, results)  # every call answered
        if cut_off or self._rounds > MAX_TOOL_ROUNDS:
            self.history += messages
        else:
            self._continue = messages  # the model finishes its answer once this thread is done

    def _end(self, message: str) -> None:
        if self._reply is not None:
            self._reply.set_text(message)
        self._pending = []

    def _stop(self) -> None:
        self._continue = None
        if self.worker is not None:
            self.worker.cancel()
            if self._reply is not None:
                self._reply.set_text((self._reply_text or "") + "\n\n*(stopped)*")
            self._pending = []

    def _on_thread_done(self) -> None:
        self.worker = None
        self.avatar.pomo.signal = None
        if self._reply is not None and not self._reply_text and not self._reply.text.strip("…"):
            self._reply.hide()  # the reply was only tool calls
            self._reply.deleteLater()
            self._reply = None
        follow_up, self._continue = self._continue, None
        if follow_up is not None:
            self._request(follow_up)
            return
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
