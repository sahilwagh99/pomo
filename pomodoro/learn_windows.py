"""The learning loop's windows: the curious question, Teach Pomo, card review, and riding out an urge."""
from __future__ import annotations

import math
import random
import time
from datetime import date

from PySide6.QtCore import QObject, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from . import learnloop
from .pomo import BODY_RY, Pomo
from .stats import plural
from .winutil import bring_to_front, set_dark_title_bar

SHADOW = 14
URGE_S = 90  # an urge usually peaks and passes within a minute or two


class OneShot(QObject):
    """One reply from the AI you picked in Ask Pomo, outside the chat (no tools, its own instructions)."""

    done = Signal(str)
    failed = Signal(str)

    def __init__(self, ctrl, system: str, messages: list[dict], parent: QObject | None = None):
        super().__init__(parent)
        from .ai import make_worker
        from .providers import model_for

        s = ctrl.settings
        self._text = ""
        self.worker = make_worker(s.ai_provider, ctrl.ai_key() or ctrl.env_ai_key(), model_for(s), messages, self,
                                  tools=False, system=system)
        self.worker.text.connect(self._chunk)
        self.worker.completed.connect(lambda reply: self.done.emit((reply.text or self._text).strip()))
        self.worker.failed.connect(self.failed.emit)
        self.worker.refused.connect(lambda: self.failed.emit("The AI couldn't help with that one."))

    def _chunk(self, text: str) -> None:
        self._text += text

    def start(self) -> None:
        self.worker.start()


def _card_paint(widget: QWidget, theme) -> None:
    """A soft floating card with a shadow (like the parked-thought card)."""
    p = QPainter(widget)
    p.setRenderHint(QPainter.Antialiasing)
    card = QRectF(widget.rect()).adjusted(SHADOW, SHADOW, -SHADOW, -SHADOW)
    p.setPen(Qt.NoPen)
    for i in range(SHADOW, 0, -1):
        p.setBrush(theme.shadow(int(theme.shadow_alpha * 0.22 * (1 - i / SHADOW) ** 2)))
        p.drawRoundedRect(card.adjusted(-i, -i + 4, i, i + 4), 14 + i, 14 + i)
    p.setBrush(QColor(theme.surface))
    p.setPen(QPen(theme.border, 1))
    p.drawRoundedRect(card.adjusted(0.5, 0.5, -0.5, -0.5), 14, 14)
    p.end()


def _place_by(widget: QWidget, anchor: QRect) -> None:
    """Below the island, or above the orb."""
    screen = QGuiApplication.screenAt(anchor.center()) or QGuiApplication.primaryScreen()
    geo = screen.availableGeometry()
    if anchor.top() - geo.top() < 60:
        x, y = anchor.center().x() - widget.width() // 2, anchor.bottom() + 10 - SHADOW
    else:
        x, y = anchor.center().x() - widget.width() // 2, anchor.top() - 10 - widget.height() + SHADOW
    widget.move(max(geo.left() - SHADOW, min(x, geo.right() + 1 - widget.width() + SHADOW)),
                max(geo.top() - SHADOW, min(y, geo.bottom() + 1 - widget.height() + SHADOW)))


# ------------------------------------------------------------------ curious first
class CuriousCard(QWidget):
    """Before a step: one question it will answer. Guess, or just start."""

    def __init__(self, ctrl):
        super().__init__(None)
        self.ctrl = ctrl
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedWidth(360 + 2 * SHADOW)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SHADOW + 16, SHADOW + 12, SHADOW + 16, SHADOW + 12)
        lay.setSpacing(6)
        title = QLabel("Curious first")
        title.setProperty("role", "title")
        self.question = QLabel()
        self.question.setWordWrap(True)
        self.guess = QLineEdit()
        self.guess.setPlaceholderText("Your guess (it's fine to be wrong)")
        self.guess.setMaxLength(200)
        self.guess.returnPressed.connect(self._save)
        hint = QLabel("You'll find out in this step. Enter saves your guess, Esc closes.")
        hint.setProperty("role", "muted")
        hint.setWordWrap(True)
        lay.addWidget(title)
        lay.addWidget(self.question)
        lay.addWidget(self.guess)
        lay.addWidget(hint)
        self._close = QTimer(self)
        self._close.setSingleShot(True)
        self._close.timeout.connect(self.hide)

    def ask(self, question: str, anchor: QRect) -> None:
        self.question.setText(question)
        self.guess.clear()
        self.guess.setEnabled(True)
        self.adjustSize()
        _place_by(self, anchor)
        self.show()
        bring_to_front(self)
        self.guess.setFocus()
        self._close.start(90_000)  # it doesn't hang around all session

    def _save(self) -> None:
        text = self.guess.text().strip()
        if text:
            self.ctrl.settings.curious["guess"] = text[:200]
            self.ctrl.save_settings()
        self.guess.setEnabled(False)
        self._close.start(700)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, _event) -> None:
        _card_paint(self, self.ctrl.theme)


