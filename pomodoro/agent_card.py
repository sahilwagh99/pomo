"""Claude Code, answered from Pomo: a permission prompt, a multiple-choice question, or the end of a turn.

The card shows Claude's own words and options exactly as it sent them, and answers through the
hook that is waiting for it. If you don't answer in time, Claude Code carries on in the terminal.
"""
from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .theme import FONT_FAMILY, mode_color

SHADOW = 14
WIDTH = 400
GAP = 10
MESSAGE_MAX_H = 260
TITLES = {"permission": "{who} needs your OK", "question": "{who} has a question", "reply": "{who} finished"}


class _ReplyBox(QPlainTextEdit):
    """Enter sends, Shift+Enter starts a new line."""

    def __init__(self, on_send):
        super().__init__()
        self._on_send = on_send

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not event.modifiers() & Qt.ShiftModifier:
            self._on_send()
        else:
            super().keyPressEvent(event)


class AgentCard(QWidget):
    def __init__(self, ctrl):
        super().__init__(None)
        self.ctrl = ctrl
        self.approval = None
        self._accent = QColor("#95c283")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedWidth(WIDTH + 2 * SHADOW)
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(SHADOW + 16, SHADOW + 12, SHADOW + 14, SHADOW + 14)
        self._root.setSpacing(8)

    # ------------------------------------------------------------- showing
    def show_for(self, approval, anchor: QRect, wait_s: int) -> None:
        self.approval = approval
        self._accent = QColor(mode_color(self.ctrl.theme, self.ctrl.settings,
                                         {"permission": "short", "question": "long", "reply": "focus"}[approval.kind]))
        self._clear(self._root)
        head = QHBoxLayout()
        head.addSpacing(14)  # the accent dot is painted here
        who = approval.session.agent_label
        self._title = title = QLabel(TITLES[approval.kind].format(who=who))
        title.setProperty("role", "title")
        close = QToolButton()
        close.setObjectName("close")
        close.setText("✕")
        close.setCursor(Qt.PointingHandCursor)
        close.setToolTip("Answer in the terminal instead" if approval.kind != "reply" else "Done")
        close.clicked.connect(lambda: self._resolve("pass" if approval.kind == "reply" else "terminal"))
        head.addWidget(title, 1)
        head.addWidget(close, 0, Qt.AlignTop)
        self._root.addLayout(head)
        sub = QLabel(approval.session.project + (f"  ·  {approval.tool}" if approval.kind == "permission" else ""))
        sub.setProperty("role", "muted")
        self._root.addWidget(sub)
        {"permission": self._build_permission, "question": self._build_question,
         "reply": self._build_reply}[approval.kind](approval)
        hint = QLabel({"permission": f"If you don't answer within {wait_s} s, {who} asks in the terminal.",
                       "question": f"If you don't answer within {wait_s} s, {who} asks in the terminal.",
                       "reply": f"{who} waits {wait_s} s for a reply, then stops as usual."}[approval.kind])
        hint.setProperty("role", "muted")
        hint.setWordWrap(True)
        self._root.addWidget(hint)
        self._refit()
        self._position(anchor)
        self.show()
        self.raise_()
        self.update()

    def close_for(self, approval) -> None:
        if approval is self.approval:
            self.approval = None
            self.hide()

    @staticmethod
    def _clear(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
            elif item.layout():
                AgentCard._clear(item.layout())

    def _message_view(self, text: str, markdown: bool) -> QTextBrowser:
        view = QTextBrowser()
        view.setOpenExternalLinks(True)
        view.setFrameShape(QTextBrowser.NoFrame)
        t = self.ctrl.theme
        view.setStyleSheet(f"QTextBrowser {{ background: {t.raised}; border: 1px solid {t.border_qss};"
                           f" border-radius: 10px; padding: 6px 8px; color: {t.ink}; }}")
        if markdown:
            view.setMarkdown(text)
        else:
            mono = QFont("Cascadia Mono")
            mono.setStyleHint(QFont.Monospace)
            mono.setPixelSize(12)
            view.document().setDefaultFont(mono)
            view.setPlainText(text)
        view.document().setTextWidth(WIDTH - 50)
        view.setFixedHeight(int(min(MESSAGE_MAX_H, view.document().size().height() + 20)))
        return view

    def _button(self, text: str, primary: bool = False, ghost: bool = False) -> QPushButton:
        button = QPushButton(text)
        button.setCursor(Qt.PointingHandCursor)
        if primary:
            button.setObjectName("primary")
            button.setStyleSheet(f"QPushButton#primary {{ background: {self._accent.name()}; }}"
                                 f" QPushButton#primary:hover {{ background: {self._accent.lighter(112).name()}; }}")
        elif ghost:
            button.setObjectName("ghost")
        return button

    # ---------------------------------------------------------- permission
    def _build_permission(self, approval) -> None:
        self._root.addWidget(self._message_view(approval.detail or approval.tool, markdown=False))
        self.note = QLineEdit()
        self.note.setPlaceholderText(f"Tell {approval.session.agent_label} what to do instead (optional)")
        self.note.returnPressed.connect(lambda: self._resolve("deny", self.note.text()))
        self.note.hide()
        self._root.addWidget(self.note)
        row = QHBoxLayout()
        row.setSpacing(8)
        allow = self._button("Allow", primary=True)
        allow.clicked.connect(lambda: self._resolve("allow"))
        deny = self._button("Deny")
        deny.clicked.connect(lambda: self._resolve("deny", self.note.text()))
        with_note = self._button("Deny with a note", ghost=True)
        with_note.clicked.connect(lambda: (self.note.show(), self.note.setFocus(), with_note.hide(),
                                           self.activateWindow(), self._refit()))
        terminal = self._button("In terminal", ghost=True)
        terminal.clicked.connect(lambda: self._resolve("terminal"))
        for b in (allow, deny, with_note, terminal):
            row.addWidget(b)
        row.addStretch(1)
        self._root.addLayout(row)

    # ------------------------------------------------------------ questions
    def _build_question(self, approval) -> None:
        self._picks: list[QButtonGroup | list[QPushButton]] = []
        self._others: list[QLineEdit] = []
        questions = approval.questions
        one_click = len(questions) == 1 and not questions[0].get("multiSelect")
        for qi, q in enumerate(questions):
            if q.get("header"):
                chip = QLabel(q["header"].upper())
                chip.setProperty("role", "section")
                self._root.addWidget(chip)
            text = QLabel(q.get("question", ""))
            text.setWordWrap(True)
            text.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self._root.addWidget(text)
            group = QButtonGroup(self)
            group.setExclusive(not q.get("multiSelect"))
            buttons = []
            for option in q.get("options") or []:
                b = self._button(option["label"])
                b.setToolTip(option.get("description") or "")
                b.setStyleSheet("QPushButton { text-align: left; padding: 6px 10px; }"
                                f" QPushButton:checked {{ background: {self._accent.name()}; }}")
                if one_click:
                    b.clicked.connect(lambda _=False, label=option["label"]: self._resolve("answer", [label]))
                else:
                    b.setCheckable(True)
                    group.addButton(b)
                if option.get("description"):
                    holder = QVBoxLayout()
                    holder.setSpacing(0)
                    holder.addWidget(b)
                    desc = QLabel(option["description"])
                    desc.setProperty("role", "muted")
                    desc.setWordWrap(True)
                    desc.setContentsMargins(10, 0, 0, 2)
                    holder.addWidget(desc)
                    self._root.addLayout(holder)
                else:
                    self._root.addWidget(b)
                buttons.append(b)
            other = QLineEdit()
            other.setPlaceholderText("Or type your own answer")
            if one_click:
                other.returnPressed.connect(lambda o=other: o.text().strip() and self._resolve("answer", [o.text().strip()]))
            self._root.addWidget(other)
            self._picks.append(buttons)
            self._others.append(other)
        row = QHBoxLayout()
        if not one_click:
            send = self._button("Send answers", primary=True)
            send.clicked.connect(self._send_answers)
            row.addWidget(send)
        terminal = self._button("In terminal", ghost=True)
        terminal.clicked.connect(lambda: self._resolve("terminal"))
        row.addWidget(terminal)
        row.addStretch(1)
        self._root.addLayout(row)

    def answers(self) -> list[str]:
        """One answer per question: typed text wins, else the picked labels (comma-joined for multi-select)."""
        out = []
        for buttons, other in zip(self._picks, self._others):
            typed = other.text().strip()
            picked = [b.text() for b in buttons if b.isCheckable() and b.isChecked()]
            out.append(typed or ", ".join(picked))
        return out

    def _send_answers(self) -> None:
        answers = self.answers()
        if all(answers):
            self._resolve("answer", answers)

    # ---------------------------------------------------------------- reply
    def _build_reply(self, approval) -> None:
        who = approval.session.agent_label
        self._root.addWidget(self._message_view(approval.message or f"({who} finished without a message.)",
                                                markdown=True))
        self.reply_box = _ReplyBox(self._send_reply)
        self.reply_box.setPlaceholderText(f"Reply to {who} (Enter sends)")
        self.reply_box.setFixedHeight(58)
        self._root.addWidget(self.reply_box)
        row = QHBoxLayout()
        send = self._button("Send reply", primary=True)
        send.clicked.connect(self._send_reply)
        done = self._button("Done", ghost=True)
        done.clicked.connect(lambda: self._resolve("pass"))
        row.addWidget(send)
        row.addWidget(done)
        row.addStretch(1)
        self._root.addLayout(row)

    def _send_reply(self) -> None:
        text = self.reply_box.toPlainText().strip()
        if text:
            self._resolve("reply", text)

    # --------------------------------------------------------------- common
    def _resolve(self, decision: str, payload=None) -> None:
        approval, self.approval = self.approval, None
        self.hide()
        if approval is not None:
            self.ctrl.agents.resolve(approval.id, decision, payload)

    def _refit(self) -> None:
        # wrapped labels make sizeHint guess at a width; ask for the height at the card's real width
        self._root.invalidate()
        self._root.activate()
        height = (self._root.heightForWidth(self.width()) if self._root.hasHeightForWidth()
                  else self._root.sizeHint().height())
        self.setFixedHeight(max(height, self._root.minimumSize().height()))

    def mousePressEvent(self, event) -> None:
        self.activateWindow()  # so the reply box and notes can take typing
        super().mousePressEvent(event)

    def _position(self, anchor: QRect) -> None:
        screen = QGuiApplication.screenAt(anchor.center()) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        w, h = self.width(), self.height()
        if anchor.top() - geo.top() < 60:  # under the island
            x, y = anchor.center().x() - w // 2, anchor.bottom() + GAP - SHADOW
        else:  # beside the orb
            x = anchor.left() - GAP - (w - SHADOW)
            if x + SHADOW < geo.left():
                x = anchor.right() + GAP - SHADOW
            y = anchor.bottom() - (h - SHADOW)
        self.move(max(geo.left() - SHADOW, min(x, geo.right() + 1 - w + SHADOW)),
                  max(geo.top() - SHADOW, min(y, geo.bottom() + 1 - h + SHADOW)))

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
        p.setPen(Qt.NoPen)
        p.setBrush(self._accent)
        title = getattr(self, "_title", None)
        cy = title.geometry().center().y() if title is not None else SHADOW + 21
        p.drawEllipse(QRectF(SHADOW + 16, cy - 4.5, 9, 9))
        p.end()
