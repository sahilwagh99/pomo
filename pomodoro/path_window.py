"""The My Path window: your goal, today's step, and every milestone and step to tick off or change."""
from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import studypath
from .charts import Meter
from .stats import plural
from .winutil import set_dark_title_bar

KIND, MI, SI = Qt.UserRole, Qt.UserRole + 1, Qt.UserRole + 2


def _label(text: str = "", role: str | None = None, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    if role:
        label.setProperty("role", role)
    label.setWordWrap(wrap)
    return label


class PathWindow(QWidget):
    def __init__(self, ctrl):
        super().__init__(None, Qt.Window)
        self.ctrl = ctrl
        self.setWindowTitle("Pomo · My path")
        self.setWindowIcon(ctrl.icon)
        self.resize(560, 720)
        page = QWidget()
        page.setObjectName("page")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(page)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(8)

        self.goal = _label("", "h1", wrap=True)
        self.why = _label("", "secondary", wrap=True)
        self.status = _label("", "muted")
        self.meter = Meter()
        lay.addWidget(self.goal)
        lay.addWidget(self.why)
        lay.addWidget(self.status)
        lay.addWidget(self.meter)
        self.views = QWidget()
        views = QHBoxLayout(self.views)
        views.setContentsMargins(0, 4, 0, 0)
        for text, slot in (("Skill tree", ctrl.open_skill_tree), ("Big picture", ctrl.open_big_picture)):
            b = QPushButton(text)
            b.setObjectName("ghost")
            b.clicked.connect(slot)
            views.addWidget(b)
        views.addStretch(1)
        lay.addWidget(self.views)
        lay.addSpacing(6)

        self.today = QFrame()
        self.today.setObjectName("card")
        today = QVBoxLayout(self.today)
        today.setContentsMargins(16, 12, 16, 14)
        today.setSpacing(4)
        today.addWidget(_label("Today's step", "muted"))
        self.step = _label("", "title", wrap=True)
        self.step_note = _label("", "secondary", wrap=True)
        today.addWidget(self.step)
        today.addWidget(self.step_note)
        buttons = QHBoxLayout()
        self.small_btn = QPushButton()
        self.small_btn.setObjectName("primary")
        self.small_btn.clicked.connect(lambda: self._start_today(small=True))
        self.full_btn = QPushButton("Start a full session")
        self.full_btn.setObjectName("ghost")
        self.full_btn.clicked.connect(lambda: self._start_today(small=False))
        buttons.addWidget(self.small_btn)
        buttons.addWidget(self.full_btn)
        buttons.addStretch(1)
        today.addSpacing(4)
        today.addLayout(buttons)
        lay.addWidget(self.today)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(2)
        self.tree.setRootIsDecorated(True)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.tree.itemChanged.connect(self._changed)
        lay.addWidget(self.tree, 1)
        self.hint = _label("Tick a step when it's done. Double-click to rename, right-click for more.", "muted", wrap=True)
        lay.addWidget(self.hint)

        self.empty = _label("", "secondary", wrap=True)
        self.empty.setText(
            "<b>No path yet.</b><br><br>Not sure what to study, or where to start? Ask Pomo: it asks you a few "
            "short questions, then makes a path of small steps toward one goal, to try for 8 weeks. "
            "You'll always know what to work on next, and you can change direction at the end.")
        self.empty.setTextFormat(Qt.RichText)
        lay.addWidget(self.empty)

        row = QHBoxLayout()
        self.ask_btn = QPushButton()
        self.ask_btn.setObjectName("primary")
        self.ask_btn.clicked.connect(self._ask)
        new = QPushButton("New path…")
        new.setObjectName("ghost")
        new.clicked.connect(self._new_path)
        row.addWidget(self.ask_btn)
        row.addStretch(1)
        row.addWidget(new)
        lay.addLayout(row)
        self.refresh()

    # ------------------------------------------------------------- showing
    def showEvent(self, event) -> None:
        super().showEvent(event)
        set_dark_title_bar(self, self.ctrl.theme.dark)
        self.refresh()

    def refresh(self) -> None:
        path = self.ctrl.settings.study_path
        has = bool(path)
        today = date.today()
        for w in (self.why, self.status, self.meter, self.views, self.tree, self.hint):
            w.setVisible(has)
        self.empty.setVisible(not has)
        self.ask_btn.setText("Ask Pomo about my path" if has else "Make my path with Ask Pomo")
        if not has:
            self.goal.setText("My path")
            self.today.hide()
            return
        self.goal.setText(path["goal"])
        self.why.setText(path["why"])
        self.why.setVisible(bool(path["why"]))
        week, weeks = studypath.week_of(path, today)
        done, total = studypath.progress(path)
        status = f"Week {min(week, weeks)} of {weeks}  ·  {done} of {plural(total, 'step')} done"
        if studypath.review_due(path, today) or week > weeks:
            status += "  ·  time to decide: keep going or change direction?"
        self.status.setText(status)
        self.meter.set(done, max(1, total), QColor(self.ctrl.theme.chart))
        nxt = studypath.next_step(path)
        self.today.setVisible(True)
        if nxt is None:
            self.step.setText("Every step is done!" if total else "Add your first step below, or ask Pomo.")
            self.step_note.setText("Ask Pomo what's next, or add more steps." if total else "")
            self.small_btn.hide()
            self.full_btn.hide()
        else:
            mi, _si, st = nxt
            self.step.setText(st["title"])
            self.step_note.setText(f"{studypath.step_label(st).removeprefix(st['title']).strip(' ()')}  ·  "
                                   f"{path['milestones'][mi]['title']}")
            self.small_btn.setText(f"Start with just {self.ctrl.settings.starter_min} minutes")
            self.small_btn.show()
            self.full_btn.show()
        self._fill_tree(path, nxt)

    def _fill_tree(self, path: dict, nxt) -> None:
        tree = self.tree
        tree.blockSignals(True)
        tree.clear()
        bold = QFont(tree.font())
        bold.setBold(True)
        muted = QColor(self.ctrl.theme.muted)
        for mi, milestone in enumerate(path["milestones"]):
            d, t = studypath.milestone_progress(milestone)
            top = QTreeWidgetItem([milestone["title"], f"{d}/{t}"])
            top.setFont(0, bold)
            top.setData(0, KIND, "milestone")
            top.setData(0, MI, mi)
            top.setFlags(top.flags() | Qt.ItemIsEditable)
            top.setForeground(1, muted)
            tree.addTopLevelItem(top)
            for si, st in enumerate(milestone["steps"]):
                is_next = nxt is not None and (nxt[0], nxt[1]) == (mi, si)
                n = st["pomodoros"]
                spent = f"{min(st['spent'], n)}/{n}" if st["spent"] and not st["done"] else f"{n}"
                child = QTreeWidgetItem([st["title"], f"{spent} 🍅" if not is_next else f"today  ·  {spent} 🍅"])
                child.setData(0, KIND, "step")
                child.setData(0, MI, mi)
                child.setData(0, SI, si)
                child.setFlags(child.flags() | Qt.ItemIsEditable | Qt.ItemIsUserCheckable)
                child.setCheckState(0, Qt.Checked if st["done"] else Qt.Unchecked)
                if st["done"]:
                    font = QFont(tree.font())
                    font.setStrikeOut(True)
                    child.setFont(0, font)
                    child.setForeground(0, muted)
                if is_next:
                    child.setFont(0, bold)
                child.setForeground(1, muted)
                top.addChild(child)
            top.setExpanded(d < t or not t)
        tree.resizeColumnToContents(1)
        tree.header().setStretchLastSection(False)
        tree.header().setSectionResizeMode(0, tree.header().ResizeMode.Stretch)
        tree.blockSignals(False)

    # ------------------------------------------------------------- changes
    def _path(self) -> dict:
        return self.ctrl.settings.study_path

    def _changed(self, item: QTreeWidgetItem, column: int) -> None:
        path = self._path()
        mi = item.data(0, MI)
        if item.data(0, KIND) == "milestone":
            title = item.text(0).strip()[:80]
            if title:
                path["milestones"][mi]["title"] = title
            self.ctrl.path_changed()
            return
        si = item.data(0, SI)
        step = path["milestones"][mi]["steps"][si]
        done = item.checkState(0) == Qt.Checked
        if done != step["done"]:
            self.ctrl.complete_step(mi, si, done)
            return
        title = item.text(0).strip()[:120]
        if title and title != step["title"]:
            step["title"] = title
        self.ctrl.path_changed()

    def _menu(self, pos) -> None:
        from .app import styled_menu

        item = self.tree.itemAt(pos)
        menu = styled_menu(QMenu(self))
        path = self._path()
        if item is None:
            menu.addAction("Add a milestone", self._add_milestone)
        elif item.data(0, KIND) == "milestone":
            mi = item.data(0, MI)
            menu.addAction("Add a step", lambda: self._add_step(mi, len(path["milestones"][mi]["steps"])))
            menu.addAction("Rename", lambda: self.tree.editItem(item, 0))
            menu.addSeparator()
            menu.addAction("Add a milestone", self._add_milestone)
            menu.addAction("Delete this milestone", lambda: self._delete(mi, None))
        else:
            mi, si = item.data(0, MI), item.data(0, SI)
            step = path["milestones"][mi]["steps"][si]
            if not step["done"]:
                menu.addAction(f"Start with just {self.ctrl.settings.starter_min} minutes",
                               lambda: self.ctrl.start_step(mi, si, small=True))
                menu.addAction("Start a full session", lambda: self.ctrl.start_step(mi, si, small=False))
                menu.addSeparator()
            sizes = menu.addMenu(f"Pomodoros it takes: {step['pomodoros']}")
            for n in range(1, studypath.MAX_POMODOROS + 1):
                act = sizes.addAction(str(n), lambda n=n: self._set_size(mi, si, n))
                act.setCheckable(True)
                act.setChecked(n == step["pomodoros"])
            menu.addAction("Rename", lambda: self.tree.editItem(item, 0))
            menu.addAction("Add a step after this", lambda: self._add_step(mi, si + 1))
            menu.addAction("Delete this step", lambda: self._delete(mi, si))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _set_size(self, mi: int, si: int, n: int) -> None:
        self._path()["milestones"][mi]["steps"][si]["pomodoros"] = n
        self.ctrl.path_changed()

    def _add_step(self, mi: int, at: int) -> None:
        title, ok = QInputDialog.getText(self, "Add a step", "A concrete step, small enough for 1 to 3 pomodoros:")
        if ok and title.strip():
            steps = self._path()["milestones"][mi]["steps"]
            steps.insert(at, {"title": title.strip()[:120], "pomodoros": 1, "spent": 0, "done": False, "done_on": ""})
            self.ctrl.path_changed()

    def _add_milestone(self) -> None:
        title, ok = QInputDialog.getText(self, "Add a milestone", "What's the milestone?")
        if ok and title.strip():
            self._path()["milestones"].append({"title": title.strip()[:80], "steps": []})
            self.ctrl.path_changed()

    def _delete(self, mi: int, si: int | None) -> None:
        milestones = self._path()["milestones"]
        if si is None:
            milestones.pop(mi)
        else:
            milestones[mi]["steps"].pop(si)
        self.ctrl.path_changed()

    def _new_path(self) -> None:
        goal, ok = QInputDialog.getText(self, "New path", "What are you working toward? (for example: "
                                                          "get a junior data analyst job)")
        if not ok or not goal.strip():
            return
        why, _ok = QInputDialog.getText(self, "New path", "Why does it matter to you? (optional)")
        weeks, ok = QInputDialog.getInt(self, "New path", "Try it for how many weeks before deciding again?",
                                        studypath.DEFAULT_WEEKS, 1, 52)
        if not ok:
            return
        path = studypath.make(goal, why, weeks, [{"title": "First steps", "steps": []}], date.today())
        self.ctrl.path_changed(path)

    def _ask(self) -> None:
        from .ai import PATH_CHECK_PROMPT, PATH_START_PROMPT

        self.ctrl.ask_pomo(PATH_CHECK_PROMPT if self._path() else PATH_START_PROMPT)

    def _start_today(self, small: bool) -> None:
        nxt = studypath.next_step(self._path())
        if nxt is not None:
            self.ctrl.start_step(nxt[0], nxt[1], small=small)