# ------------------------------------------------------------------ teach Pomo
class TeachWindow(QWidget):
    """Explain a step to Pomo; he asks one follow-up, then turns it into review cards."""

    def __init__(self, ctrl, topic: str, goal: str = "", milestone: str = ""):
        super().__init__(None, Qt.Window)
        self.ctrl = ctrl
        self.topic = topic
        self.goal, self.milestone = goal, milestone
        self.turns: list[dict] = []
        self.taught: list[str] = []
        self.job: OneShot | None = None
        self.finished_teaching = False
        self.setWindowTitle("Pomo · Teach Pomo")
        self.setWindowIcon(ctrl.icon)
        self.resize(500, 620)
        page = QWidget()
        page.setObjectName("page")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(page)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(8)
        from .chat import PomoAvatar

        top = QHBoxLayout()
        self.avatar = PomoAvatar(54)
        top.addWidget(self.avatar, 0, Qt.AlignTop)
        words = QVBoxLayout()
        title = QLabel("Teach Pomo")
        title.setProperty("role", "h1")
        self.sub = QLabel()
        self.sub.setWordWrap(True)
        self.sub.setProperty("role", "secondary")
        words.addWidget(title)
        words.addWidget(self.sub)
        top.addLayout(words, 1)
        lay.addLayout(top)
        self.talk = QTextBrowser()
        self.talk.setMinimumHeight(200)
        lay.addWidget(self.talk, 1)
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText("Explain it in your own words, as if to a friend who's new to it…")
        self.input.setFixedHeight(110)
        lay.addWidget(self.input)
        row = QHBoxLayout()
        self.status = QLabel()
        self.status.setProperty("role", "muted")
        self.send_btn = QPushButton("Teach")
        self.send_btn.setObjectName("primary")
        self.send_btn.clicked.connect(self._send)
        row.addWidget(self.status, 1)
        row.addWidget(self.send_btn)
        lay.addLayout(row)
        curious = ctrl.settings.curious
        question = curious.get("question", "") if curious.get("task") == topic else ""
        intro = f"Explain **{topic}** to me in a few sentences. I'm new to it!"
        if question:
            intro += f"\n\nAnd before, I wondered: *{question}*"
            if curious.get("guess"):
                intro += f" (You guessed: {curious['guess']}.) Can you answer it now?"
            else:
                intro += " Can you answer it now?"
        self.sub.setText("Explaining what you learned is one of the best ways to understand it, and Pomo turns "
                         "it into review cards for later.")
        self._lines = [f"**Pomo:** {intro}"]
        self._render()
        if not ctrl.has_ai_key():
            self.status.setText("No AI key: your explanation is saved to your study log, and you can add review "
                                "cards yourself in Review cards.")

    def showEvent(self, event) -> None:
        super().showEvent(event)
        set_dark_title_bar(self, self.ctrl.theme.dark)
        self.input.setFocus()

    def _render(self) -> None:
        self.talk.setMarkdown("\n\n".join(self._lines))
        bar = self.talk.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _send(self) -> None:
        if self.finished_teaching:
            self.close()
            return
        text = self.input.toPlainText().strip()
        if not text or self.job is not None:
            return
        self.input.clear()
        self.taught.append(text)
        self._lines.append(f"**You:** {text}")
        self._render()
        if len(self.taught) == 1:
            self.ctrl.add_study_log(f"Taught Pomo about {self.topic}: {text}")  # it's English practice too
        if not self.ctrl.has_ai_key():
            self._finish_without_ai()
            return
        if not self.turns:
            self.turns.append({"role": "user", "content": learnloop.context(self.goal, self.topic, self.milestone)
                               + f"\n\nMy explanation: {text}"})
        else:
            self.turns.append({"role": "user", "content": text})
        self._ask(learnloop.TEACH_PROMPT, self.turns, self._replied, "Pomo is thinking…")

    def _ask(self, system: str, messages: list[dict], on_done, status: str) -> None:
        self.status.setText(status)
        self.send_btn.setEnabled(False)
        self.avatar.pomo.signal = "busy"
        self.job = OneShot(self.ctrl, system, messages, self)
        self.job.done.connect(on_done)
        self.job.failed.connect(self._failed)
        self.job.worker.finished.connect(self._job_done)
        self.job.start()

    def _job_done(self) -> None:
        self.job = None
        self.avatar.pomo.signal = None
        self.send_btn.setEnabled(len(self.taught) < 2 or self.finished_teaching)

    def _failed(self, message: str) -> None:
        self.status.setText(message)

    def _replied(self, text: str) -> None:
        self.turns.append({"role": "assistant", "content": text})
        self._lines.append(f"**Pomo:** {text}")
        self._render()
        self.avatar.pomo.hello()
        if len(self.taught) >= 2:  # his wrap-up: now the cards
            self.send_btn.setEnabled(False)
            QTimer.singleShot(0, self._make_cards)
        else:
            self.status.setText("Answer Pomo's question, and he'll make your review cards.")

    def _make_cards(self) -> None:
        taught = "\n".join(self.taught)
        messages = [{"role": "user", "content": learnloop.context(self.goal, self.topic, self.milestone)
                     + f"\n\nWhat the learner taught:\n{taught}"}]
        self._ask(learnloop.CARDS_PROMPT, messages, self._cards_made, "Making your review cards…")

    def _cards_made(self, text: str) -> None:
        cards = learnloop.parse_cards(text)
        due = learnloop.first_due(date.today())
        for q, a in cards:
            self.ctrl.storage.add_card(self.topic, q, a, due)
        if cards:
            listed = "\n".join(f"- {q}" for q, _a in cards)
            self._lines.append(f"**{plural(len(cards), 'review card')} for tomorrow:**\n\n{listed}")
            self.status.setText("Pomo will ask you these tomorrow, then again a few days later.")
        else:
            self.status.setText("Pomo couldn't make cards this time. You can add your own in Review cards.")
        self._render()
        self.ctrl.settings.curious = {}
        self.ctrl.save_settings()
        self.ctrl.cards_changed()
        self._done()

    def _finish_without_ai(self) -> None:
        self._lines.append("**Pomo:** Thank you! That's saved in your study log. Add a review card or two in "
                           "*Review cards* to remember the key ideas.")
        self._render()
        self.ctrl.settings.curious = {}
        self.ctrl.save_settings()
        self._done()

    def _done(self) -> None:
        self.finished_teaching = True
        self.input.hide()
        self.send_btn.setText("Done")
        self.send_btn.setEnabled(True)

    def closeEvent(self, event) -> None:
        if self.job is not None:
            self.job.worker.cancel()
        super().closeEvent(event)


