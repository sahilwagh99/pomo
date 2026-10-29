"""The study log: a few sentences in English, each day, about what you learned.

Writing it does two jobs. Putting what you learned into your own words makes it stick, and doing
it in English every day, a little at a time, builds the confidence that comes from practice where a
mistake costs nothing. With an AI key, Pomo can check the English gently: your text with minimal
corrections, a few notes on why, and one thing you did well. The log stays on your PC; it's only
sent to your AI provider when you ask for a check.
"""
from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .stats import plural
from .winutil import set_dark_title_bar

TUTOR_PROMPT = """You are a kind English tutor inside Pomo, a study app. The learner wrote a short study log in English about what they learned today. English is not their first language and they feel unsure about it, so be warm, specific and brief.

Reply in Markdown with these parts:

**Polished:** their log with minimal corrections. Keep their words, ideas and voice; fix only grammar, spelling and word choice that's wrong or unnatural.

**Notes:** up to three of the most useful fixes, each as *what they wrote* → *better*, and a few words on why. Leave this part out if the English was already right.

**Well done:** one sentence on something they did well (a good word, a clear explanation, a tricky tense used right).

Keep the whole reply under 160 words. Don't comment on the study topic itself."""

PROMPTS = (
    "What did you learn today? Explain one idea as if to a friend.",
    "What was hard today, and what did you try?",
    "What will you do tomorrow, and why?",
    "Describe something you understood today that confused you before.",
    "Write three sentences: what you studied, what clicked, what's still unclear.",
)
MIN_WORDS = 15  # below this, saving still works; the check asks for a bit more first


def streak(days: set[date], today: date) -> int:
    """Days in a row with a log, ending today (or yesterday, so today isn't lost before you write)."""
    day = today if today in days else today - timedelta(days=1)
    count = 0
    while day in days:
        count += 1
        day -= timedelta(days=1)
    return count


def words(text: str) -> int:
    return len(text.split())


def prompt_for(day: date) -> str:
    return PROMPTS[day.toordinal() % len(PROMPTS)]


def summary(storage, today: date) -> str:
    """For Ask Pomo's context."""
    entries = storage.logs(limit=3)
    if not entries:
        return "Study log: nothing written yet."
    days = storage.log_days()
    latest = entries[0]
    text = latest.text if len(latest.text) <= 300 else latest.text[:299] + "…"
    return (f"Study log (written in English, to practise it): {plural(len(days), 'day')} written, "
            f"{streak(days, today)}-day streak. Latest, {latest.day.isoformat()}: \"{text}\"")


class StudyLogWindow(QWidget):
    """Write today's log, have the English checked, and look back at earlier days."""

    def __init__(self, ctrl):
        super().__init__(None, Qt.Window)
        self.ctrl = ctrl
        self.worker = None
        self._checking: int | None = None   # the entry whose English is being checked
        self._feedback = ""
        self.setWindowTitle("Pomo · Study log")
        self.setWindowIcon(ctrl.icon)
        self.resize(520, 700)
        page = QWidget()
        page.setObjectName("page")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(page)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(8)

        title = QLabel("Study log")
        title.setProperty("role", "h1")
        sub = QLabel("A few sentences in English about what you learned today. Short is fine: writing every "
                     "day is what builds your English and helps you remember.")
        sub.setProperty("role", "muted")
        sub.setWordWrap(True)
        self.streak = QLabel()
        self.streak.setProperty("role", "secondary")
        lay.addWidget(title)
        lay.addWidget(sub)
        lay.addWidget(self.streak)
        lay.addSpacing(6)

        idea = QHBoxLayout()
        self.idea = QLabel()
        self.idea.setWordWrap(True)
        self.idea.setProperty("role", "secondary")
        another = QPushButton("Another idea")
        another.setObjectName("ghost")
        another.setCursor(Qt.PointingHandCursor)
        another.clicked.connect(self._next_idea)
        idea.addWidget(self.idea, 1)
        idea.addWidget(another, 0, Qt.AlignTop)
        lay.addLayout(idea)

        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("Today I learned…")
        self.editor.setMinimumHeight(140)
        self.editor.textChanged.connect(self._count)
        lay.addWidget(self.editor)
        row = QHBoxLayout()
        self.count = QLabel()
        self.count.setProperty("role", "muted")
        self.save_btn = QPushButton("Save")
        self.save_btn.setObjectName("ghost")
        self.save_btn.clicked.connect(lambda: self._save(check=False))
        self.check_btn = QPushButton("Save and check my English")
        self.check_btn.setObjectName("primary")
        self.check_btn.clicked.connect(lambda: self._save(check=True))
        row.addWidget(self.count)
        row.addStretch(1)
        row.addWidget(self.save_btn)
        row.addWidget(self.check_btn)
        lay.addLayout(row)

        self.feedback = QTextBrowser()
        self.feedback.setOpenExternalLinks(True)
        self.feedback.setMinimumHeight(150)
        self.feedback.hide()
        lay.addWidget(self.feedback)
        self.check_entry = QPushButton("Check this one")
        self.check_entry.setObjectName("ghost")
        self.check_entry.clicked.connect(self._check_selected)
        self.check_entry.hide()
        lay.addWidget(self.check_entry, 0, Qt.AlignRight)
        self._selected = None

        past = QLabel("Earlier days")
        past.setProperty("role", "title")
        lay.addSpacing(6)
        lay.addWidget(past)
        self.history = QListWidget()
        self.history.setTextElideMode(Qt.ElideRight)
        self.history.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.history.itemClicked.connect(self._show_entry)
        lay.addWidget(self.history, 1)
        self._idea_index = date.today().toordinal()
        self._next_idea(first=True)
        self.refresh()

    # ------------------------------------------------------------- showing
    def showEvent(self, event) -> None:
        super().showEvent(event)
        set_dark_title_bar(self, self.ctrl.theme.dark)
        self.refresh()
        self.editor.setFocus()

    def refresh(self) -> None:
        storage = self.ctrl.storage
        days = storage.log_days()
        n = streak(days, date.today())
        if days:
            self.streak.setText(f"{plural(n, 'day')} in a row  ·  {plural(len(days), 'day')} written"
                                + ("  ·  today's is done" if date.today() in days else ""))
        else:
            self.streak.setText("Your first entry starts the streak.")
        self.history.clear()
        for entry in storage.logs(limit=120):
            first = entry.text.splitlines()[0] if entry.text else ""
            item = QListWidgetItem(f"{entry.day:%a %d %b}  ·  {first[:70]}{'…' if len(first) > 70 else ''}")
            item.setData(Qt.UserRole, entry.id)
            if entry.feedback:
                item.setToolTip("Has notes on the English")
            self.history.addItem(item)
        self._count()

    def _next_idea(self, first: bool = False) -> None:
        if not first:
            self._idea_index += 1
        self.idea.setText(f"Idea: {PROMPTS[self._idea_index % len(PROMPTS)]}")

    def _count(self) -> None:
        n = words(self.editor.toPlainText())
        self.count.setText(plural(n, "word"))
        has_text = n > 0
        self.save_btn.setEnabled(has_text and self.worker is None)
        can_check = self.ctrl.has_ai_key()
        self.check_btn.setEnabled(has_text and can_check and self.worker is None)
        self.check_btn.setToolTip("" if can_check else "Add an AI key in Ask Pomo (or Settings > AI) to check your English")

    # -------------------------------------------------------------- saving
    def _save(self, check: bool) -> None:
        text = self.editor.toPlainText().strip()
        if not text or self.worker is not None:
            return
        entry = self.ctrl.add_study_log(text)
        self.editor.clear()
        self.refresh()
        if check:
            self.check(entry.id, text)
        else:
            self._show_text(f"**Saved.** {self._encouragement()}")

    def _encouragement(self) -> str:
        n = streak(self.ctrl.storage.log_days(), date.today())
        return f"That's {plural(n, 'day')} in a row. See you tomorrow!" if n > 1 else "Nice start. See you tomorrow!"

    def check(self, entry_id: int, text: str) -> None:
        """Ask the AI tutor to look over one entry's English."""
        if words(text) < MIN_WORDS:
            self._show_text(f"**Saved.** Write at least {MIN_WORDS} words next time and Pomo can check the "
                            "English for you. A few sentences is plenty.")
            return
        from .ai import make_worker
        from .providers import model_for

        s = self.ctrl.settings
        self._checking = entry_id
        self._feedback = ""
        self.check_entry.hide()
        self._show_text("*Reading your log…*")
        self.worker = make_worker(s.ai_provider, self.ctrl.ai_key() or self.ctrl.env_ai_key(), model_for(s),
                                  [{"role": "user", "content": text}], self, tools=False, system=TUTOR_PROMPT)
        self.worker.text.connect(self._on_text)
        self.worker.completed.connect(self._on_done)
        self.worker.failed.connect(lambda message: self._show_text(message))
        self.worker.refused.connect(lambda: self._show_text("Pomo couldn't check this one."))
        self.worker.finished.connect(self._worker_done)
        self.worker.start()
        self._count()

    def _on_text(self, chunk: str) -> None:
        self._feedback += chunk
        self._show_text(self._feedback)

    def _on_done(self, reply) -> None:
        text = (reply.text or self._feedback).strip()
        if text and self._checking is not None:
            self.ctrl.storage.set_log_feedback(self._checking, text)
            self._show_text(text)

    def _worker_done(self) -> None:
        if self.worker is not None:
            self.worker.deleteLater()
        self.worker = None
        self._checking = None
        self.refresh()

    def _show_text(self, markdown: str) -> None:
        self.feedback.setMarkdown(markdown)
        self.feedback.show()

    def _show_entry(self, item: QListWidgetItem) -> None:
        entry_id = item.data(Qt.UserRole)
        entry = next((e for e in self.ctrl.storage.logs(limit=500) if e.id == entry_id), None)
        if entry is None:
            return
        notes = entry.feedback or "*No notes on the English for this one yet.*"
        self._show_text(f"**{entry.day:%A %d %B}**\n\n{entry.text}\n\n---\n\n{notes}")
        self._selected = entry
        self.check_entry.setVisible(not entry.feedback and self.ctrl.has_ai_key() and self.worker is None)

    def _check_selected(self) -> None:
        self.check_entry.hide()
        if self._selected is not None:
            self.check(self._selected.id, self._selected.text)

    def closeEvent(self, event) -> None:
        if self.worker is not None:
            self.worker.cancel()
        super().closeEvent(event)