# ------------------------------------------------------------------ recall
class RecallWindow(QWidget):
    """Review the cards that are due: the question, then the answer, then Got it or Not yet."""

    def __init__(self, ctrl):
        super().__init__(None, Qt.Window)
        self.ctrl = ctrl
        self.queue = []
        self.done_n = self.right = 0
        self.setWindowTitle("Pomo · Review cards")
        self.setWindowIcon(ctrl.icon)
        self.resize(460, 520)
        page = QWidget()
        page.setObjectName("page")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(page)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(10)
        title = QLabel("Review cards")
        title.setProperty("role", "h1")
        self.count = QLabel()
        self.count.setProperty("role", "muted")
        lay.addWidget(title)
        lay.addWidget(self.count)
        self.topic = QLabel()
        self.topic.setProperty("role", "muted")
        self.question = QLabel()
        self.question.setWordWrap(True)
        self.question.setProperty("role", "title")
        self.question.setMinimumHeight(70)
        self.answer = QLabel()
        self.answer.setWordWrap(True)
        self.answer.setProperty("role", "secondary")
        self.answer.setMinimumHeight(60)
        lay.addWidget(self.topic)
        lay.addWidget(self.question)
        lay.addWidget(self.answer)
        lay.addStretch(1)
        row = QHBoxLayout()
        self.show_btn = QPushButton("Show the answer")
        self.show_btn.setObjectName("primary")
        self.show_btn.clicked.connect(self._reveal)
        self.miss_btn = QPushButton("Not yet")
        self.miss_btn.setObjectName("ghost")
        self.miss_btn.clicked.connect(lambda: self._grade(False))
        self.got_btn = QPushButton("Got it")
        self.got_btn.setObjectName("primary")
        self.got_btn.clicked.connect(lambda: self._grade(True))
        row.addStretch(1)
        for b in (self.show_btn, self.miss_btn, self.got_btn):
            row.addWidget(b)
        lay.addLayout(row)
        add = QLabel("Add your own card:")
        add.setProperty("role", "muted")
        self.new_q = QLineEdit()
        self.new_q.setPlaceholderText("Question")
        self.new_a = QLineEdit()
        self.new_a.setPlaceholderText("Answer")
        self.new_a.returnPressed.connect(self._add)
        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._add)
        new_row = QHBoxLayout()
        new_row.addWidget(self.new_q, 3)
        new_row.addWidget(self.new_a, 2)
        new_row.addWidget(add_btn)
        lay.addSpacing(8)
        lay.addWidget(add)
        lay.addLayout(new_row)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        set_dark_title_bar(self, self.ctrl.theme.dark)
        self.load()

    def load(self) -> None:
        self.queue = self.ctrl.storage.cards(due_by=date.today())
        self.done_n = self.right = 0
        self._next()

    def _next(self) -> None:
        total = self.done_n + len(self.queue)
        if not self.queue:
            self.topic.setText("")
            if self.done_n:
                self.question.setText(f"All done! {self.right} of {plural(self.done_n, 'card')} remembered.")
                self.answer.setText("The ones you missed come back tomorrow; the others in a few days.")
            else:
                upcoming = self.ctrl.storage.cards()
                self.question.setText("Nothing to review right now.")
                self.answer.setText(f"{plural(len(upcoming), 'card')} waiting for later. Teach Pomo a step to "
                                    "make more, or add your own below." if upcoming else
                                    "Teach Pomo a step to make cards, or add your own below.")
            self.count.setText("")
            for b in (self.show_btn, self.miss_btn, self.got_btn):
                b.hide()
            return
        card = self.queue[0]
        self.count.setText(f"Card {self.done_n + 1} of {total}")
        self.topic.setText(card.topic)
        self.question.setText(card.question)
        self.answer.setText("")
        self.show_btn.show()
        self.miss_btn.hide()
        self.got_btn.hide()

    def _reveal(self) -> None:
        if self.queue:
            self.answer.setText(self.queue[0].answer)
            self.show_btn.hide()
            self.miss_btn.show()
            self.got_btn.show()

    def _grade(self, got_it: bool) -> None:
        card = self.queue.pop(0)
        box, due = learnloop.next_review(card.box, got_it, date.today())
        self.ctrl.storage.review_card(card.id, box, due)
        self.done_n += 1
        self.right += int(got_it)
        if not self.queue:
            self.ctrl.cards_changed()
        self._next()

    def _add(self) -> None:
        q, a = self.new_q.text().strip(), self.new_a.text().strip()
        if q and a:
            self.ctrl.storage.add_card(self.ctrl.engine.task or "My cards", q, a, learnloop.first_due(date.today()))
            self.new_q.clear()
            self.new_a.clear()
            self.new_q.setFocus()
            self.ctrl.cards_changed()
            self.count.setText("Added. It comes up tomorrow.")


# ------------------------------------------------------------------ ride the urge
class UrgeCard(QWidget):
    """90 seconds to breathe through an urge (to play, to scroll...): it rises, peaks and passes."""

    finished = Signal(bool)  # rode it out

    def __init__(self, ctrl):
        super().__init__(None)
        self.ctrl = ctrl
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(420 + 2 * SHADOW, 340 + 2 * SHADOW)
        self.pomo = Pomo(random.Random())
        self.pomo._next_fidget = 1e9
        self._start = 0.0
        self._last = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SHADOW + 20, SHADOW + 16, SHADOW + 20, SHADOW + 14)
        lay.addStretch(1)
        row = QHBoxLayout()
        self.back = QPushButton("Back to work")
        self.back.setObjectName("primary")
        self.back.clicked.connect(lambda: self._end(True))
        self.give = QPushButton("I still want to")
        self.give.setObjectName("ghost")
        self.give.clicked.connect(lambda: self._end(False))
        row.addStretch(1)
        row.addWidget(self.give)
        row.addWidget(self.back)
        lay.addLayout(row)

    def start(self) -> None:
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.center().x() - self.width() // 2, screen.center().y() - self.height() // 2)
        self._start = self._last = time.monotonic()
        self.back.setEnabled(False)
        self.give.setEnabled(False)
        self.show()
        bring_to_front(self)
        self._timer.start()

    def elapsed(self) -> float:
        return time.monotonic() - self._start

    def _tick(self) -> None:
        now = time.monotonic()
        self.pomo.update(now - self._last)
        self._last = now
        if self.elapsed() >= URGE_S and not self.back.isEnabled():
            self.back.setEnabled(True)
            self.give.setEnabled(True)
            self.pomo.hop(1.6)
        if self.elapsed() >= 20:  # you can always leave, just not in the first rush
            self.give.setEnabled(True)
        self.update()

    def _end(self, rode_out: bool) -> None:
        self._timer.stop()
        self.ctrl.storage.add_urge(rode_out)
        self.hide()
        self.finished.emit(rode_out)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape and self.give.isEnabled():
            self._end(False)
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event) -> None:
        _card_paint(self, self.ctrl.theme)
        t = self.ctrl.theme
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        inner = QRectF(self.rect()).adjusted(SHADOW + 20, SHADOW + 14, -SHADOW - 20, -SHADOW - 60)
        e = min(self.elapsed(), URGE_S)
        left = max(0, URGE_S - int(e))
        title_font = p.font()
        title_font.setPixelSize(17)
        title_font.setBold(True)
        p.setFont(title_font)
        p.setPen(QColor(t.ink))
        p.drawText(QRectF(inner.left(), inner.top(), inner.width(), 24), Qt.AlignLeft,
                   "Ride the urge" if left else "It passed. You rode it out.")
        small = p.font()
        small.setPixelSize(12)
        small.setBold(False)
        p.setFont(small)
        p.setPen(QColor(t.ink_2))
        p.drawText(QRectF(inner.left(), inner.top() + 26, inner.width(), 34), Qt.AlignLeft | Qt.TextWordWrap,
                   "An urge rises, peaks and fades, usually within a minute or two. Breathe with Pomo and watch it pass."
                   if left else "Each one you ride out makes the next one weaker.")
        # the wave: how strong urges usually feel over time, and where you are on it
        wave = QRectF(inner.left() + 130, inner.top() + 80, inner.width() - 130, 110)
        path = QPainterPath()
        for i in range(61):
            x = i / 60
            y = math.exp(-((x - 0.3) ** 2) / 0.03) * 0.9 + 0.08
            pt = QPointF(wave.left() + x * wave.width(), wave.bottom() - y * wave.height())
            path.moveTo(pt) if i == 0 else path.lineTo(pt)
        p.setPen(QPen(QColor(t.grid), 3, Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        x = e / URGE_S
        y = math.exp(-((x - 0.3) ** 2) / 0.03) * 0.9 + 0.08
        dot = QPointF(wave.left() + x * wave.width(), wave.bottom() - y * wave.height())
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(t.chart))
        p.drawEllipse(dot, 6, 6)
        # Pomo breathing: in for 4 seconds, out for 6
        phase = e % 10.0
        breath = phase / 4.0 if phase < 4.0 else 1.0 - (phase - 4.0) / 6.0
        size = 70 * (0.88 + 0.22 * breath)
        anchor = QPointF(inner.left() + 60, wave.bottom() - size * BODY_RY)
        self.pomo.paint(p, anchor, size)
        p.setPen(QColor(t.ink_2))
        p.drawText(QRectF(inner.left(), wave.bottom() + 8, 120, 18), Qt.AlignCenter,
                   ("Breathe in" if phase < 4.0 else "Breathe out") if left else "Nice.")
        clock = p.font()
        clock.setPixelSize(13)
        p.setFont(clock)
        p.setPen(QColor(t.muted))
        p.drawText(QRectF(wave.left(), wave.bottom() + 8, wave.width(), 18), Qt.AlignRight,
                   f"{left // 60}:{left % 60:02d}" if left else "")
        p.end()
